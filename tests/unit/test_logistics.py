"""Unit tests for trip logistics (travel + lodging): queries + service helpers.

In-memory SQLite; no network. Web-route tests live in test_web_routes.py.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Base, Logistics
from src.db.queries import (
    add_logistics,
    create_trip,
    delete_logistics,
    get_logistics,
    get_unenriched_logistics,
    update_logistics,
)
from src.services.trip_service import geocode_logistics, hotel_for_day


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


@pytest.fixture
def trip_id(session):
    return create_trip(session, "Test", "Tokyo", 3).id


# --- add / parse ----------------------------------------------------------

def test_add_travel_parses_time_and_ints(session, trip_id):
    item = add_logistics(session, trip_id, {
        "kind": "arrival", "mode": "flight", "label": "Narita",
        "day_number": "1", "time_minutes": "14:30", "transit_minutes": "60",
    })
    assert item is not None
    assert item.kind == "arrival" and item.mode == "flight" and item.label == "Narita"
    assert item.day_number == 1 and item.time_minutes == 14 * 60 + 30
    assert item.transit_minutes == 60


def test_add_lodging_parses_day_range(session, trip_id):
    item = add_logistics(session, trip_id, {
        "kind": "lodging", "label": "Park Hyatt",
        "check_in_day": "1", "check_out_day": "3",
    })
    assert item.kind == "lodging" and item.check_in_day == 1 and item.check_out_day == 3
    assert item.mode is None and item.time_minutes is None


def test_add_rejects_invalid_kind(session, trip_id):
    assert add_logistics(session, trip_id, {"kind": "bogus", "label": "x"}) is None


def test_add_drops_invalid_mode_and_blank_time(session, trip_id):
    item = add_logistics(session, trip_id, {
        "kind": "departure", "mode": "spaceship", "label": "HND",
        "day_number": "3", "time_minutes": "",
    })
    assert item.mode is None and item.time_minutes is None and item.day_number == 3


# --- get / ordering -------------------------------------------------------

def test_get_logistics_returns_all_for_trip(session, trip_id):
    add_logistics(session, trip_id, {"kind": "arrival", "label": "A", "day_number": "1"})
    add_logistics(session, trip_id, {"kind": "lodging", "label": "H", "check_in_day": "1", "check_out_day": "3"})
    rows = get_logistics(session, trip_id)
    assert len(rows) == 2
    assert {r.kind for r in rows} == {"arrival", "lodging"}


# --- update ---------------------------------------------------------------

def test_update_changes_field(session, trip_id):
    item = add_logistics(session, trip_id, {"kind": "arrival", "label": "A", "day_number": "1"})
    update_logistics(session, item, {"day_number": "2", "time_minutes": "09:15"})
    assert item.day_number == 2 and item.time_minutes == 9 * 60 + 15


def test_update_is_immutable_on_kind(session, trip_id):
    item = add_logistics(session, trip_id, {"kind": "arrival", "label": "A"})
    update_logistics(session, item, {"kind": "lodging", "label": "B"})
    assert item.kind == "arrival" and item.label == "B"


def test_update_label_clears_geocode(session, trip_id):
    item = add_logistics(session, trip_id, {"kind": "lodging", "label": "Old Hotel"})
    item.place_id = "abc"
    from datetime import datetime
    item.place_refreshed_at = datetime(2026, 1, 1)
    session.commit()
    update_logistics(session, item, {"label": "New Hotel"})
    assert item.place_id is None and item.place_refreshed_at is None


def test_update_same_label_keeps_geocode(session, trip_id):
    item = add_logistics(session, trip_id, {"kind": "lodging", "label": "Hotel", "check_in_day": "1"})
    item.place_id = "abc"
    from datetime import datetime
    item.place_refreshed_at = datetime(2026, 1, 1)
    session.commit()
    update_logistics(session, item, {"check_out_day": "4"})  # non-location field
    assert item.place_id == "abc" and item.place_refreshed_at is not None


# --- delete ---------------------------------------------------------------

def test_delete_logistics(session, trip_id):
    item = add_logistics(session, trip_id, {"kind": "arrival", "label": "A"})
    delete_logistics(session, item)
    assert get_logistics(session, trip_id) == []


# --- unenriched gating ----------------------------------------------------

def test_get_unenriched_only_untried_with_location(session, trip_id):
    a = add_logistics(session, trip_id, {"kind": "arrival", "label": "Narita"})
    add_logistics(session, trip_id, {"kind": "departure"})  # no label/address → skipped
    rows = get_unenriched_logistics(session, trip_id)
    assert [r.id for r in rows] == [a.id]


def test_get_unenriched_excludes_already_attempted(session, trip_id):
    item = add_logistics(session, trip_id, {"kind": "lodging", "label": "Hotel"})
    from datetime import datetime
    item.place_refreshed_at = datetime(2026, 1, 1)  # attempted (even if failed)
    session.commit()
    assert get_unenriched_logistics(session, trip_id) == []


# --- hotel_for_day --------------------------------------------------------

def _lodging(check_in, check_out, label="H"):
    return Logistics(kind="lodging", label=label, check_in_day=check_in, check_out_day=check_out)


def test_hotel_for_day_single_range():
    h = _lodging(1, 3)
    assert hotel_for_day([h], 1) is h
    assert hotel_for_day([h], 3) is h
    assert hotel_for_day([h], 4) is None
    assert hotel_for_day([h], 0) is None


def test_hotel_for_day_overlap_latest_checkin_wins():
    early, late = _lodging(1, 3, "early"), _lodging(3, 5, "late")
    assert hotel_for_day([early, late], 3) is late  # overlap day → latest check_in


def test_hotel_for_day_gap_returns_none():
    assert hotel_for_day([_lodging(1, 2), _lodging(4, 5)], 3) is None


def test_hotel_for_day_ignores_incomplete_range():
    assert hotel_for_day([_lodging(1, None)], 1) is None
    assert hotel_for_day([], 1) is None


# --- geocode_logistics ----------------------------------------------------

class _FakePlaces:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def lookup(self, query):
        self.calls.append(query)
        return self._result


def test_geocode_noop_without_client(session, trip_id):
    from src.db.models import Trip
    add_logistics(session, trip_id, {"kind": "arrival", "label": "Narita"})
    trip = session.get(Trip, trip_id)
    assert asyncio.run(geocode_logistics(session, trip, None)) == {"geocoded": 0, "failed": 0}


def test_geocode_sets_fields_on_hit(session, trip_id):
    from src.db.models import Trip
    item = add_logistics(session, trip_id, {"kind": "lodging", "label": "Park Hyatt"})
    client = _FakePlaces({
        "place_id": "p1", "latitude": 35.6, "longitude": 139.7,
        "maps_link": "http://maps/x", "formatted_address": "Tokyo",
    })
    stats = asyncio.run(geocode_logistics(session, session.get(Trip, trip_id), client))
    assert stats == {"geocoded": 1, "failed": 0}
    assert item.place_id == "p1" and item.latitude == 35.6 and item.maps_link == "http://maps/x"
    assert item.address == "Tokyo" and item.place_refreshed_at is not None
    # the destination is appended to the search query
    assert "Park Hyatt" in client.calls[0] and "Tokyo" in client.calls[0]


def test_geocode_marks_attempted_on_miss(session, trip_id):
    from src.db.models import Trip
    item = add_logistics(session, trip_id, {"kind": "arrival", "label": "Nowhere"})
    stats = asyncio.run(geocode_logistics(session, session.get(Trip, trip_id), _FakePlaces(None)))
    assert stats == {"geocoded": 0, "failed": 1}
    assert item.place_id is None and item.place_refreshed_at is not None  # won't retry every save


def test_cascade_delete_with_trip(session, trip_id):
    from src.db.models import Trip
    add_logistics(session, trip_id, {"kind": "arrival", "label": "A"})
    trip = session.get(Trip, trip_id)
    session.delete(trip)
    session.commit()
    assert session.query(Logistics).count() == 0
