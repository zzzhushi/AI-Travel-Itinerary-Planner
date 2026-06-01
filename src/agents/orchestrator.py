"""
Orchestrator: coordinates Researcher and Planner agents for a trip.

For the CLI this is a thin coordinator. In the web app it will handle
background tasks and state transitions.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Optional

from src.agents.researcher import research_activity
from src.agents.planner import build_schedule, refine_schedule_with_llm, DayPlan
from src.tools.maps import get_place_info, maps_search_link


def _make_hash(trip_id: int, query: str) -> str:
    key = f"{trip_id}:{query.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


async def research_and_enrich(
    trip_id: int,
    destination: str,
    query: str,
    is_specific: bool = False,
    existing_hash: Optional[str] = None,
) -> tuple[list[dict], str]:
    """
    Research an activity and enrich results with Google Maps data.

    Returns (options, error). Each option has all fields from researcher
    plus: maps_link, latitude, longitude (from Maps API if available).

    If existing_hash matches the computed hash, returns ([], "") to signal
    "already researched — use cached results" (idempotency).
    """
    research_hash = _make_hash(trip_id, query)

    if existing_hash and existing_hash == research_hash:
        return [], ""  # Caller should use cached DB results

    options, err = await research_activity(
        destination=destination,
        query=query,
        is_specific=is_specific,
        research_hash=research_hash,
    )
    if err:
        return [], err

    # Enrich with Maps data
    enriched = []
    for opt in options:
        maps_query = opt.get("maps_search") or f"{opt['name']} {destination}"
        place = get_place_info(maps_query)
        if place:
            opt["maps_link"] = place.maps_link
            opt["latitude"] = place.latitude
            opt["longitude"] = place.longitude
            opt["address"] = opt["address"] or place.address
        else:
            opt["maps_link"] = maps_search_link(maps_query)
            opt["latitude"] = None
            opt["longitude"] = None
        enriched.append(opt)

    return enriched, ""


async def generate_schedule(
    destination: str,
    options: list[dict],
    num_days: int,
    use_llm_refinement: bool = True,
    min_rating: int = 3,
) -> tuple[list[DayPlan], list[dict], str]:
    """
    Generate a day-by-day schedule from rated options.

    Returns (day_plans, llm_days, error).
    day_plans: deterministic Python schedule (always available)
    llm_days: LLM-refined version (may be empty if refinement fails or is skipped)
    """
    day_plans = build_schedule(options, num_days=num_days, min_rating=min_rating)

    llm_days: list[dict] = []
    if use_llm_refinement:
        llm_days, err = await refine_schedule_with_llm(day_plans, destination)
        if err:
            # Non-fatal: fall back to deterministic schedule
            return day_plans, [], f"LLM refinement skipped: {err}"

    return day_plans, llm_days, ""
