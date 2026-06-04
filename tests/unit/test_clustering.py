"""Unit tests for deterministic geographic clustering (no network, no DB).

Coordinates are chosen at ~35°N where 0.01° of latitude ≈ 1.11 km, so distances
are easy to reason about against the default 1.67 km (20-min walk) threshold.
"""

from __future__ import annotations

import pytest

from src.workers.planner import (
    Cluster,
    ClusterPoint,
    ScheduleItem,
    assign_clusters,
    cluster_points,
    haversine_km,
)
from src.workers.planner.clustering import (
    DEFAULT_MAX_DIAMETER_MINUTES,
    DEFAULT_WALK_MINUTES,
    WALK_SPEED_KMH,
    _name_cluster,
    walk_threshold_km,
)


def _point(
    option_id: int,
    *,
    lat=35.0,
    lng=139.0,
    name=None,
    neighborhood=None,
    google_rating=None,
) -> ClusterPoint:
    return ClusterPoint(
        option_id=option_id,
        name=name or f"Place {option_id}",
        latitude=lat,
        longitude=lng,
        neighborhood=neighborhood,
        google_rating=google_rating,
    )


# ---------------------------------------------------------------------------
# haversine + threshold
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(35.0, 139.0, 35.0, 139.0) == pytest.approx(0.0)

    def test_one_hundredth_degree_latitude_is_about_1_1_km(self):
        # 0.01° latitude ≈ 1.11 km — the basis for the threshold tests below.
        d = haversine_km(35.0, 139.0, 35.01, 139.0)
        assert d == pytest.approx(1.112, abs=0.02)

    def test_symmetric(self):
        a = haversine_km(35.0, 139.0, 35.2, 139.3)
        b = haversine_km(35.2, 139.3, 35.0, 139.0)
        assert a == pytest.approx(b)

    def test_default_threshold_(self):
        assert walk_threshold_km() == pytest.approx(1.6666666666666665)
        assert walk_threshold_km(DEFAULT_WALK_MINUTES, WALK_SPEED_KMH) == pytest.approx(1.6666666666666665)

    def test_default_diameter_cap_is_2x_link_threshold(self):
        assert DEFAULT_MAX_DIAMETER_MINUTES == DEFAULT_WALK_MINUTES * 2  # 40 min


# ---------------------------------------------------------------------------
# clustering geometry — link within threshold, split when distant
# ---------------------------------------------------------------------------

