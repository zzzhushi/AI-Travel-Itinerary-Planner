"""
Researcher agent: given a destination + activity query, returns 4-5 real options.

Idempotency: caller should check Option.research_hash before calling.
Rate limiting: tenacity exponential backoff on 429/5xx.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.adk.tools import google_search
from google.genai import types
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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


def _extract_json(text: str) -> list[dict] | None:
    text = text.strip()
    # Strip code fences if present
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    # Try direct parse
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        pass
    # Find first JSON array in text
    i = text.find("[")
    if i == -1:
        return None
    depth = 0
    for j, c in enumerate(text[i:], i):
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[i : j + 1])
                    return data if isinstance(data, list) else None
                except json.JSONDecodeError:
                    return None
    return None


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


def _retry_config():
    return types.HttpRetryOptions(
        attempts=5,
        exp_base=7,
        initial_delay=1,
        http_status_codes=[429, 500, 503, 504],
    )


async def research_activity(
    destination: str,
    query: str,
    is_specific: bool = False,
    research_hash: str = "",
) -> tuple[list[dict], str]:
    """
    Research an activity query for a destination.

    Returns (options, error_message). error_message is empty on success.
    Each option dict has: name, address, location, maps_search, category, why, research_hash.
    """
    if os.getenv("GOOGLE_API_KEY"):
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

    agent = Agent(
        name="ResearcherAgent",
        model=Gemini(model="gemini-2.5-flash", retry_options=_retry_config()),
        instruction=RESEARCHER_INSTRUCTION,
        tools=[google_search],
    )
    runner = InMemoryRunner(agent=agent)
    prompt = _build_prompt(destination, query, is_specific)

    try:
        response = await runner.run_debug(prompt)
    except Exception as e:
        return [], f"Agent error: {e}"

    # Extract text from response
    text = _extract_text(response)
    if not text:
        return [], "No text in agent response."

    raw = _extract_json(text)
    if raw is None:
        return [], f"Could not parse JSON from response:\n{text[:300]}"

    options = _normalize(raw, research_hash)
    if not options:
        return [], "Agent returned no valid options."

    return options, ""


def _extract_text(response: Any) -> str:
    # run_debug returns list[Event] — find the final response event
    if isinstance(response, list):
        for event in reversed(response):
            if hasattr(event, "is_final_response") and event.is_final_response():
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            return part.text
        # fallback: any event with text parts
        for event in reversed(response):
            if hasattr(event, "content") and event.content and hasattr(event.content, "parts"):
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        return part.text
    if isinstance(response, str):
        return response
    if hasattr(response, "content") and response.content:
        c = response.content
        if isinstance(c, str):
            return c
        if hasattr(c, "parts") and c.parts:
            for part in c.parts:
                if hasattr(part, "text") and part.text:
                    return part.text
    return ""
