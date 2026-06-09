"""Phase 2 tests: flights/travel constrain scheduling via per-day windows and
locked anchors. No network — exercises the pure planner helpers directly.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Base
from src.workers.logistics import build_day_window_fn, travel_option_dicts
from src.workers.planner.deterministic import DeterministicPlanner
from src.workers.planner.llm import apply_llm_refinement
from src.workers.planner.types import DayPlan, DayWindow, ScheduleItem, constant_window


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


# --- build_day_window_fn --------------------------------------------------

def test_window_no_travel_is_constant():
    w = build_day_window_fn([], base_start=540, base_end=1260)
    assert w(1) == DayWindow(540, 1260)
    assert w(5) == DayWindow(540, 1260)


def test_window_arrival_raises_start_only_for_that_day():
    travel = [{"kind": "arrival", "day_number": 1, "time_minutes": 840, "transit_minutes": 90}]
    w = build_day_window_fn(travel, base_start=540, base_end=1260)
    assert w(1).start_minutes == 840 + 90   # land 14:00 + 90m transit → 15:30
    assert w(1).end_minutes == 1260
    assert w(2).start_minutes == 540          # other days untouched


def test_window_departure_lowers_end():
    travel = [{"kind": "departure", "day_number": 3, "time_minutes": 1140, "transit_minutes": 120}]
    w = build_day_window_fn(travel, base_start=540, base_end=1260)
    assert w(3).end_minutes == 1140 - 120     # depart 19:00 − 120m → 17:00
    assert w(3).start_minutes == 540


def test_window_inverted_guard():
    # Arrival after the base day-end must not produce start > end.
    travel = [{"kind": "arrival", "day_number": 1, "time_minutes": 1300, "transit_minutes": 0}]
    w = build_day_window_fn(travel, base_start=540, base_end=1260)
    win = w(1)
    assert win.start_minutes <= win.end_minutes


def test_window_ignores_rows_without_day_or_time():
    travel = [{"kind": "arrival", "label": "x"}, {"kind": "departure", "day_number": 2}]
    w = build_day_window_fn(travel, base_start=540, base_end=1260)
    assert w(2) == DayWindow(540, 1260)


# --- travel_option_dicts --------------------------------------------------

def test_travel_option_dicts_projects_locked_transport():
    travel = [{"id": 7, "kind": "arrival", "day_number": 1, "time_minutes": 870,
               "transit_minutes": 60, "label": "Narita", "mode": "flight"}]
    out = travel_option_dicts(travel)
    assert len(out) == 1
    d = out[0]
    assert d["is_locked"] is True and d["day_number"] == 1 and d["start_minutes"] == 870
    assert d["category"] == "transport" and d["option_id"] < 0
    assert d["default_duration_minutes"] == 60
    assert "Narita" in d["name"]


def test_travel_option_dicts_skips_incomplete():
    assert travel_option_dicts([{"kind": "arrival", "label": "x"}]) == []


# --- deterministic planner per-day window ---------------------------------

def _opt(i, category="culture", rating=5):
    return {"option_id": i, "name": f"O{i}", "category": category, "user_rating": rating}


def test_deterministic_respects_per_day_window_start():
    planner = DeterministicPlanner()
    window = (lambda d: DayWindow(900, 1260) if d == 1 else DayWindow(540, 1260))
    res = asyncio.run(planner.plan([_opt(1)], num_days=1, min_rating=1, window_fn=window))
    assert res.day_plans[0].items[0].start_minutes == 900  # pushed to 15:00 by arrival


def test_deterministic_no_window_fn_matches_constant_window():
    """Golden regression: default behavior == an explicit constant window."""
    planner = DeterministicPlanner()
    options = [_opt(i) for i in range(1, 5)]
    a = asyncio.run(planner.plan(options, num_days=2, min_rating=1))
    b = asyncio.run(planner.plan(options, num_days=2, min_rating=1,
                                 window_fn=constant_window(540, 1260)))

    def flat(r):
        return [(dp.day_number, [(i.option_id, i.start_minutes) for i in dp.items])
                for dp in r.day_plans]

    assert flat(a) == flat(b)


# --- apply_llm_refinement: departure cap vs locked anchor -----------------

def _item(option_id, *, locked=False, day=2, start=None, category="culture"):
    return ScheduleItem(
        option_id=option_id, name=f"x{option_id}", category=category,
        latitude=None, longitude=None, user_rating=5,
        is_locked=locked, day_number=day, start_minutes=start, duration_minutes=60,
    )


def test_departure_cap_drops_unlocked_keeps_locked():
    # Day 2 ends early (17:00) because of a departure flight; the locked departure
    # anchor itself starts at 19:00 and must survive, while an unlocked 17:30 stop
    # is dropped by the cap.
    locked_dep = _item(-1, locked=True, day=2, start=1140, category="transport")
    free = _item(1, locked=False, day=2)
    draft = [DayPlan(day_number=1, items=[]), DayPlan(day_number=2, items=[locked_dep, free])]
    window = (lambda d: DayWindow(540, 1020) if d == 2 else DayWindow(540, 1260))
    llm_days = [{"day": 2, "items": [{"option_id": 1, "start_minutes": 1050}]}]

    result = apply_llm_refinement(llm_days, draft, num_days=2, window_fn=window)
    day2 = next(dp for dp in result if dp.day_number == 2)
    ids = {i.option_id for i in day2.items}
    assert -1 in ids        # locked departure kept despite starting after the cap
    assert 1 not in ids     # unlocked 17:30 stop dropped by the day-2 end cap


def test_arrival_window_seeds_unlocked_start():
    # Day 1 starts at 16:00 (arrival); an unlocked stop with no LLM time should
    # fall forward to the day's start, not the global 09:00.
    free = _item(1, locked=False, day=1, start=None)
    draft = [DayPlan(day_number=1, items=[free])]
    window = (lambda d: DayWindow(960, 1260))  # 16:00 start
    llm_days = [{"day": 1, "items": [{"option_id": 1}]}]  # no start_minutes
    result = apply_llm_refinement(llm_days, draft, num_days=1, window_fn=window)
    assert result[0].items[0].start_minutes == 960


# --- Phase 3: hotels as home-base anchors ---------------------------------

from src.services.travel import ClusterAnchor, cluster_and_route, select_anchors
from src.workers.planner.clustering import Cluster
from src.workers.planner.llm import _format_home_bases
from src.services.trip_service import _home_base_by_day


def test_select_anchors_forced_override_wins():
    clusters = [Cluster(cluster_id=0, name="A", member_ids=(1, 2))]
    forced = {0: ClusterAnchor(0, "hotelP", 35.7, 139.7)}
    anchors = select_anchors(
        clusters, {1: "p1", 2: "p2"}, {1: 4.0, 2: 5.0},
        forced_anchor_by_cluster=forced,
    )
    assert len(anchors) == 1 and anchors[0].place_id == "hotelP"


def test_select_anchors_default_without_force():
    clusters = [Cluster(cluster_id=0, name="A", member_ids=(1, 2))]
    anchors = select_anchors(clusters, {1: "p1", 2: "p2"}, {1: 4.0, 2: 5.0})
    assert anchors[0].place_id == "p2"  # highest rating wins normally


def test_cluster_and_route_stamps_hotel_cluster(session):
    # A hotel co-located with an option joins that option's cluster.
    options = [{"option_id": 1, "name": "Museum", "latitude": 35.71,
                "longitude": 139.79, "place_id": "opt1", "google_rating": 4.5}]
    hotels = [{"id": 9, "option_id": -2_000_009, "name": "Hotel", "label": "Hotel",
               "latitude": 35.71, "longitude": 139.79, "place_id": "hotelP",
               "check_in_day": 1, "check_out_day": 3}]
    cluster_and_route(session, options, hotels=hotels)
    assert hotels[0]["cluster_id"] is not None
    assert options[0]["cluster_id"] == hotels[0]["cluster_id"]


def test_format_home_bases_renders_per_day():
    hb = {1: {"label": "Park Hyatt", "cluster_id": 2},
          2: {"label": "Park Hyatt", "cluster_id": 2}}
    out = _format_home_bases(hb, num_days=2)
    assert "Home base" in out and "Park Hyatt" in out
    assert "cluster 2" in out and "Day 1" in out and "Day 2" in out


def test_format_home_bases_empty():
    assert _format_home_bases(None, 3) == ""
    assert _format_home_bases({}, 3) == ""


def test_home_base_by_day_latest_checkin_wins_on_overlap():
    lodging = [
        {"label": "A", "check_in_day": 1, "check_out_day": 3, "cluster_id": 0},
        {"label": "B", "check_in_day": 3, "check_out_day": 5, "cluster_id": 1},
    ]
    m = _home_base_by_day(lodging, 5)
    assert m[1]["label"] == "A" and m[2]["label"] == "A"
    assert m[3]["label"] == "B" and m[5]["label"] == "B"  # day 3 overlap → latest


def test_home_base_by_day_tolerates_gaps():
    lodging = [{"label": "A", "check_in_day": 1, "check_out_day": 2, "cluster_id": 0}]
    m = _home_base_by_day(lodging, 4)
    assert set(m) == {1, 2} and 3 not in m and 4 not in m
