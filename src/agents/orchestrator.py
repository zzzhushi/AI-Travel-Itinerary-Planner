"""
Orchestrator: coordinates Researcher and Planner agents for a trip.

For the CLI this is a thin coordinator. In the web app it will handle
background tasks and state transitions.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from src.agents.researcher import ResearcherAgent, RESEARCHER_INSTRUCTION
from src.agents.planner import PlannerAgent, DayPlan, PLANNER_INSTRUCTION, build_schedule


def _make_hash(trip_id: int, query: str) -> str:
    key = f"{trip_id}:{query.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# Lazy singletons — constructed on first call so env vars are loaded before init.
_researcher: ResearcherAgent | None = None
_planner: PlannerAgent | None = None


def _get_researcher() -> ResearcherAgent:
    global _researcher
    if _researcher is None:
        from google.adk.tools import google_search
        from src.agents.providers import GeminiProvider

        _researcher = ResearcherAgent(
            GeminiProvider(
                agent_name="ResearcherAgent",
                instruction=RESEARCHER_INSTRUCTION,
                tools=[google_search],
                retry_attempts=5,
                retry_exp_base=7,
            )
        )
    return _researcher


def _get_planner() -> PlannerAgent:
    global _planner
    if _planner is None:
        from src.agents.providers import GeminiProvider

        _planner = PlannerAgent(
            GeminiProvider(
                agent_name="PlannerAgent",
                instruction=PLANNER_INSTRUCTION,
                tools=[],
                retry_attempts=3,
                retry_exp_base=5,
            )
        )
    return _planner


async def research(
    trip_id: int,
    destination: str,
    query: str,
    is_specific: bool = False,
    existing_hash: Optional[str] = None,
) -> tuple[list[dict], str]:
    """
    Research a single activity for a destination.

    Returns (options, error). If existing_hash matches the computed hash,
    returns ([], "") to signal "already researched — use cached results" (idempotency).
    """
    research_hash = _make_hash(trip_id, query)

    if existing_hash and existing_hash == research_hash:
        return [], ""  # Caller should use cached DB results

    return await _get_researcher().research(
        destination=destination,
        query=query,
        is_specific=is_specific,
        research_hash=research_hash,
    )


async def research_batch(
    trip_id: int,
    destination: str,
    activities: list[dict],
    batch_size: int = 10,
) -> list[tuple[list[dict], str]]:
    """Research a batch of activities. Processes activities in groups of batch_size.

    Each activity dict needs: query. Optional: is_specific, existing_hash.
    Returns a list of (options, error) in the same order as the input.
    """
    if not activities:
        return []

    to_research: list[tuple[int, dict]] = []
    results: list[tuple[list[dict], str]] = [([], "")] * len(activities)

    for i, act in enumerate(activities):
        h = _make_hash(trip_id, act["query"])
        if act.get("existing_hash") == h:
            continue
        to_research.append((i, {
            "query": act["query"],
            "is_specific": act.get("is_specific", False),
            "research_hash": h,
        }))

    researcher = _get_researcher()
    for start in range(0, len(to_research), batch_size):
        chunk = to_research[start:start + batch_size]
        batch_results = await researcher.research_batch(destination, [a for _, a in chunk])
        for (orig_idx, _), (options, err) in zip(chunk, batch_results):
            results[orig_idx] = ([], err) if err else (options, "")

    return results


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
        llm_days, err = await _get_planner().refine(day_plans, destination)
        if err:
            # Non-fatal: fall back to deterministic schedule
            return day_plans, [], f"LLM refinement skipped: {err}"

    return day_plans, llm_days, ""
