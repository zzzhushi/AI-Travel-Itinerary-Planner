"""Business logic for trip operations.

These functions are the shared core between the CLI and web routes:
- They take a SQLAlchemy Session and a Trip ORM object.
- They are async so web routes can await them directly.
- They return data (no I/O); presentation is the caller's responsibility.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.agents.orchestrator import generate_schedule, research_batch
from src.agents.planner import DayPlan
from src.db.models import Trip
from src.db.queries import (
    get_rated_options_for_schedule,
    get_unresearched_activities,
    mark_researched,
    save_options,
    upsert_schedule,
)


async def research_activities(session: Session, trip: Trip) -> list[dict]:
    """Research all unresearched activities for a trip, persist results.

    Returns one summary dict per activity:
        {"query": str, "options_saved": int, "error": str}
    Returns [] if all activities are already researched.
    """
    activities = get_unresearched_activities(session, trip.id)
    if not activities:
        return []

    batch_input = [
        {"query": act.query, "is_specific": act.is_specific}
        for act in activities
    ]

    results = await research_batch(trip.id, trip.destination, batch_input)

    summaries = []
    for act, (options, err) in zip(activities, results):
        if err:
            summaries.append({"query": act.query, "options_saved": 0, "error": err})
            continue
        save_options(session, act.id, options)
        mark_researched(session, act.id)
        summaries.append({"query": act.query, "options_saved": len(options), "error": ""})

    return summaries


async def generate_and_save_schedule(
    session: Session,
    trip: Trip,
    num_days: int,
    use_llm_refinement: bool = True,
) -> tuple[list[DayPlan], list[dict], str]:
    """Generate a schedule from rated options and persist it.

    Returns (day_plans, llm_days, warning).
    day_plans is always populated; llm_days is empty if refinement was skipped.
    """
    options = get_rated_options_for_schedule(session, trip.id)
    if not options:
        return [], [], ""

    day_plans, llm_days, warn = await generate_schedule(
        destination=trip.destination,
        options=options,
        num_days=num_days,
        use_llm_refinement=use_llm_refinement,
        min_rating=1,
    )

    upsert_schedule(session, trip.id, day_plans)
    return day_plans, llm_days, warn
