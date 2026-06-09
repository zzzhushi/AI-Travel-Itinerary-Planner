"""
Researcher agent: given a destination + activity query, returns 4-5 real options.

Rate limiting: tenacity exponential backoff on 429/5xx.
"""

from __future__ import annotations

from typing import Any, Optional

from src.agents.base import LlmAgent, _extract_json, _extract_json_dict
from src.agents.providers import LLMProvider
from src.workers.preferences import Preferences

_BUDGET_HINT = {
    "budget": "Favor affordable, good-value options; avoid high-end picks.",
    "mid_range": "Favor solid mid-range options; neither bargain nor luxury.",
    "splurge": "Favor premium, high-end, or special-occasion options.",
}

RESEARCHER_INSTRUCTION = """You are a travel research assistant helping plan a trip itinerary.

Given a destination and an activity query, find 4-5 real, specific options the traveler could visit.
Use google_search to find current, accurate results.

Rules:
- Return ONLY a JSON array. No markdown, no code fences, no extra text.
- Each element must have these exact keys:
  - "name": string — place or experience name
  - "address": string — street address or area (best effort)
  - "location": string — neighborhood or district
  - "maps_search": string — the best search string to use on Google Maps (e.g. "Ichiran Ramen Shinjuku Tokyo")
  - "category": string — one of: food, nightlife, sightseeing, shopping, nature, culture, transport, accommodation, other
  - "why": string — one sentence on why this is a good option

- For specific queries (user already named a place), return just that 1 option enriched with details.
- For vague queries, return 4-5 diverse, well-regarded options.
- Prefer places with strong reputations or unique appeal over generic tourist traps.
- Use google_search at most twice per activity to stay within rate limits.
"""


def _preferences_block(preferences: Optional[Preferences]) -> str:
    """Render a soft-bias preferences block for the research prompt, or "".

    These are *biases*, not hard filters — the researcher should still return the
    best options for the query, leaning toward the traveler's style where it can.
    """
    if preferences is None or not preferences.has_research_bias():
        return ""
    lines = ["\nTraveler preferences (soft bias — lean toward these, do not exclude good options):"]
    if preferences.budget:
        lines.append(f"- Budget: {_BUDGET_HINT.get(preferences.budget, preferences.budget)}")
    if preferences.interests:
        lines.append(f"- Interests: {', '.join(preferences.interests)}")
    if preferences.notes:
        lines.append(f"- Notes: {preferences.notes}")
    return "\n".join(lines) + "\n"


def _build_batch_prompt(
    destination: str,
    activities: list[dict],
    preferences: Optional[Preferences] = None,
) -> str:
    lines = [
        f'{i}. "{a["query"]}" [{"specific — 1 option" if a.get("is_specific") else "general — 4-5 options"}]'
        for i, a in enumerate(activities)
    ]
    expected_keys = ", ".join(f'"{i}"' for i in range(len(activities)))
    return (
        f"Destination: {destination}\n"
        f"{_preferences_block(preferences)}\n"
        f"Research each activity below. Use google_search for each one.\n\n"
        f"Activities:\n" + "\n".join(lines) + "\n\n"
        f"Return ONLY a JSON object with exactly {len(activities)} keys: {expected_keys}.\n"
        f"Values are arrays of options with fields: name, address, location, maps_search, category, why.\n"
        f"Do not close the object until ALL {len(activities)} keys are present.\n"
        f"No markdown, no code fences, no extra text."
    )


def _build_prompt(
    destination: str,
    query: str,
    is_specific: bool,
    preferences: Optional[Preferences] = None,
) -> str:
    specificity = "The user already has a specific place in mind." if is_specific else "This is a general activity — find 4-8 good options."
    return (
        f"Destination: {destination}\n"
        f"Activity query: {query}\n"
        f"{specificity}\n"
        f"{_preferences_block(preferences)}\n"
        f"Return the JSON array of options."
    )


def _normalize(raw: list[Any]) -> list[dict]:
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "name": str(item.get("name", "")).strip(),
                "address": str(item.get("address", "")).strip(),
                "location": str(item.get("location", "")).strip(),
                "maps_search": str(item.get("maps_search", "")).strip() or None,
                "category": str(item.get("category", "other")).strip().lower(),
                "why": str(item.get("why", "")).strip() or None,
            }
        )
    return [o for o in out if o["name"]]


class ResearcherAgent(LlmAgent):
    def __init__(self, provider: LLMProvider) -> None:
        super().__init__(provider)

    async def research(
        self,
        destination: str,
        query: str,
        is_specific: bool = False,
        preferences: Optional[Preferences] = None,
    ) -> tuple[list[dict], str]:
        """Research a single activity. Returns (options, error)."""
        prompt = _build_prompt(destination, query, is_specific, preferences)
        text, err = await self.ask(prompt)
        if err:
            return [], err
        raw = _extract_json(text)
        if raw is None:
            return [], f"Could not parse JSON from response:\n{text[:300]}"
        options = _normalize(raw)
        return (options, "") if options else ([], "Agent returned no valid options.")

    async def research_batch(
        self,
        destination: str,
        activities: list[dict],
        preferences: Optional[Preferences] = None,
    ) -> list[tuple[list[dict], str]]:
        """Research up to 5 activities in a single API call.

        Each activity dict needs: query, is_specific (bool).
        Returns a list of (options, error) in the same order as the input.
        """
        if not activities:
            return []
        prompt = _build_batch_prompt(destination, activities, preferences)
        text, err = await self.ask(prompt)
        if err:
            return [([], err)] * len(activities)
        raw = _extract_json_dict(text)
        if raw is None:
            # LLM sometimes returns a bare array instead of {"0": [...]} when
            # there is only one activity in the batch. Treat it as key "0".
            raw_array = _extract_json(text)
            if isinstance(raw_array, list) and raw_array and len(activities) == 1:
                raw = {"0": raw_array}
            else:
                error = f"Could not parse batch JSON:\n{text[:300]}"
                return [([], error)] * len(activities)

        results: list[tuple[list[dict], str] | None] = [None] * len(activities)
        missing: list[int] = []

        for i, act in enumerate(activities):
            options_raw = raw.get(str(i), [])
            if not isinstance(options_raw, list) or not options_raw:
                missing.append(i)
                continue
            options = _normalize(options_raw)
            results[i] = (options, "") if options else ([], f"No valid options for: {act['query']}")

        # Fall back to individual calls for any activities the model dropped.
        for i in missing:
            act = activities[i]
            opts, err = await self.research(
                destination,
                act["query"],
                act.get("is_specific", False),
                preferences,
            )
            results[i] = (opts, err)

        return results  # type: ignore[return-value]