class TestClusterGeometry:
    def test_close_points_share_a_cluster_distant_ones_split(self):
        points = [
            _point(1, lat=35.000),          # A
            _point(2, lat=35.010),          # B ~1.11 km from A
            _point(3, lat=35.100),          # C ~11 km away
        ]
        clusters = cluster_points(points)
        members = {frozenset(c.member_ids) for c in clusters}
        assert members == {frozenset({1, 2}), frozenset({3})}

    def test_single_linkage_chains_transitively(self):
        # A-B and B-C each within threshold, but A-C is not (~2.22 km).
        # Single-linkage still puts all three in one cluster.
        points = [
            _point(1, lat=35.000),
            _point(2, lat=35.010),  # ~1.11 km from A
            _point(3, lat=35.020),  # ~1.11 km from B, ~2.22 km from A
        ]
        clusters = cluster_points(points)
        assert len(clusters) == 1
        assert clusters[0].member_ids == (1, 2, 3)

    def test_four_point_chain_diameter_cap_enforced(self):
        # A-B, B-C, C-D each ~1.11 km (within the 1.67 km / 20-min link threshold).
        # A-D is ~3.33 km ≈ 40 min walk — exceeds the 40-min / 3.34-km diameter cap.
        # The invariant: A (option_id=1) and D (option_id=4) must not share a cluster.
        # The exact partition ({A,B,C}+{D} or {A,B}+{C,D}) depends on floating-point
        # tie-breaking in the pair sort and is not specified here.
        points = [
            _point(1, lat=35.000),
            _point(2, lat=35.010),  # ~1.11 km from A
            _point(3, lat=35.020),  # ~1.11 km from B, ~2.22 km from A
            _point(4, lat=35.030),  # ~1.11 km from C, ~3.33 km from A
        ]
        clusters = cluster_points(points)
        # A and D must be in separate clusters.
        cluster_for = {oid: c.cluster_id for c in clusters for oid in c.member_ids}
        assert cluster_for[1] != cluster_for[4]
        # Every cluster must respect the diameter cap (no two members > 2.5 km apart).
        from src.workers.planner.clustering import walk_threshold_km
        cap_km = walk_threshold_km(30.0)
        for c in clusters:
            pts = [p for p in points if p.option_id in c.member_ids]
            for a in pts:
                for b in pts:
                    assert haversine_km(a.latitude, a.longitude, b.latitude, b.longitude) <= cap_km

    def test_four_point_chain_merges_when_cap_is_relaxed(self):
        # Same geometry, but with a 60-min diameter cap all four can merge.
        points = [
            _point(1, lat=35.000),
            _point(2, lat=35.010),
            _point(3, lat=35.020),
            _point(4, lat=35.030),
        ]
        clusters = cluster_points(points, max_diameter_minutes=60.0)
        assert len(clusters) == 1
        assert clusters[0].member_ids == (1, 2, 3, 4)

    def test_threshold_edge_just_inside_links(self):
        # ~1.11 km apart links at the default 1.25 km threshold...
        points = [_point(1, lat=35.000), _point(2, lat=35.010)]
        clusters = cluster_points(points)
        assert len(clusters) == 1

    def test_threshold_edge_just_outside_splits(self):
        # ...but with a tighter 10-min walk (~0.83 km) the same pair splits.
        points = [_point(1, lat=35.000), _point(2, lat=35.010)]
        clusters = cluster_points(points, walk_minutes=10.0)
        assert len(clusters) == 2

    def test_tunable_threshold_can_merge_distant_points(self):
        # A generous 60-min walk threshold (~5 km) merges points ~2.22 km apart.
        points = [_point(1, lat=35.000), _point(2, lat=35.020)]
        clusters = cluster_points(points, walk_minutes=60.0)
        assert len(clusters) == 1

    def test_empty_input(self):
        assert cluster_points([]) == []

    def test_single_point_is_one_cluster(self):
        clusters = cluster_points([_point(1)])
        assert len(clusters) == 1
        assert clusters[0].member_ids == (1,)


# ---------------------------------------------------------------------------
# null coordinates → singletons, never crashes
# ---------------------------------------------------------------------------

class TestNullCoordinates:
    def test_null_latlng_becomes_singleton(self):
        points = [
            _point(1, lat=35.000),
            _point(2, lat=35.005),               # near 1
            ClusterPoint(3, "No coords", None, None),
        ]
        clusters = cluster_points(points)
        members = {frozenset(c.member_ids) for c in clusters}
        assert frozenset({3}) in members
        assert frozenset({1, 2}) in members

    def test_multiple_null_points_do_not_merge(self):
        # Two coordinate-less options must NOT be lumped together — they could be
        # anywhere. Each is its own singleton.
        points = [
            ClusterPoint(1, "A", None, None),
            ClusterPoint(2, "B", None, None),
        ]
        clusters = cluster_points(points)
        assert len(clusters) == 2
        assert {frozenset(c.member_ids) for c in clusters} == {frozenset({1}), frozenset({2})}

    def test_partial_coords_treated_as_missing(self):
        points = [ClusterPoint(1, "Half", 35.0, None)]
        clusters = cluster_points(points)
        assert len(clusters) == 1
        assert clusters[0].member_ids == (1,)

    def test_null_point_keeps_its_neighborhood_name(self):
        points = [ClusterPoint(1, "A", None, None, neighborhood="Shibuya")]
        clusters = cluster_points(points)
        assert clusters[0].name == "Shibuya"


# ---------------------------------------------------------------------------
# cluster naming — modal neighborhood, tie-break, fallback
# ---------------------------------------------------------------------------

