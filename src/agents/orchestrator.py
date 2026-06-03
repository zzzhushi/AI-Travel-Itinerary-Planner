"""
Orchestrator: coordinates Researcher and Planner agents for a trip.

For the CLI this is a thin coordinator. In the web app it will handle
background tasks and state transitions.
"""

from __future__ import annotations


from src.agents.researcher import ResearcherAgent, RESEARCHER_INSTRUCTION
from src.agents.planner import PlannerAgent, DayPlan, PLANNER_INSTRUCTION, build_schedule, apply_llm_refinement


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
) -> tuple[list[DayPlan], list[dict], str]:
    """
    Generate a day-by-day schedule from rated options.

    Returns (day_plans, llm_days, error).
    day_plans: deterministic Python schedule (always available)
    llm_days: LLM-refined version (may be empty if refinement fails or is skipped)
    """
    day_plans = build_schedule(options, num_days=num_days, min_rating=min_rating)

    llm_days: list[dict] = []
    warn = ""
    if use_llm_refinement:
        llm_days, err = await _get_planner().refine(day_plans, destination)
        if err:
            warn = f"LLM refinement skipped: {err}"
        else:
            refined = apply_llm_refinement(llm_days, day_plans, num_days)
            if refined:
                day_plans = refined
            else:
                warn = "LLM produced an unusable schedule; using deterministic fallback."

    return day_plans, llm_days, warn
