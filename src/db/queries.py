"""Database query helpers for the CLI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from src.db.models import Activity, Option, RouteCache, ScheduledItem, Trip

# Google ToS allows caching place data for up to 30 days.
PLACES_STALE_DAYS = 30
# Failed lookups (place_id IS NULL but already attempted) are retried after this many days.
PLACES_RETRY_DAYS = 7

# Travel times change slowly; cache inter-cluster legs for up to 30 days.
ROUTES_STALE_DAYS = 30
# A cached "no route found" (NULL duration) is retried after this many days,
# preventing both repeated API calls and infinite retries (mirrors places).
ROUTES_RETRY_DAYS = 7


def get_trips(session: Session) -> list[Trip]:
    return session.query(Trip).order_by(Trip.created_at.desc()).all()


def get_trip(session: Session, trip_id: int) -> Optional[Trip]:
    return session.get(Trip, trip_id)


def create_trip(
    session: Session, name: str, destination: str, num_days: Optional[int]
) -> Trip:
    trip = Trip(name=name, destination=destination, num_days=num_days)
    session.add(trip)
    session.commit()
    session.refresh(trip)
    return trip


def update_trip_num_days(session: Session, trip_id: int, num_days: int) -> None:
    trip = session.get(Trip, trip_id)
    if trip:
        trip.num_days = num_days
        session.commit()


def get_activities(session: Session, trip_id: int) -> list[Activity]:
    return (
        session.query(Activity)
        .filter_by(trip_id=trip_id)
        .order_by(Activity.created_at)
        .all()
    )


def add_activity(
    session: Session,
    trip_id: int,
    query: str,
    category: Optional[str],
    is_specific: bool,
) -> Activity:
    act = Activity(trip_id=trip_id, query=query, category=category, is_specific=is_specific)
    session.add(act)
    session.commit()
    session.refresh(act)
    return act


def get_unresearched_activities(session: Session, trip_id: int) -> list[Activity]:
    return (
        session.query(Activity)
        .filter(Activity.trip_id == trip_id, Activity.researched_at.is_(None))
        .order_by(Activity.created_at)
        .all()
    )


def get_unresearched_count(session: Session, trip_id: int) -> int:
    return (
        session.query(Activity)
        .filter(Activity.trip_id == trip_id, Activity.researched_at.is_(None))
        .count()
    )


def mark_researched(session: Session, activity_id: int) -> None:
    act = session.get(Activity, activity_id)
    if act:
        act.researched_at = datetime.now(UTC)
        session.commit()


def save_options(session: Session, activity_id: int, options: list[dict]) -> list[Option]:
    saved = []
    for o in options:
        opt = Option(
            activity_id=activity_id,
            name=o["name"],
            address=o.get("address"),
            location=o.get("location"),
            maps_link=o.get("maps_link"),
            maps_search=o.get("maps_search"),
            why=o.get("why"),
            category=o.get("category"),
            latitude=o.get("latitude"),
            longitude=o.get("longitude"),
        )
        session.add(opt)
        saved.append(opt)
    session.commit()
    for opt in saved:
        session.refresh(opt)
    return saved


def get_unenriched_options(session: Session, trip_id: int) -> list[Option]:
    """Return options that need Places API enrichment.

    Includes options that:
    - have never been enriched (place_refreshed_at IS NULL), or
    - have stale data (last enriched > PLACES_STALE_DAYS ago), or
    - previously returned no match (place_id IS NULL) and enough time has passed to retry
      (place_refreshed_at < PLACES_RETRY_DAYS ago) — prevents infinite retries.

    All options have a name, which is used as a fallback search string when
    maps_search is NULL (i.e. options researched before that field was added).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_cutoff = now - timedelta(days=PLACES_STALE_DAYS)
    retry_cutoff = now - timedelta(days=PLACES_RETRY_DAYS)
    return (
        session.query(Option)
        .join(Activity)
        .filter(
            Activity.trip_id == trip_id,
            Option.name.isnot(None),  # name is always set; used as fallback search string
            or_(
                Option.place_refreshed_at.is_(None),
                Option.place_refreshed_at < stale_cutoff,
                and_(Option.place_id.is_(None), Option.place_refreshed_at < retry_cutoff),
            ),
        )
        .order_by(Option.id)
        .all()
    )


def get_all_options_for_trip(session: Session, trip_id: int) -> list[Option]:
    """Return every option for a trip (flat list), ignoring enrichment state.

    Used by force re-enrichment to backfill newly added Places fields (e.g.
    neighborhood) onto options that were enriched before the field existed.
    """
    return (
        session.query(Option)
        .join(Activity)
        .filter(Activity.trip_id == trip_id, Option.name.isnot(None))
        .order_by(Option.id)
        .all()
    )


def get_options_for_trip(
    session: Session, trip_id: int
) -> list[tuple[Activity, list[Option]]]:
    """Return (activity, options) pairs for activities that have at least one option."""
    activities = get_activities(session, trip_id)
    result = []
    for act in activities:
        opts = (
            session.query(Option)
            .filter_by(activity_id=act.id)
            .order_by(Option.id)
            .all()
        )
        if opts:
            result.append((act, opts))
    return result


def get_unrated_count(session: Session, trip_id: int) -> int:
    return (
        session.query(Option)
        .join(Activity)
        .filter(Activity.trip_id == trip_id, Option.user_rating.is_(None))
        .count()
    )


def set_rating(session: Session, option_id: int, rating: Optional[int]) -> None:
    opt = session.get(Option, option_id)
    if opt:
        opt.user_rating = rating
        session.commit()


def set_option_duration(session: Session, option_id: int, minutes: Optional[int]) -> None:
    """Set the per-option duration override. Pass None to revert to the category default."""
    opt = session.get(Option, option_id)
    if opt:
        opt.default_duration_minutes = minutes
        session.commit()


