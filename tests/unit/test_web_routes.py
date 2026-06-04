"""Smoke + guard tests for the FastAPI web routes in web/app.py.

Uses Starlette's TestClient against an in-memory SQLite database (`get_db` is
overridden). No network, no Postgres, no API keys needed.

Background jobs (research / schedule) are patched at the runner-function level
and executed inline via _InlineThread so the polling contract can be tested
without timing dependencies.

Jinja2 cache note: In Python 3.14 + certain Jinja2/Starlette combinations,
Jinja2's LRU cache computes a tuple cache key that becomes unhashable (it
contains a dict). _NoHashCache replaces the LRU cache in the test fixture: it
always reports a miss so Jinja2 takes the normal "load from disk" path without
ever trying to hash the key.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import web.app as webapp
from src.db.models import Base
from src.db.queries import add_activity, create_trip, save_options, set_rating
from web.app import app
from web.deps import get_db


# ---------------------------------------------------------------------------
# Jinja2 cache workaround
# ---------------------------------------------------------------------------

class _NoHashCache:
    """Jinja2-compatible cache stub that always reports a miss and never stores.

    Avoids the Python 3.14 + Jinja2 bug where the LRU cache key tuple becomes
    unhashable. Jinja2 checks `if self.cache is not None` and then calls
    `self.cache.get(key)` / `self.cache[key] = template`. This stub satisfies
    that interface without ever hashing the key.
    """

    def get(self, key, default=None):
        return None  # always miss

    def __setitem__(self, key, value):
        pass  # don't store

    def __contains__(self, key):
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_factory():
    """StaticPool in-memory SQLite: one shared connection, so data written in
    helper sessions is visible to the TestClient's request sessions."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(db_factory):
    def _override_get_db():
        with db_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    _orig_cache = webapp.templates.env.cache
    webapp.templates.env.cache = _NoHashCache()
    with TestClient(app) as c:
        yield c
    webapp.templates.env.cache = _orig_cache
    app.dependency_overrides.clear()


