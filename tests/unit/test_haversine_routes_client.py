"""Unit tests for HaversineRoutesClient (pure geometry, no network)."""

from __future__ import annotations

import pytest

from src.clients.haversine_routes_client import HaversineRoutesClient, _haversine_km


# Three Tokyo anchors ~1–10 km apart.
_COORDS = {
    "tower":   (35.6586, 139.7454),  # Tokyo Tower
    "sensoji": (35.7148, 139.7967),  # Senso-ji (~8 km)
    "shibuya": (35.6595, 139.7004),  # Shibuya (~3.6 km from tower)
}


def _client(coords=None):
    return HaversineRoutesClient(coords if coords is not None else _COORDS)


class TestHaversineKm:
    def test_same_point_is_zero(self):
        assert _haversine_km(35.0, 139.0, 35.0, 139.0) == pytest.approx(0.0)

    def test_known_distance(self):
        # Tokyo Tower → Shibuya is roughly 3.6 km straight-line.
        d = _haversine_km(35.6586, 139.7454, 35.6595, 139.7004)
        assert 3.0 < d < 4.5

    def test_symmetric(self):
        a = _haversine_km(35.0, 139.0, 35.5, 139.5)
        b = _haversine_km(35.5, 139.5, 35.0, 139.0)
        assert a == pytest.approx(b)


class TestComputeMatrix:
    def test_walk_durations_are_positive(self):
        result = _client().compute_matrix(["tower", "sensoji"], ["tower", "sensoji"], "walk")
        assert result[("tower", "sensoji")]["duration_seconds"] > 0
        assert result[("sensoji", "tower")]["duration_seconds"] > 0

    def test_same_place_omitted(self):
        result = _client().compute_matrix(["tower"], ["tower"], "walk")
        assert result == {}

    def test_all_three_modes_produce_results(self):
        ids = list(_COORDS)
        for mode in ("walk", "drive", "transit"):
            result = _client().compute_matrix(ids, ids, mode)
            assert len(result) == 6  # 3×3 minus 3 self pairs

    def test_drive_faster_than_walk(self):
        result = _client().compute_matrix(["tower", "sensoji"], ["tower", "sensoji"], "walk")
        walk_t = result[("tower", "sensoji")]["duration_seconds"]
        result2 = _client().compute_matrix(["tower", "sensoji"], ["tower", "sensoji"], "drive")
        drive_t = result2[("tower", "sensoji")]["duration_seconds"]
        assert drive_t < walk_t

    def test_duration_consistent_with_speed_and_distance(self):
        # tower → shibuya: ~3.6 km at 5 km/h walk ≈ 43 min.
        result = _client().compute_matrix(["tower"], ["shibuya"], "walk")
        leg = result[("tower", "shibuya")]
        minutes = leg["duration_seconds"] / 60
        assert 35 < minutes < 55

    def test_distance_meters_is_positive_and_consistent(self):
        result = _client().compute_matrix(["tower"], ["sensoji"], "walk")
        leg = result[("tower", "sensoji")]
        assert leg["distance_meters"] > 0
        # meters ≈ seconds × speed (5 km/h = 1.389 m/s) — within 1%
        expected = leg["duration_seconds"] * (5000 / 3600)
        assert leg["distance_meters"] == pytest.approx(expected, rel=0.01)

    def test_missing_place_id_omits_leg(self):
        result = _client().compute_matrix(["tower", "unknown"], ["sensoji"], "walk")
        # tower→sensoji present; unknown→sensoji absent.
        assert ("tower", "sensoji") in result
        assert ("unknown", "sensoji") not in result

    def test_all_missing_returns_empty_dict(self):
        result = _client({}).compute_matrix(["A", "B"], ["A", "B"], "walk")
        assert result == {}

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            _client().compute_matrix(["tower"], ["sensoji"], "teleport")

    def test_empty_inputs_return_empty_dict(self):
        assert _client().compute_matrix([], ["tower"], "walk") == {}
        assert _client().compute_matrix(["tower"], [], "walk") == {}

    def test_close_is_a_noop(self):
        _client().close()  # must not raise


class TestIntegrationWithTravelMatrix:
    """Prove HaversineRoutesClient works end-to-end with compute_inter_cluster_matrix."""

    def test_matrix_with_haversine_client(self, session):
        from src.services.travel import ClusterAnchor, compute_inter_cluster_matrix

        anchors = [
            ClusterAnchor(0, "tower"),
            ClusterAnchor(1, "sensoji"),
            ClusterAnchor(2, "shibuya"),
        ]
        client = _client()
        matrix = compute_inter_cluster_matrix(session, anchors, client, modes=("walk", "drive"))

        # All 6 inter-cluster pairs present for each mode.
        assert len(matrix["walk"]) == 6
        assert len(matrix["drive"]) == 6
        # Closer clusters have shorter durations.
        assert matrix["walk"][(0, 2)]["duration_seconds"] < matrix["walk"][(0, 1)]["duration_seconds"]
        # Results cached to DB — second call makes zero client calls.
        from tests.mocks.routes_client import MockRoutesClient
        sentry = MockRoutesClient()
        compute_inter_cluster_matrix(session, anchors, sentry, modes=("walk", "drive"))
        assert sentry.calls == []
