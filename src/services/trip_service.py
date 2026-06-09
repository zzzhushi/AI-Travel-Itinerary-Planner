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

from src.services.orchestrator import generate_schedule, research_batch
from src.services.travel import cluster_and_route
from src.workers.planner import PlanResult
from src.workers.preferences import Preferences
from src.db.models import Trip, TripPreferences
from src.db.queries import (
    get_all_options_for_trip,
    get_logistics,
    get_rated_options_for_schedule,
    get_unenriched_logistics,
    get_unenriched_options,
    get_unresearched_activities,
    mark_researched,
    save_options,
    update_preferences,
    upsert_schedule,
)
from src.clients.places_client import PlacesClient
from src.workers.logistics import build_day_window_fn, travel_option_dicts

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

    preferences = Preferences.from_orm(trip.preferences)
    results = await research_batch(trip.destination, batch_input, preferences=preferences)

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


def _logistics_search(item, destination: Optional[str]) -> str:
    """Build a Places search string for a logistics row from its label/address."""
    base = (item.label or item.address or "").strip()
    return f"{base} {destination}".strip() if destination else base


async def geocode_logistics(
    session: Session,
    trip: Trip,
    places_client: Optional[PlacesClient] = None,
) -> dict:
    """Best-effort geocode of logistics rows lacking coordinates.

    Mirrors enrich_options_with_places: one concurrent Places lookup per
    unenriched row, setting place_refreshed_at even on failure so we never
    re-hit Places on every save (users rarely edit logistics). A row keeps
    working without coordinates — it just won't anchor clustering.

    Returns {"geocoded": int, "failed": int}.
    """
    if places_client is None:
        return {"geocoded": 0, "failed": 0}
    rows = get_unenriched_logistics(session, trip.id)
    if not rows:
        return {"geocoded": 0, "failed": 0}

    loop = asyncio.get_event_loop()
    lookups = await asyncio.gather(
        *[
            loop.run_in_executor(None, places_client.lookup, _logistics_search(item, trip.destination))
            for item in rows
        ]
    )

    geocoded = failed = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for item, result in zip(rows, lookups):
        item.place_refreshed_at = now
        if result and result.get("place_id"):
            item.place_id = result.get("place_id")
            item.latitude = result.get("latitude")
            item.longitude = result.get("longitude")
            item.maps_link = result.get("maps_link")
            if not item.address and result.get("formatted_address"):
                item.address = result["formatted_address"]
            geocoded += 1
        else:
            failed += 1
            logger.debug("Places geocode found nothing for logistics %d (%r)", item.id, item.label)

    session.commit()
    return {"geocoded": geocoded, "failed": failed}


def _travel_dicts(logistics: list) -> list[dict]:
    """Convert arrival/departure Logistics rows to the plain dicts the planner
    projections (build_day_window_fn / travel_option_dicts) consume.

    Keeps the workers layer decoupled from the ORM (it sees dicts, not models).
    """
    return [
        {
            "id": l.id, "kind": l.kind, "day_number": l.day_number,
            "time_minutes": l.time_minutes, "transit_minutes": l.transit_minutes,
            "label": l.label, "mode": l.mode,
            "latitude": l.latitude, "longitude": l.longitude,
        }
        for l in logistics
        if l.kind in ("arrival", "departure")
    ]


def _lodging_dicts(logistics: list) -> list[dict]:
    """Convert lodging Logistics rows to plain dicts for cluster_and_route.

    Synthetic negative option_ids in a distinct range from travel anchors so they
    never collide with real Option ids or each other. These join clustering only
    (to land in a neighborhood cluster + force its anchor); they are never
    scheduled as stops.
    """
    return [
        {
            "id": l.id,
            "option_id": -2_000_000 - l.id,
            "name": l.label or "Hotel",
            "label": l.label or "Hotel",
            "latitude": l.latitude, "longitude": l.longitude,
            "place_id": l.place_id,
            "check_in_day": l.check_in_day, "check_out_day": l.check_out_day,
        }
        for l in logistics
        if l.kind == "lodging"
    ]


def _home_base_by_day(lodging_dicts: list[dict], num_days: int) -> dict[int, dict]:
    """Map each day to its home-base hotel {label, cluster_id} (latest check-in wins).

    Reads cluster_id stamped onto the lodging dicts by cluster_and_route.
    """
    by_day: dict[int, dict] = {}
    for d in range(1, num_days + 1):
        best = None
        for h in lodging_dicts:
            ci, co = h.get("check_in_day"), h.get("check_out_day")
            if ci is None or co is None:
                continue
            if ci <= d <= co and (best is None or ci > best["check_in_day"]):
                best = h
        if best is not None:
            by_day[d] = {"label": best["label"], "cluster_id": best.get("cluster_id")}
    return by_day


def hotel_for_day(lodgings: list, day: int):
    """Return the lodging that is the home base on `day` (1-based), or None.

    Convention (owned here so storage stays simple): check_in_day..check_out_day
    are inclusive day numbers the hotel is the base. On overlap the latest
    check_in wins; rows with an incomplete range are ignored; gaps return None.
    """
    best = None
    for h in lodgings:
        if h.check_in_day is None or h.check_out_day is None:
            continue
        if h.check_in_day <= day <= h.check_out_day:
            if best is None or h.check_in_day > best.check_in_day:
                best = h
    return best


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

    prefs = Preferences.from_orm(trip.preferences)
    logistics = get_logistics(session, trip.id)

    # Flights/travel: arrival/departure rows become a per-day window (compressing
    # the first/last day) and locked "transport" anchors pinned on the timeline.
    travel = _travel_dicts(logistics)
    window_fn = build_day_window_fn(
        travel, base_start=prefs.day_start_minutes, base_end=prefs.day_end_minutes
    )

    # Lodging: each day's hotel is its geographic home base. The dicts join
    # clustering (cluster_and_route stamps cluster_id on them and forces their
    # cluster's anchor to the hotel) so travel originates from where the day
    # starts/ends; home_base_by_day surfaces that to the LLM prompt.
    lodging = _lodging_dicts(logistics)

    # Geographic clustering + inter-cluster travel matrix feed the LLM planner.
    # cluster_and_route stamps cluster_id/cluster_name onto the option dicts in
    # place (so they reach the prompt) and returns the K×K matrix. With no
    # routes client it falls back to haversine estimates — no API billing.
    # Clustering runs on real options + hotels only; travel anchors are added
    # after so the matrix stays city-focused (an airport would distort legs).
    travel_matrix = None
    home_base_by_day: dict[int, dict] = {}
    if use_llm_refinement:
        try:
            travel_matrix = cluster_and_route(session, options, hotels=lodging)
            home_base_by_day = _home_base_by_day(lodging, num_days)
        except Exception:
            logger.warning("Clustering/travel-matrix failed for trip %s", trip.id, exc_info=True)

    # Inject locked travel anchors into the pool the planners schedule around.
    options = options + travel_option_dicts(travel)

    result = await generate_schedule(
        destination=trip.destination,
        options=options,
        num_days=num_days,
        use_llm_refinement=use_llm_refinement,
        min_rating=1,
        start_date=trip.start_date,
        travel_matrix=travel_matrix,
        preferences=prefs,
        window_fn=window_fn,
        home_base_by_day=home_base_by_day,
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


def update_trip_preferences(
    session: Session, trip: Trip, field: str, raw: str
) -> TripPreferences:
    """Validate and persist a single preference field for a trip.

    Thin wrapper over the query helper so routes stay free of business logic.
    """
    return update_preferences(session, trip.id, field, raw)