class _InlineThread:
    """Runs the target synchronously on start() so background jobs complete
    within the request that spawns them — no timing or thread coordination."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class _FakeThreading:
    Thread = _InlineThread


def _make_trip(db_factory, name="Test", destination="Tokyo", num_days=3):
    with db_factory() as s:
        trip = create_trip(s, name, destination, num_days)
        return trip.id


# ---------------------------------------------------------------------------
# Trip list + creation
# ---------------------------------------------------------------------------

def test_trip_list_empty(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "No trips yet" in r.text


def test_create_trip_redirects_to_dashboard(client):
    r = client.post(
        "/trips",
        data={"name": "Japan", "destination": "Tokyo", "num_days": "5"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/trips/")


def test_trip_list_shows_created_trip(client):
    client.post("/trips", data={"name": "Japan", "destination": "Tokyo", "num_days": "5"})
    r = client.get("/")
    assert "Japan" in r.text
    assert "Tokyo" in r.text


# ---------------------------------------------------------------------------
# Dashboard guards
# ---------------------------------------------------------------------------

def test_dashboard_renders_for_real_trip(client, db_factory):
    trip_id = _make_trip(db_factory)
    r = client.get(f"/trips/{trip_id}")
    assert r.status_code == 200


def test_dashboard_missing_trip_redirects_home(client):
    r = client.get("/trips/99999", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

def test_add_activity_returns_row(client, db_factory):
    trip_id = _make_trip(db_factory)
    r = client.post(f"/trips/{trip_id}/activities", data={"query": "ramen", "category": "food"})
    assert r.status_code == 200
    assert "ramen" in r.text


def test_add_activity_missing_trip_returns_404(client):
    r = client.post("/trips/99999/activities", data={"query": "ramen"})
    assert r.status_code == 404


def test_update_activity_wrong_trip_returns_404(client, db_factory):
    # An activity that belongs to trip A cannot be edited via trip B's URL.
    trip_a = _make_trip(db_factory, name="A")
    trip_b = _make_trip(db_factory, name="B")
    with db_factory() as s:
        act = add_activity(s, trip_a, "ramen", "food", False)
        act_id = act.id
    r = client.patch(f"/trips/{trip_b}/activities/{act_id}", data={"query": "sushi"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Field-edit allowlist (_EDITABLE_TRIP_FIELDS guard)
# ---------------------------------------------------------------------------

def test_field_edit_rejects_non_allowlisted_field(client, db_factory):
    # 'id' is not in _EDITABLE_TRIP_FIELDS — must return 404, not crash.
    trip_id = _make_trip(db_factory)
    r = client.get(f"/trips/{trip_id}/fields/id")
    assert r.status_code == 404


def test_field_edit_missing_trip_returns_404(client):
    r = client.get("/trips/99999/fields/name")
    assert r.status_code == 404


def test_update_trip_field_persists(client, db_factory):
    trip_id = _make_trip(db_factory, num_days=3)
    r = client.patch(f"/trips/{trip_id}", data={"num_days": "7"})
    assert r.status_code == 200
    r2 = client.get(f"/trips/{trip_id}")
    assert "7" in r2.text


def test_update_missing_trip_returns_404(client):
    r = client.patch("/trips/99999", data={"name": "X"})
    assert r.status_code == 404


def test_update_trip_invalid_date_returns_422(client, db_factory):
    trip_id = _make_trip(db_factory)
    r = client.patch(f"/trips/{trip_id}", data={"start_date": "not-a-date"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Options tab
# ---------------------------------------------------------------------------

def test_options_tab_shows_neighborhood(client, db_factory):
    # An enriched option's neighborhood renders as a chip in the options tab.
    trip_id = _make_trip(db_factory)
    with db_factory() as s:
        act = add_activity(s, trip_id, "ramen", "food", False)
        [opt] = save_options(s, act.id, [{"name": "Ichiran"}])
        opt.neighborhood = "Shinjuku"
        s.commit()
    r = client.get(f"/trips/{trip_id}/tabs/options")
    assert r.status_code == 200
    assert "Shinjuku" in r.text


def test_options_tab_omits_neighborhood_when_absent(client, db_factory):
    # No neighborhood → no chip (and no crash).
    trip_id = _make_trip(db_factory)
    with db_factory() as s:
        act = add_activity(s, trip_id, "ramen", "food", False)
        save_options(s, act.id, [{"name": "Ichiran"}])
    r = client.get(f"/trips/{trip_id}/tabs/options")
    assert r.status_code == 200
    assert "Ichiran" in r.text


# ---------------------------------------------------------------------------
# Rating
# ---------------------------------------------------------------------------

def test_rate_missing_option_returns_404(client, db_factory):
    trip_id = _make_trip(db_factory)
    r = client.post(f"/trips/{trip_id}/options/99999/rate", data={"rating": "4"})
    assert r.status_code == 404


def test_rate_option_updates_rating(client, db_factory):
    trip_id = _make_trip(db_factory)
    with db_factory() as s:
        act = add_activity(s, trip_id, "ramen", "food", False)
        [opt] = save_options(s, act.id, [{"name": "Ichiran"}])
        opt_id = opt.id
    r = client.post(f"/trips/{trip_id}/options/{opt_id}/rate", data={"rating": "5"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Research polling contract
# ---------------------------------------------------------------------------

def test_research_status_no_job_shows_spinner(client, db_factory):
    # No job registered → status endpoint returns the in-progress spinner.
    trip_id = _make_trip(db_factory)
    r = client.get(f"/trips/{trip_id}/research/status")
    assert r.status_code == 200
    assert 'id="research-status"' in r.text


def test_research_flow_completes(client, db_factory, monkeypatch):
    # Full round-trip: POST starts job (spinner returned), GET /status
    # transitions to the rendered tab once the job completes.
    trip_id = _make_trip(db_factory)
    with db_factory() as s:
        add_activity(s, trip_id, "ramen", "food", False)

    def fake_runner(tid, job):
        job.result = [{"query": "ramen", "options_saved": 2, "error": ""}]
        job.done = True

    monkeypatch.setattr(webapp, "_run_research_and_enrich_background", fake_runner)
    monkeypatch.setattr(webapp, "threading", _FakeThreading)

    start = client.post(f"/trips/{trip_id}/research")
    assert start.status_code == 200
    assert 'id="research-status"' in start.text  # spinner partial

    done = client.get(f"/trips/{trip_id}/research/status")
    assert done.status_code == 200
    assert 'id="research-status"' not in done.text  # replaced by tab content


# ---------------------------------------------------------------------------
# Schedule polling contract
# ---------------------------------------------------------------------------

def test_schedule_status_no_job_shows_spinner(client, db_factory):
    trip_id = _make_trip(db_factory)
    r = client.get(f"/trips/{trip_id}/schedule/status")
    assert r.status_code == 200
    assert 'id="schedule-status"' in r.text


def test_schedule_flow_completes(client, db_factory, monkeypatch):
    trip_id = _make_trip(db_factory)

    def fake_runner(tid, num_days, use_llm, job):
        job.result = {"warn": ""}
        job.done = True

    monkeypatch.setattr(webapp, "_run_schedule_background", fake_runner)
    monkeypatch.setattr(webapp, "threading", _FakeThreading)

    start = client.post(f"/trips/{trip_id}/schedule", data={"use_llm": "false"})
    assert start.status_code == 200
    assert 'id="schedule-status"' in start.text  # spinner partial

    done = client.get(f"/trips/{trip_id}/schedule/status")
    assert done.status_code == 200
    assert 'id="schedule-status"' not in done.text  # replaced by tab content


# ---------------------------------------------------------------------------
# Option duration (P2)
# ---------------------------------------------------------------------------

def test_set_duration_persists(client, db_factory):
    trip_id = _make_trip(db_factory)
    with db_factory() as s:
        act = add_activity(s, trip_id, "ramen", "food", False)
        [opt] = save_options(s, act.id, [{"name": "Ichiran"}])
        opt_id = opt.id
    r = client.patch(f"/trips/{trip_id}/options/{opt_id}/duration",
                     data={"duration_minutes": "90"})
    assert r.status_code == 200
    with db_factory() as s:
        from src.db.models import Option
        o = s.get(Option, opt_id)
        assert o.default_duration_minutes == 90


def test_set_duration_blank_reverts_to_none(client, db_factory):
    trip_id = _make_trip(db_factory)
    with db_factory() as s:
        act = add_activity(s, trip_id, "ramen", "food", False)
        [opt] = save_options(s, act.id, [{"name": "Ichiran"}])
        opt_id = opt.id
    # Set then clear
    client.patch(f"/trips/{trip_id}/options/{opt_id}/duration",
                 data={"duration_minutes": "60"})
    r = client.patch(f"/trips/{trip_id}/options/{opt_id}/duration",
                     data={"duration_minutes": ""})
    assert r.status_code == 200
    with db_factory() as s:
        from src.db.models import Option
        o = s.get(Option, opt_id)
        assert o.default_duration_minutes is None


def test_set_duration_zero_reverts_to_none(client, db_factory):
    trip_id = _make_trip(db_factory)
    with db_factory() as s:
        act = add_activity(s, trip_id, "ramen", "food", False)
        [opt] = save_options(s, act.id, [{"name": "Ichiran"}])
        opt_id = opt.id
    r = client.patch(f"/trips/{trip_id}/options/{opt_id}/duration",
                     data={"duration_minutes": "0"})
    assert r.status_code == 200
    with db_factory() as s:
        from src.db.models import Option
        o = s.get(Option, opt_id)
        assert o.default_duration_minutes is None


def test_set_duration_caps_at_1440(client, db_factory):
    trip_id = _make_trip(db_factory)
    with db_factory() as s:
        act = add_activity(s, trip_id, "ramen", "food", False)
        [opt] = save_options(s, act.id, [{"name": "Ichiran"}])
        opt_id = opt.id
    r = client.patch(f"/trips/{trip_id}/options/{opt_id}/duration",
                     data={"duration_minutes": "9999"})
    assert r.status_code == 200
    with db_factory() as s:
        from src.db.models import Option
        o = s.get(Option, opt_id)
        assert o.default_duration_minutes == 1440


def test_set_duration_missing_option_returns_404(client, db_factory):
    trip_id = _make_trip(db_factory)
    r = client.patch(f"/trips/{trip_id}/options/99999/duration",
                     data={"duration_minutes": "60"})
    assert r.status_code == 404


def test_set_duration_wrong_trip_returns_404(client, db_factory):
    trip_a = _make_trip(db_factory, name="A")
    trip_b = _make_trip(db_factory, name="B")
    with db_factory() as s:
        act = add_activity(s, trip_a, "ramen", "food", False)
        [opt] = save_options(s, act.id, [{"name": "Ichiran"}])
        opt_id = opt.id
    r = client.patch(f"/trips/{trip_b}/options/{opt_id}/duration",
                     data={"duration_minutes": "60"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Schedule item drag / resize (P4)
# ---------------------------------------------------------------------------

def _make_scheduled_item(db_factory, trip_id, start_minutes=540, locked=False):
    """Create an activity + option + scheduled item; return (opt_id, si_id)."""
    from src.db.models import ScheduledItem
    with db_factory() as s:
        act = add_activity(s, trip_id, "ramen", "food", False)
        [opt] = save_options(s, act.id, [{"name": "Ichiran"}])
        set_rating(s, opt.id, 4)
        si = ScheduledItem(trip_id=trip_id, option_id=opt.id,
                           day_number=1, time_slot="morning",
                           start_minutes=start_minutes, is_locked=locked)
        s.add(si)
        s.commit()
        return opt.id, si.id


def test_patch_start_minutes_persists(client, db_factory):
    trip_id = _make_trip(db_factory)
    _, si_id = _make_scheduled_item(db_factory, trip_id, start_minutes=540)
    r = client.patch(f"/trips/{trip_id}/schedule/{si_id}",
                     data={"start_minutes": "660"})
    assert r.status_code == 200
    from src.db.models import ScheduledItem
    with db_factory() as s:
        si = s.get(ScheduledItem, si_id)
        assert si.start_minutes == 660


def test_patch_start_minutes_clamped_to_valid_range(client, db_factory):
    trip_id = _make_trip(db_factory)
    _, si_id = _make_scheduled_item(db_factory, trip_id)
    # Above max → clamped to 1439
    client.patch(f"/trips/{trip_id}/schedule/{si_id}", data={"start_minutes": "9999"})
    from src.db.models import ScheduledItem
    with db_factory() as s:
        assert s.get(ScheduledItem, si_id).start_minutes == 1439
    # Below min → clamped to 0
    client.patch(f"/trips/{trip_id}/schedule/{si_id}", data={"start_minutes": "-100"})
    with db_factory() as s:
        assert s.get(ScheduledItem, si_id).start_minutes == 0


def test_patch_duration_minutes_persists(client, db_factory):
    trip_id = _make_trip(db_factory)
    _, si_id = _make_scheduled_item(db_factory, trip_id)
    r = client.patch(f"/trips/{trip_id}/schedule/{si_id}",
                     data={"duration_minutes": "90"})
    assert r.status_code == 200
    from src.db.models import ScheduledItem
    with db_factory() as s:
        assert s.get(ScheduledItem, si_id).duration_minutes == 90


def test_patch_duration_minutes_capped_at_1440(client, db_factory):
    trip_id = _make_trip(db_factory)
    _, si_id = _make_scheduled_item(db_factory, trip_id)
    client.patch(f"/trips/{trip_id}/schedule/{si_id}", data={"duration_minutes": "9999"})
    from src.db.models import ScheduledItem
    with db_factory() as s:
        assert s.get(ScheduledItem, si_id).duration_minutes == 1440


def test_locked_item_ignores_movement_fields(client, db_factory):
    # Server must ignore start_minutes/duration_minutes/day_number for locked items.
    trip_id = _make_trip(db_factory)
    _, si_id = _make_scheduled_item(db_factory, trip_id, start_minutes=540, locked=True)
    r = client.patch(f"/trips/{trip_id}/schedule/{si_id}",
                     data={"start_minutes": "900", "duration_minutes": "120", "day_number": "3"})
    assert r.status_code == 200
    from src.db.models import ScheduledItem
    with db_factory() as s:
        si = s.get(ScheduledItem, si_id)
        assert si.start_minutes == 540   # unchanged
        assert si.duration_minutes is None  # unchanged
        assert si.day_number == 1           # unchanged


def test_patch_schedule_item_missing_returns_404(client, db_factory):
    trip_id = _make_trip(db_factory)
    r = client.patch(f"/trips/{trip_id}/schedule/99999",
                     data={"start_minutes": "600"})
    assert r.status_code == 404
