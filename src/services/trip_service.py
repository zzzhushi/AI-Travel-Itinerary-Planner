"""Business logic for trip operations.

These functions are the shared core between the CLI and web routes:
- They take a SQLAlchemy Session and a Trip ORM object.
- They are async so web routes can await them directly.
- They return data (no I/O); presentation is the caller's responsibility.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

import obslog
from src.services.orchestrator import generate_schedule, research_batch
from src.services.travel import cluster_and_route
from src.workers.planner import PlanResult
from src.db.models import Trip
from src.db.queries import (
    get_all_options_for_trip,
    get_rated_options_for_schedule,
    get_unenriched_options,
    get_unresearched_activities,
    mark_researched,
    save_options,
    upsert_schedule,
)
from src.clients.places_client import PlacesClient

logger = logging.getLogger(__name__)


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

    with obslog.bind_labels(trip_id=trip.id):
        results = await research_batch(trip.destination, batch_input)

    summaries = []
    for act, (options, err) in zip(activities, results):
        if err:
            summaries.append({"query": act.query, "options_saved": 0, "error": err})
            continue
        save_options(session, act.id, options)
        mark_researched(session, act.id)
        summaries.append({"query": act.query, "options_saved": len(options), "error": ""})

    return summaries


async def research_and_enrich(
    session: Session,
    trip: Trip,
    places_client: Optional[PlacesClient] = None,
) -> tuple[list[dict], dict]:
    """Research unresearched activities, then best-effort enrich the new options.

    `research_activities` commits its results before enrichment runs, so a
    Places failure never discards the research output. When `places_client`
    is None, enrichment is skipped.

    Returns (research_summaries, enrichment_stats).
    """
    summaries = await research_activities(session, trip)
    stats = {"enriched": 0, "skipped": 0, "failed": 0}
    if places_client is not None:
        try:
            stats = await enrich_options_with_places(session, trip, places_client)
        except Exception:
            logger.warning("Places enrichment failed for trip %s", trip.id, exc_info=True)
    return summaries, stats


async def enrich_options_with_places(
    session: Session,
    trip: Trip,
    places_client: Optional[PlacesClient] = None,
    force: bool = False,
) -> dict:
    """Enrich Options for a trip with data from the Places API.

    By default picks up only options needing enrichment (never enriched, stale,
    or failed past the retry window — see get_unenriched_options). With
    force=True, re-enriches every option for the trip, ignoring the staleness
    gate — used to backfill newly added Places fields (e.g. neighborhood) onto
    options enriched before the field existed.

    Sets place_refreshed_at on every processed option (even on lookup failure)
    so non-forced re-runs skip already-attempted options.

    Returns {"enriched": int, "skipped": int, "failed": int}.
    """
    if places_client is None:
        return {"enriched": 0, "skipped": 0, "failed": 0}

    options = (
        get_all_options_for_trip(session, trip.id)
        if force
        else get_unenriched_options(session, trip.id)
    )
    if not options:
        return {"enriched": 0, "skipped": 0, "failed": 0}

    # PlacesClient.lookup is a synchronous blocking HTTP call. run_in_executor moves each
    # call to a thread pool so they don't block the event loop, and asyncio.gather fires
    # all of them concurrently instead of waiting for each one serially.
    loop = asyncio.get_event_loop()
    lookups = await asyncio.gather(
        *[
            loop.run_in_executor(None, places_client.lookup, opt.maps_search or opt.name)
            for opt in options
        ]
    )

    enriched = skipped = failed = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for opt, result in zip(options, lookups):
        opt.place_refreshed_at = now
        if result is None:
            failed += 1
            logger.debug("Places lookup returned no result for option %d (%r)", opt.id, opt.maps_search)
        else:
            opt.place_id = result.get("place_id")
            opt.latitude = result.get("latitude")
            opt.longitude = result.get("longitude")
            opt.maps_link = result.get("maps_link")
            opt.neighborhood = result.get("neighborhood")
            if result.get("formatted_address"):
                opt.address = result["formatted_address"]
            opt.google_rating = result.get("google_rating")
            opt.price_level = result.get("price_level")
            opt.phone_number = result.get("phone_number")
            opt.website = result.get("website")
            opt.opening_hours = result.get("opening_hours")
            if result.get("place_id"):
                enriched += 1
            else:
                skipped += 1

    session.commit()
    return {"enriched": enriched, "skipped": skipped, "failed": failed}


async def generate_and_save_schedule(
    session: Session,
    trip: Trip,
    num_days: int,
    use_llm_refinement: bool = True,
) -> PlanResult:
    """Generate a schedule from rated options and persist it.

    Returns a PlanResult with day_plans, source ("llm" | "deterministic"),
    and any warning. Returns PlanResult([], "deterministic") when no options exist.
    """
    options = get_rated_options_for_schedule(session, trip.id)
    if not options:
        return PlanResult(day_plans=[], source="deterministic")

    # Geographic clustering + inter-cluster travel matrix feed the LLM planner.
    # cluster_and_route stamps cluster_id/cluster_name onto the option dicts in
    # place (so they reach the prompt) and returns the K×K matrix. With no
    # routes client it falls back to haversine estimates — no API billing.
    # Skipped for the deterministic-only path, which ignores both signals.
    travel_matrix = None
    if use_llm_refinement:
        try:
            travel_matrix = cluster_and_route(session, options)
        except Exception:
            logger.warning("Clustering/travel-matrix failed for trip %s", trip.id, exc_info=True)

    with obslog.bind_labels(trip_id=trip.id):
        result = await generate_schedule(
            destination=trip.destination,
            options=options,
            num_days=num_days,
            use_llm_refinement=use_llm_refinement,
            min_rating=1,
            start_date=trip.start_date,
            travel_matrix=travel_matrix,
        )

    upsert_schedule(session, trip.id, result.day_plans)
    return result


_CONVERTIBLE_FIELDS = frozenset(("name", "destination", "num_days", "start_date", "end_date"))


def update_trip_fields(session: Session, trip: Trip, raw_fields: dict) -> None:
    """Convert and apply a subset of raw form values to a Trip, then commit.

    Only fields present in raw_fields and in _CONVERTIBLE_FIELDS are touched.
    Raises ValueError if a date field contains a non-empty, non-ISO-8601 string.
    """
    for field, raw in raw_fields.items():
        if field not in _CONVERTIBLE_FIELDS:
            continue
        if field == "num_days":
            stripped = str(raw).strip()
            value = int(stripped) if stripped.isdigit() else None
        elif field in ("start_date", "end_date"):
            stripped = str(raw).strip()
            if not stripped:
                value = None
            else:
                value = date.fromisoformat(stripped)  # raises ValueError on bad input
        else:
            value = str(raw).strip()
        setattr(trip, field, value)
    session.commit()