class TestClusterNaming:
    def test_modal_neighborhood_wins(self):
        members = [
            _point(1, neighborhood="Shinjuku"),
            _point(2, neighborhood="Shinjuku"),
            _point(3, neighborhood="Shibuya"),
        ]
        assert _name_cluster(members) == "Shinjuku"

    def test_members_without_neighborhood_are_ignored_for_mode(self):
        members = [
            _point(1, neighborhood=None),
            _point(2, neighborhood="Ueno"),
        ]
        assert _name_cluster(members) == "Ueno"

    def test_tie_breaks_to_highest_rated_member(self):
        members = [
            _point(1, neighborhood="Shinjuku", google_rating=4.0),
            _point(2, neighborhood="Shibuya", google_rating=4.6),
        ]
        # 1 vote each → tie → neighborhood of the higher-rated member.
        assert _name_cluster(members) == "Shibuya"

    def test_fallback_area_near_top_member_when_no_neighborhoods(self):
        members = [
            _point(1, name="Tokyo Tower", google_rating=4.5),
            _point(2, name="Some Cafe", google_rating=4.1),
        ]
        assert _name_cluster(members) == "Area near Tokyo Tower"

    def test_fallback_with_no_ratings_uses_lowest_option_id(self):
        members = [
            _point(1, name="First"),
            _point(2, name="Second"),
        ]
        # No ratings → top member ties resolve to the lowest option_id (pre-sorted).
        assert _name_cluster(members) == "Area near First"

    def test_naming_applies_end_to_end(self):
        # Three close points sharing a modal neighborhood.
        points = [
            _point(1, lat=35.000, neighborhood="Shinjuku"),
            _point(2, lat=35.005, neighborhood="Shinjuku"),
            _point(3, lat=35.010, neighborhood="Shibuya"),
        ]
        clusters = cluster_points(points)
        assert len(clusters) == 1
        assert clusters[0].name == "Shinjuku"


# ---------------------------------------------------------------------------
# deterministic ids + assign_clusters bridge
# ---------------------------------------------------------------------------

class TestDeterminismAndAssignment:
    def test_cluster_ids_ordered_by_lowest_member_option_id(self):
        points = [
            _point(5, lat=35.000),
            _point(2, lat=35.005),  # near 5 → cluster {2, 5}
            _point(1, lat=35.500),  # far → cluster {1}
        ]
        clusters = cluster_points(points)
        # Cluster {1} has the lowest min option_id → id 0; {2,5} → id 1.
        by_id = {c.cluster_id: c.member_ids for c in clusters}
        assert by_id[0] == (1,)
        assert by_id[1] == (2, 5)

    def test_repeated_runs_are_stable(self):
        points = [_point(3, lat=35.0), _point(1, lat=35.005), _point(2, lat=35.5)]
        assert cluster_points(points) == cluster_points(points)

    def test_assign_clusters_stamps_schedule_items(self):
        points = [
            _point(1, lat=35.000, neighborhood="Shinjuku"),
            _point(2, lat=35.005, neighborhood="Shinjuku"),
            _point(3, lat=35.500, neighborhood="Ginza"),
        ]
        clusters = cluster_points(points)
        items = [
            ScheduleItem(option_id=1, name="A", category="food", latitude=35.0, longitude=139.0, user_rating=4),
            ScheduleItem(option_id=2, name="B", category="food", latitude=35.005, longitude=139.0, user_rating=4),
            ScheduleItem(option_id=3, name="C", category="food", latitude=35.5, longitude=139.0, user_rating=4),
        ]
        assign_clusters(items, clusters)

        by_opt = {i.option_id: i for i in items}
        assert by_opt[1].cluster_id == by_opt[2].cluster_id
        assert by_opt[1].cluster_name == "Shinjuku"
        assert by_opt[3].cluster_id != by_opt[1].cluster_id
        assert by_opt[3].cluster_name == "Ginza"

    def test_assign_clusters_leaves_unmatched_items_untouched(self):
        clusters = [Cluster(cluster_id=0, name="X", member_ids=(1,))]
        item = ScheduleItem(option_id=99, name="Z", category="food", latitude=None, longitude=None, user_rating=3)
        assign_clusters([item], clusters)
        assert item.cluster_id is None
        assert item.cluster_name is None
