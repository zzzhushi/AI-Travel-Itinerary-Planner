"""Database query helpers for the CLI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.db.models import Activity, Option, ScheduledItem, Trip

# Google ToS allows caching place data for up to 30 days.
PLACES_STALE_DAYS = 30


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
        act.researched_at = datetime.utcnow()
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
            latitude=o.get("latitude"),
            longitude=o.get("longitude"),
            research_hash=o.get("research_hash"),
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
    - previously returned no match (place_id IS NULL) and should be retried.

    All options have a name, which is used as a fallback search string when
    maps_search is NULL (i.e. options researched before that field was added).
    """
    stale_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=PLACES_STALE_DAYS)
    return (
        session.query(Option)
        .join(Activity)
        .filter(
            Activity.trip_id == trip_id,
            Option.name.isnot(None),  # name is always set; used as fallback search string
            or_(
                Option.place_refreshed_at.is_(None),
                Option.place_refreshed_at < stale_cutoff,
                Option.place_id.is_(None),
            ),
        )
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
            "category": act.category or "other",
            "latitude": opt.latitude,
            "longitude": opt.longitude,
            "user_rating": opt.user_rating,
            "is_locked": si.is_locked if si else False,
            "day_number": si.day_number if si else None,
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
                time_slot=item.time_slot,
                is_locked=False,
            ))

    # single commit: if this raises, the delete is also rolled back
    session.commit()


def get_schedule(session: Session, trip_id: int) -> list[ScheduledItem]:
    return (
        session.query(ScheduledItem)
        .filter_by(trip_id=trip_id)
        .order_by(ScheduledItem.day_number, ScheduledItem.time_slot)
        .all()
    )