def get_rated_options_for_schedule(session: Session, trip_id: int) -> list[dict]:
    """Return options formatted for build_schedule(). Only options with a rating."""
    rows = (
        session.query(Option, Activity)
        .join(Activity)
        .filter(Activity.trip_id == trip_id, Option.user_rating.isnot(None))
        .order_by(Option.id)
        .all()
    )
    result = []
    for opt, act in rows:
        si = opt.scheduled_item
        result.append({
            "option_id": opt.id,
            "name": opt.name,
            # Prefer the researcher's per-option category; fall back to the
            # activity's category, then "other".
            "category": opt.category or act.category or "other",
            "latitude": opt.latitude,
            "longitude": opt.longitude,
            "user_rating": opt.user_rating,
            "default_duration_minutes": opt.default_duration_minutes,
            "opening_hours": opt.opening_hours,
            "is_locked": si.is_locked if si else False,
            "day_number": si.day_number if si else None,
            # Existing placement time — lets build_schedule keep locked items
            # pinned to the time the user set rather than re-laying them.
            "start_minutes": si.start_minutes if si else None,
            "time_slot": si.time_slot if si else None,
        })
    return result


def upsert_schedule(session: Session, trip_id: int, day_plans) -> None:
    """Replace non-locked scheduled items atomically.

    If the insert fails the delete is also rolled back, so the original
    schedule is preserved rather than leaving only locked items behind.
    """
    session.query(ScheduledItem).filter(
        ScheduledItem.trip_id == trip_id,
        ScheduledItem.is_locked == False,  # noqa: E712
    ).delete(synchronize_session=False)

    # flush (not commit) so the delete reaches the DB within this transaction,
    # freeing unique option_id slots before the inserts
    session.flush()

    for dp in day_plans:
        for item in dp.items:
            if item.is_locked:
                continue  # locked items were not deleted — don't re-insert
            session.add(ScheduledItem(
                trip_id=trip_id,
                option_id=item.option_id,
                day_number=item.day_number,
                time_slot=None,
                start_minutes=item.start_minutes,
                duration_minutes=item.duration_minutes,
                is_locked=False,
            ))

    # single commit: if this raises, the delete is also rolled back
    session.commit()


def get_schedule(session: Session, trip_id: int) -> list[ScheduledItem]:
    return (
        session.query(ScheduledItem)
        .filter_by(trip_id=trip_id)
        .order_by(
            ScheduledItem.day_number,
            # NULLs sort last; items without start_minutes fall back to end of day.
            case((ScheduledItem.start_minutes.is_(None), 9999), else_=ScheduledItem.start_minutes),
        )
        .all()
    )


# ---------------------------------------------------------------------------
# route_cache — inter-cluster travel-time cache
# ---------------------------------------------------------------------------

def _naive_utcnow() -> datetime:
    """Naive UTC now, matching the tz-naive DateTime columns in the DB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def route_is_fresh(row: RouteCache, now: Optional[datetime] = None) -> bool:
    """True when a cached route is still usable (should not be re-fetched).

    Mirrors the Places staleness rule:
    - A successful row (duration_seconds set) is fresh until ROUTES_STALE_DAYS.
    - A failed/unreachable row (duration_seconds IS NULL) is fresh only until the
      shorter ROUTES_RETRY_DAYS window, then re-attempted.
    """
    now = now or _naive_utcnow()
    age = now - row.refreshed_at
    if row.duration_seconds is None:
        return age < timedelta(days=ROUTES_RETRY_DAYS)
    return age < timedelta(days=ROUTES_STALE_DAYS)


def get_fresh_route_cache(
    session: Session,
    mode: str,
    place_ids: list[str],
    now: Optional[datetime] = None,
) -> dict[tuple[str, str], RouteCache]:
    """Return fresh cached routes among the given place_ids for one mode.

    Keyed by (origin_place_id, dest_place_id). Only rows still within their
    staleness window (see route_is_fresh) are included, so a caller can treat a
    missing key as "needs (re-)fetching". Restricting both endpoints to the
    supplied place_ids keeps the scan to the K×K anchor set.
    """
    if not place_ids:
        return {}
    now = now or _naive_utcnow()
    ids = set(place_ids)
    rows = (
        session.query(RouteCache)
        .filter(
            RouteCache.mode == mode,
            RouteCache.origin_place_id.in_(ids),
            RouteCache.dest_place_id.in_(ids),
        )
        .all()
    )
    return {
        (row.origin_place_id, row.dest_place_id): row
        for row in rows
        if route_is_fresh(row, now)
    }


def upsert_route(
    session: Session,
    origin_place_id: str,
    dest_place_id: str,
    mode: str,
    leg: Optional[dict],
    now: Optional[datetime] = None,
) -> None:
    """Insert or refresh a single cached route leg (no commit — caller commits).

    `leg` is a {"duration_seconds", "distance_meters"} dict for a reachable pair,
    or None to cache a confirmed-unreachable / failed lookup (NULL duration).
    """
    now = now or _naive_utcnow()
    duration = leg.get("duration_seconds") if leg else None
    distance = leg.get("distance_meters") if leg else None
    row = (
        session.query(RouteCache)
        .filter_by(
            origin_place_id=origin_place_id,
            dest_place_id=dest_place_id,
            mode=mode,
        )
        .one_or_none()
    )
    if row is None:
        session.add(
            RouteCache(
                origin_place_id=origin_place_id,
                dest_place_id=dest_place_id,
                mode=mode,
                duration_seconds=duration,
                distance_meters=distance,
                refreshed_at=now,
            )
        )
    else:
        row.duration_seconds = duration
        row.distance_meters = distance
        row.refreshed_at = now
