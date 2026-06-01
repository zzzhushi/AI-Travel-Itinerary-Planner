"""
Researcher agent: given a destination + activity query, returns 4-5 real options.

Idempotency: caller should check Option.research_hash before calling.
Rate limiting: tenacity exponential backoff on 429/5xx.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import google_search

from src.agents.base import LlmAgent, _extract_json

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
"""


def _build_prompt(destination: str, query: str, is_specific: bool) -> str:
    specificity = "The user already has a specific place in mind." if is_specific else "This is a general activity — find 4-5 good options."
    return (
        f"Destination: {destination}\n"
        f"Activity query: {query}\n"
        f"{specificity}\n\n"
        f"Return the JSON array of options."
    )


def _normalize(raw: list[Any], research_hash: str) -> list[dict]:
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "name": str(item.get("name", "")).strip(),
                "address": str(item.get("address", "")).strip(),
                "location": str(item.get("location", "")).strip(),
                "maps_search": str(item.get("maps_search", "")).strip(),
                "category": str(item.get("category", "other")).strip().lower(),
                "why": str(item.get("why", "")).strip(),
                "research_hash": research_hash,
            }
        )
    return [o for o in out if o["name"]]


class ResearcherAgent(LlmAgent):
    def __init__(self) -> None:
        super().__init__(
            name="ResearcherAgent",
            instruction=RESEARCHER_INSTRUCTION,
            tools=[google_search],
            retry_attempts=5,
            retry_exp_base=7,
        )

    async def research(
        self,
        destination: str,
        query: str,
        is_specific: bool = False,
        research_hash: str = "",
    ) -> tuple[list[dict], str]:
        prompt = _build_prompt(destination, query, is_specific)
        text, err = await self.ask(prompt)
        if err:
            return [], err
        raw = _extract_json(text)
        if raw is None:
            return [], f"Could not parse JSON from response:\n{text[:300]}"
        options = _normalize(raw, research_hash)
        return (options, "") if options else ([], "Agent returned no valid options.")


async def research_activity(
    destination: str,
    query: str,
    is_specific: bool = False,
    research_hash: str = "",
) -> tuple[list[dict], str]:
    """Research an activity query for a destination.

    Returns (options, error_message). error_message is empty on success.
    Each option dict has: name, address, location, maps_search, category, why, research_hash.
    """
    return await ResearcherAgent().research(destination, query, is_specific, research_hash)
