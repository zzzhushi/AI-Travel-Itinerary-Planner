"""Database query helpers for the CLI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from src.db.models import (
    Activity,
    BUDGET_CHOICES,
    LOGISTICS_KINDS,
    Logistics,
    Option,
    PACE_CHOICES,
    RouteCache,
    ScheduledItem,
    TRAVEL_MODES,
    Trip,
    TripPreferences,
)

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


# ---------------------------------------------------------------------------
# Trip preferences
# ---------------------------------------------------------------------------

# Fields the web layer is allowed to write, and how each is parsed/validated.
_PREFERENCE_FIELDS = frozenset(
    ("pace", "budget", "interests", "notes", "day_start_minutes", "day_end_minutes")
)


def _parse_hhmm(raw: str) -> Optional[int]:
    """Parse an "HH:MM" time string into minutes-from-midnight, or None."""
    parts = raw.split(":")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    hours, minutes = int(parts[0]), int(parts[1])
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def get_or_create_preferences(session: Session, trip_id: int) -> TripPreferences:
    """Return the trip's preferences row, creating an empty one if absent."""
    prefs = (
        session.query(TripPreferences)
        .filter(TripPreferences.trip_id == trip_id)
        .one_or_none()
    )
    if prefs is None:
        prefs = TripPreferences(trip_id=trip_id)
        session.add(prefs)
        session.commit()
        session.refresh(prefs)
    return prefs


def update_preferences(
    session: Session, trip_id: int, field: str, raw: str
) -> TripPreferences:
    """Validate and apply a single preference field, then commit.

    Unknown fields are ignored (allowlist). Invalid enum values and blank input
    reset the field to NULL ("no preference"). interests is parsed from a
    comma-separated string into a JSON list; window edges from "HH:MM".
    """
    prefs = get_or_create_preferences(session, trip_id)
    if field not in _PREFERENCE_FIELDS:
        return prefs

    stripped = str(raw).strip()
    if field == "pace":
        prefs.pace = stripped if stripped in PACE_CHOICES else None
    elif field == "budget":
        prefs.budget = stripped if stripped in BUDGET_CHOICES else None
    elif field == "interests":
        items = [s.strip() for s in stripped.split(",") if s.strip()]
        prefs.interests = items or None
    elif field == "notes":
        prefs.notes = stripped or None
    elif field == "day_start_minutes":
        prefs.day_start_minutes = _parse_hhmm(stripped) if stripped else None
    elif field == "day_end_minutes":
        prefs.day_end_minutes = _parse_hhmm(stripped) if stripped else None

    session.commit()
    session.refresh(prefs)
    return prefs


def reset_research_state(session: Session, trip_id: int) -> None:
    """Clear research output so it can be regenerated under new preferences.

    Drops the trip's schedule and every researched Option, then marks all
    activities unresearched. This lets the normal research flow re-run cleanly
    without appending duplicate options. Destructive: the current schedule and
    ratings are discarded.
    """
    activity_ids = [a.id for a in get_activities(session, trip_id)]

    session.query(ScheduledItem).filter(
        ScheduledItem.trip_id == trip_id
    ).delete(synchronize_session=False)

    if activity_ids:
        session.query(Option).filter(
            Option.activity_id.in_(activity_ids)
        ).delete(synchronize_session=False)
        session.query(Activity).filter(
            Activity.id.in_(activity_ids)
        ).update({Activity.researched_at: None}, synchronize_session=False)

    session.commit()


# ---------------------------------------------------------------------------
# Trip logistics (travel + lodging)
# ---------------------------------------------------------------------------

# Location fields whose change triggers a re-geocode (see trip_service).
LOGISTICS_LOCATION_FIELDS = frozenset(("label", "address"))
# Form fields the web layer may write, parsed/validated by _coerce_logistics.
_LOGISTICS_FIELDS = frozenset(
    (
        "kind", "mode", "label", "day_number", "time_minutes", "transit_minutes",
        "check_in_day", "check_out_day", "address", "note",
    )
)


def _to_int(raw: str) -> Optional[int]:
    s = str(raw).strip()
    return int(s) if s.lstrip("-").isdigit() else None


def _coerce_logistics(raw: dict) -> dict:
    """Parse a raw form dict into typed Logistics column values (allowlisted).

    Only keys present in `raw` and in _LOGISTICS_FIELDS are returned, so this is
    usable for both create (full form) and patch (single field). `time_minutes`
    accepts "HH:MM"; day/transit fields accept integers; enum fields fall back to
    None when invalid.
    """
    out: dict = {}
    for field, value in raw.items():
        if field not in _LOGISTICS_FIELDS:
            continue
        s = str(value).strip()
        if field == "kind":
            out["kind"] = s if s in LOGISTICS_KINDS else None
        elif field == "mode":
            out["mode"] = s if s in TRAVEL_MODES else None
        elif field in ("label", "address", "note"):
            out[field] = s or None
        elif field == "time_minutes":
            out["time_minutes"] = _parse_hhmm(s) if s else None
        elif field in ("day_number", "transit_minutes", "check_in_day", "check_out_day"):
            out[field] = _to_int(s)
    return out


def get_logistics(session: Session, trip_id: int) -> list[Logistics]:
    """All logistics rows for a trip, ordered for stable display."""
    return (
        session.query(Logistics)
        .filter_by(trip_id=trip_id)
        .order_by(Logistics.kind, Logistics.day_number, Logistics.check_in_day, Logistics.id)
        .all()
    )


def add_logistics(session: Session, trip_id: int, raw: dict) -> Optional[Logistics]:
    """Create a logistics row from a raw form dict. Returns None if kind invalid."""
    fields = _coerce_logistics(raw)
    if not fields.get("kind"):
        return None
    item = Logistics(trip_id=trip_id, **fields)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def update_logistics(session: Session, item: Logistics, raw: dict) -> Logistics:
    """Apply allowlisted, parsed fields to an existing logistics row, then commit.

    If a location field (label/address) changed, clears place_refreshed_at so the
    caller's geocode pass re-resolves coordinates.
    """
    fields = _coerce_logistics(raw)
    fields.pop("kind", None)  # kind is immutable after creation
    location_changed = False
    for field, value in fields.items():
        if field in LOGISTICS_LOCATION_FIELDS and getattr(item, field) != value:
            location_changed = True
        setattr(item, field, value)
    if location_changed:
        item.place_id = None
        item.place_refreshed_at = None
    session.commit()
    session.refresh(item)
    return item


def delete_logistics(session: Session, item: Logistics) -> None:
    session.delete(item)
    session.commit()


def get_unenriched_logistics(session: Session, trip_id: int) -> list[Logistics]:
    """Logistics rows that still need a Places geocode.

    Only rows with a location hint (label or address) and not yet attempted
    (place_refreshed_at IS NULL). One-shot: a failed lookup sets
    place_refreshed_at so we never re-hit Places on every save.
    """
    return (
        session.query(Logistics)
        .filter(
            Logistics.trip_id == trip_id,
            Logistics.place_refreshed_at.is_(None),
            or_(Logistics.label.isnot(None), Logistics.address.isnot(None)),
        )
        .order_by(Logistics.id)
        .all()
    )


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
            # Places-enrichment fields used by geographic clustering + the
            # inter-cluster travel matrix (src/services/travel.py). May be None
            # when an option has not been enriched.
            "place_id": opt.place_id,
            "neighborhood": opt.neighborhood,
            "google_rating": opt.google_rating,
            "price_level": opt.price_level,
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
