"""Unit tests for the route_cache helpers and the inter-cluster travel matrix.

Uses the SQLite in-memory session fixture and MockRoutesClient — no network,
no API keys. Covers cache freshness/upsert, anchor selection, and the
cache-aware matrix coordinator (cache hits skip the API; failures degrade).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.db.models import RouteCache
from src.db.queries import (
    ROUTES_RETRY_DAYS,
    ROUTES_STALE_DAYS,
    get_fresh_route_cache,
    route_is_fresh,
    upsert_route,
)
from src.services.travel import (
    ClusterAnchor,
    compute_inter_cluster_matrix,
    select_anchors,
)
from src.workers.planner.clustering import Cluster
from tests.mocks.routes_client import MockRoutesClient


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days_ago(n: int) -> datetime:
    return _utcnow() - timedelta(days=n)


def _leg(seconds: int, meters: int = 500) -> dict:
    return {"duration_seconds": seconds, "distance_meters": meters}


# ---------------------------------------------------------------------------
# upsert_route + get_fresh_route_cache + route_is_fresh
# ---------------------------------------------------------------------------

class TestRouteCacheHelpers:
    def test_upsert_inserts_then_updates_same_row(self, session):
        upsert_route(session, "A", "B", "walk", _leg(1200, 1000))
        session.commit()
        upsert_route(session, "A", "B", "walk", _leg(1300, 1100))
        session.commit()

        rows = session.query(RouteCache).all()
        assert len(rows) == 1  # unique (origin, dest, mode) → updated in place
        assert rows[0].duration_seconds == 1300
        assert rows[0].distance_meters == 1100

    def test_upsert_none_caches_a_negative(self, session):
        upsert_route(session, "A", "B", "walk", None)
        session.commit()
        row = session.query(RouteCache).one()
        assert row.duration_seconds is None
        assert row.distance_meters is None

    def test_same_pair_different_mode_is_distinct_row(self, session):
        upsert_route(session, "A", "B", "walk", _leg(1200))
        upsert_route(session, "A", "B", "drive", _leg(600))
        session.commit()
        assert session.query(RouteCache).count() == 2

    def test_fresh_success_is_returned(self, session):
        upsert_route(session, "A", "B", "walk", _leg(1200), now=_days_ago(10))
        session.commit()
        fresh = get_fresh_route_cache(session, "walk", ["A", "B"], now=_utcnow())
        assert ("A", "B") in fresh

    def test_stale_success_is_excluded(self, session):
        upsert_route(session, "A", "B", "walk", _leg(1200), now=_days_ago(ROUTES_STALE_DAYS + 1))
        session.commit()
        fresh = get_fresh_route_cache(session, "walk", ["A", "B"], now=_utcnow())
        assert fresh == {}

    def test_recent_failure_is_fresh_within_retry_window(self, session):
        # NULL duration refreshed inside the retry window → still "fresh" (don't retry).
        upsert_route(session, "A", "B", "walk", None, now=_days_ago(ROUTES_RETRY_DAYS - 1))
        session.commit()
        fresh = get_fresh_route_cache(session, "walk", ["A", "B"], now=_utcnow())
        assert ("A", "B") in fresh

    def test_old_failure_is_retried(self, session):
        # NULL duration older than the retry window → excluded so it is re-fetched.
        upsert_route(session, "A", "B", "walk", None, now=_days_ago(ROUTES_RETRY_DAYS + 1))
        session.commit()
        fresh = get_fresh_route_cache(session, "walk", ["A", "B"], now=_utcnow())
        assert fresh == {}

    def test_fresh_filters_mode_and_place_ids(self, session):
        upsert_route(session, "A", "B", "walk", _leg(1200))
        upsert_route(session, "A", "B", "drive", _leg(600))
        upsert_route(session, "A", "Z", "walk", _leg(900))  # Z not in query set
        session.commit()
        fresh = get_fresh_route_cache(session, "walk", ["A", "B"], now=_utcnow())
        assert set(fresh) == {("A", "B")}

    def test_route_is_fresh_boundary(self, session):
        row = RouteCache(
            origin_place_id="A", dest_place_id="B", mode="walk",
            duration_seconds=100, distance_meters=100, refreshed_at=_days_ago(1),
        )
        assert route_is_fresh(row, _utcnow()) is True
        row.refreshed_at = _days_ago(ROUTES_STALE_DAYS + 1)
        assert route_is_fresh(row, _utcnow()) is False


# ---------------------------------------------------------------------------
# select_anchors
# ---------------------------------------------------------------------------

class TestSelectAnchors:
    def test_picks_highest_rated_member_with_place_id(self):
        clusters = [Cluster(0, "X", (1, 2, 3))]
        place_ids = {1: "pa", 2: "pb", 3: "pc"}
        ratings = {1: 4.0, 2: 4.8, 3: 4.5}
        anchors = select_anchors(clusters, place_ids, ratings)
        assert [(a.cluster_id, a.place_id) for a in anchors] == [(0, "pb")]

    def test_skips_members_without_place_id(self):
        clusters = [Cluster(0, "X", (1, 2))]
        anchors = select_anchors(clusters, {1: "pa", 2: None}, {1: 3.0, 2: 5.0})
        assert [(a.cluster_id, a.place_id) for a in anchors] == [(0, "pa")]

    def test_ties_break_to_lowest_option_id(self):
        clusters = [Cluster(0, "X", (1, 2))]
        anchors = select_anchors(clusters, {1: "pa", 2: "pb"}, {1: 4.0, 2: 4.0})
        assert [(a.cluster_id, a.place_id) for a in anchors] == [(0, "pa")]

    def test_cluster_without_any_place_id_is_dropped(self):
        clusters = [Cluster(0, "X", (1,)), Cluster(1, "Y", (2,))]
        anchors = select_anchors(clusters, {1: None, 2: "pb"})
        assert [(a.cluster_id, a.place_id) for a in anchors] == [(1, "pb")]

    def test_no_ratings_still_picks_a_member(self):
        clusters = [Cluster(0, "X", (5, 7))]
        anchors = select_anchors(clusters, {5: "p5", 7: "p7"})
        assert [(a.cluster_id, a.place_id) for a in anchors] == [(0, "p5")]

    def test_coords_by_option_populates_anchor_latlng(self):
        clusters = [Cluster(0, "X", (1, 2))]
        anchors = select_anchors(
            clusters,
            {1: "pa", 2: "pb"},
            {1: 4.0, 2: 4.8},
            coords_by_option={1: (35.6, 139.7), 2: (35.7, 139.8)},
        )
        # Member 2 is highest-rated → anchor "pb" with its coordinates.
        assert anchors[0].place_id == "pb"
        assert anchors[0].latitude == 35.7
        assert anchors[0].longitude == 139.8

    def test_missing_coords_leave_anchor_latlng_none(self):
        clusters = [Cluster(0, "X", (1,))]
        anchors = select_anchors(clusters, {1: "pa"})
        assert anchors[0].latitude is None
        assert anchors[0].longitude is None


# ---------------------------------------------------------------------------
# compute_inter_cluster_matrix
# ---------------------------------------------------------------------------

_ANCHORS = [ClusterAnchor(0, "A"), ClusterAnchor(1, "B"), ClusterAnchor(2, "C")]


class TestComputeMatrix:
    def test_first_call_hits_api_and_caches(self, session):
        client = MockRoutesClient(seconds_by_mode={"walk": 1200})
        matrix = compute_inter_cluster_matrix(
            session, _ANCHORS, client, modes=("walk",)
        )
        # 3 anchors → 6 ordered inter-cluster pairs, all present.
        assert len(matrix["walk"]) == 6
        assert matrix["walk"][(0, 1)]["duration_seconds"] == 1200
        # No self pairs.
        assert (0, 0) not in matrix["walk"]
        # One API call for the mode; rows persisted.
        assert len(client.calls) == 1
        assert session.query(RouteCache).count() == 6

    def test_second_call_is_served_from_cache(self, session):
        first = MockRoutesClient(seconds_by_mode={"walk": 1200})
        compute_inter_cluster_matrix(session, _ANCHORS, first, modes=("walk",))

        second = MockRoutesClient(seconds_by_mode={"walk": 9999})
        matrix = compute_inter_cluster_matrix(session, _ANCHORS, second, modes=("walk",))
        # Served entirely from cache → original durations, no second API call.
        assert matrix["walk"][(0, 1)]["duration_seconds"] == 1200
        assert second.calls == []

    def test_default_modes_compute_walk_and_drive(self, session):
        client = MockRoutesClient()
        matrix = compute_inter_cluster_matrix(session, _ANCHORS, client)
        assert set(matrix) == {"walk", "drive"}
        # One matrix call per mode.
        assert {mode for _, _, mode in client.calls} == {"walk", "drive"}

    def test_unreachable_pair_is_omitted_and_cached(self, session):
        client = MockRoutesClient(legs={("A", "B", "walk"): None})
        matrix = compute_inter_cluster_matrix(session, _ANCHORS, client, modes=("walk",))
        # The unreachable cluster pair is absent from the matrix...
        assert (0, 1) not in matrix["walk"]
        assert (1, 0) in matrix["walk"]
        # ...but cached as a negative so it isn't re-requested next time.
        repeat = MockRoutesClient()
        compute_inter_cluster_matrix(session, _ANCHORS, repeat, modes=("walk",))
        assert repeat.calls == []

    def test_api_failure_degrades_to_empty(self, session):
        client = MockRoutesClient(fail_modes={"walk"})
        matrix = compute_inter_cluster_matrix(session, _ANCHORS, client, modes=("walk",))
        assert matrix["walk"] == {}
        # Nothing cached → a later successful call still tries the API.
        assert session.query(RouteCache).count() == 0

    def test_no_client_no_coords_returns_empty(self, session):
        # Anchors have no coordinates → haversine fallback can't be built → empty.
        matrix = compute_inter_cluster_matrix(session, _ANCHORS, None, modes=("walk",))
        assert matrix["walk"] == {}

    def test_no_client_with_coords_falls_back_to_haversine(self, session):
        # Anchors carry lat/lng → haversine auto-built; matrix is populated.
        anchors = [
            ClusterAnchor(0, "A", 35.658, 139.745),  # Tokyo Tower area
            ClusterAnchor(1, "B", 35.660, 139.700),  # ~3 km west
        ]
        matrix = compute_inter_cluster_matrix(session, anchors, None, modes=("walk",))
        assert (0, 1) in matrix["walk"]
        assert matrix["walk"][(0, 1)]["duration_seconds"] > 0
        # Results cached — a second call with no client still serves from cache.
        matrix2 = compute_inter_cluster_matrix(session, anchors, None, modes=("walk",))
        assert matrix2["walk"][(0, 1)] == matrix["walk"][(0, 1)]

    def test_no_client_still_serves_existing_cache(self, session):
        upsert_route(session, "A", "B", "walk", _leg(1200))
        session.commit()
        matrix = compute_inter_cluster_matrix(session, _ANCHORS, None, modes=("walk",))
        assert matrix["walk"][(0, 1)]["duration_seconds"] == 1200

    def test_single_anchor_makes_no_call(self, session):
        client = MockRoutesClient()
        matrix = compute_inter_cluster_matrix(
            session, [ClusterAnchor(0, "A")], client, modes=("walk",)
        )
        assert matrix["walk"] == {}
        assert client.calls == []  # no pairs → no API

    def test_transit_mode_supported(self, session):
        client = MockRoutesClient(seconds_by_mode={"transit": 800})
        matrix = compute_inter_cluster_matrix(
            session, _ANCHORS, client, modes=("transit",)
        )
        assert matrix["transit"][(0, 1)]["duration_seconds"] == 800
