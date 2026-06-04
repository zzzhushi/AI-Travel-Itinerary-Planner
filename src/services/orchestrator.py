"""
Orchestrator: coordinates Researcher and Planner agents for a trip.

Implements the Strategy pattern for scheduling:
- LlmPlanner is tried first when use_llm_refinement=True.
- DeterministicPlanner is used as fallback (or exclusively when AI is off).

Both planners return PlanResult; the coordinator owns the fallback chain.
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Optional

from src.workers.researcher import ResearcherAgent, RESEARCHER_INSTRUCTION
from src.workers.planner import (
    DeterministicPlanner,
    LlmPlanner,
    PlanResult,
    PLANNER_INSTRUCTION,
)

# Thread-local agent instances.
#
# The ADK InMemoryRunner binds its async resources to whichever event loop is
# running when it is first awaited. In the web app each background thread calls
# asyncio.run(), which creates and then closes its own event loop. Using a single
# global singleton means the second thread's asyncio.run() gets a runner whose
# resources belong to the first (now-closed) loop → RuntimeError("Event loop is
# closed"). Thread-local storage gives each thread its own instance, naturally
# scoped to that thread's loop and garbage-collected when the thread exits.
_local = threading.local()

# DeterministicPlanner is stateless — a module-level singleton is safe and avoids
# repeated construction without needing thread-local storage.
_deterministic_planner = DeterministicPlanner()


def _get_researcher() -> ResearcherAgent:
    if not getattr(_local, "researcher", None):
        from google.adk.tools import google_search
        from src.agents.providers import GeminiProvider

        _local.researcher = ResearcherAgent(
            GeminiProvider(
                agent_name="ResearcherAgent",
                instruction=RESEARCHER_INSTRUCTION,
                tools=[google_search],
                retry_attempts=5,
                retry_exp_base=7,
            )
        )
    return _local.researcher


def _get_llm_planner() -> LlmPlanner:
    if not getattr(_local, "llm_planner", None):
        from src.agents.providers import GeminiProvider

        _local.llm_planner = LlmPlanner(
            GeminiProvider(
                agent_name="PlannerAgent",
                instruction=PLANNER_INSTRUCTION,
                tools=[],
                retry_attempts=3,
                retry_exp_base=5,
            )
        )
    return _local.llm_planner


async def research(
    destination: str,
    query: str,
    is_specific: bool = False,
) -> tuple[list[dict], str]:
    """Research a single activity for a destination. Returns (options, error)."""
    return await _get_researcher().research(
        destination=destination,
        query=query,
        is_specific=is_specific,
    )


async def research_batch(
    destination: str,
    activities: list[dict],
    batch_size: int = 10,
) -> list[tuple[list[dict], str]]:
    """Research a batch of activities. Processes activities in groups of batch_size.

    Each activity dict needs: query. Optional: is_specific.
    Returns a list of (options, error) in the same order as the input.
    """
    if not activities:
        return []

    results: list[tuple[list[dict], str]] = []
    researcher = _get_researcher()
    for start in range(0, len(activities), batch_size):
        chunk = activities[start:start + batch_size]
        batch_results = await researcher.research_batch(destination, chunk)
        results.extend(batch_results)

    return results


async def generate_schedule(
    destination: str,
    options: list[dict],
    num_days: int,
    use_llm_refinement: bool = True,
    min_rating: int = 3,
    start_date: Optional[date] = None,
    travel_matrix: Optional[dict[str, dict[tuple[int, int], dict]]] = None,
) -> PlanResult:
    """Generate a day-by-day schedule from rated options.

    Tries the LlmPlanner first when use_llm_refinement=True. Falls back to
    DeterministicPlanner if the LLM fails or produces an unusable schedule.
    Always returns a bounded, valid schedule.

    `travel_matrix` is the inter-cluster travel matrix
    (`services.travel.cluster_and_route`); it is passed to the LlmPlanner so the
    model can sequence clusters by real travel time. The DeterministicPlanner
    ignores it.
    """
    if use_llm_refinement:
        result = await _get_llm_planner().plan(
            options, num_days,
            destination=destination,
            start_date=start_date,
            min_rating=min_rating,
            travel_matrix=travel_matrix,
        )
        if result.day_plans:
            return result
        # LLM failed or unusable — fall back to deterministic, carry the warning.
        llm_warning = result.warning
        fallback = await _deterministic_planner.plan(options, num_days, min_rating=min_rating)
        fallback.warning = llm_warning or "LLM produced an unusable schedule; using deterministic fallback."
        return fallback

    return await _deterministic_planner.plan(options, num_days, min_rating=min_rating)
