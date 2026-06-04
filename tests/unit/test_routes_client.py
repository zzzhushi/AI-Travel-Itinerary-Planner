"""Unit tests for RoutesClient response parsing (no network).

The HTTP layer (`_http.post`) is replaced with a recorder that returns canned
payloads, so these tests exercise request shaping + response parsing for both
the computeRouteMatrix path (walk/drive) and the per-pair computeRoutes transit
fallback, plus graceful failure handling.
"""

from __future__ import annotations

import httpx
import pytest

from src.clients.routes_client import (
    RoutesClient,
    _parse_duration,
    routes_client_from_env,
)


class _FakeResponse:
    def __init__(self, payload, raise_exc=None):
        self._payload = payload
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self):
        return self._payload


class _Recorder:
    """Stand-in for httpx.Client.post that records calls and returns canned data.

    `responder(url, json_body)` returns a _FakeResponse (or raises).
    """

    def __init__(self, responder):
        self.calls: list[tuple] = []
        self._responder = responder

    def __call__(self, url, headers=None, json=None):
        self.calls.append((url, json, headers))
        return self._responder(url, json)


def _client(responder) -> tuple[RoutesClient, _Recorder]:
    client = RoutesClient("test-key")
    recorder = _Recorder(responder)
    client._http.post = recorder  # type: ignore[assignment]
    return client, recorder


# ---------------------------------------------------------------------------
# _parse_duration
# ---------------------------------------------------------------------------

class TestParseDuration:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("123s", 123),
            ("123.4s", 123),
            ("0s", 0),
            ("60", 60),       # no trailing 's'
            (None, None),
            ("", None),
            ("abc", None),
        ],
    )
    def test_parse(self, value, expected):
        assert _parse_duration(value) == expected


# ---------------------------------------------------------------------------
# WALK / DRIVE — computeRouteMatrix
# ---------------------------------------------------------------------------

class TestComputeMatrix:
    def test_walk_matrix_parses_elements(self):
        elements = [
            {"originIndex": 0, "destinationIndex": 1, "duration": "1200s",
             "distanceMeters": 1000, "condition": "ROUTE_EXISTS"},
            {"originIndex": 1, "destinationIndex": 0, "duration": "1100s",
             "distanceMeters": 900, "condition": "ROUTE_EXISTS"},
            # Same place — must be skipped even if the API returns it.
            {"originIndex": 0, "destinationIndex": 0, "duration": "0s",
             "condition": "ROUTE_EXISTS"},
            # No route — cached as a negative (None leg).
            {"originIndex": 0, "destinationIndex": 2, "condition": "ROUTE_NOT_FOUND"},
        ]
        client, rec = _client(lambda url, body: _FakeResponse(elements))
        result = client.compute_matrix(["A", "B", "C"], ["A", "B", "C"], "walk")

        assert result[("A", "B")] == {"duration_seconds": 1200, "distance_meters": 1000}
        assert result[("B", "A")] == {"duration_seconds": 1100, "distance_meters": 900}
        assert ("A", "A") not in result
        assert result[("A", "C")] is None
        # One matrix request, hitting the matrix endpoint.
        assert len(rec.calls) == 1
        assert rec.calls[0][0].endswith(":computeRouteMatrix")

    def test_walk_body_has_no_routing_preference(self):
        client, rec = _client(lambda url, body: _FakeResponse([]))
        client.compute_matrix(["A", "B"], ["A", "B"], "walk")
        body = rec.calls[0][1]
        assert body["travelMode"] == "WALK"
        assert "routingPreference" not in body

    def test_drive_body_sets_traffic_aware(self):
        client, rec = _client(lambda url, body: _FakeResponse([]))
        client.compute_matrix(["A", "B"], ["A", "B"], "drive")
        body = rec.calls[0][1]
        assert body["travelMode"] == "DRIVE"
        assert body["routingPreference"] == "TRAFFIC_AWARE"
        # Waypoints carry placeId.
        assert body["origins"][0] == {"waypoint": {"placeId": "A"}}

    def test_matrix_request_failure_returns_none(self):
        def boom(url, body):
            return _FakeResponse(None, raise_exc=httpx.HTTPError("500"))

        client, _ = _client(boom)
        assert client.compute_matrix(["A", "B"], ["A", "B"], "drive") is None

    def test_empty_inputs_short_circuit(self):
        client, rec = _client(lambda url, body: _FakeResponse([]))
        assert client.compute_matrix([], ["A"], "walk") == {}
        assert client.compute_matrix(["A"], [], "walk") == {}
        assert rec.calls == []  # no HTTP at all

    def test_unknown_mode_raises(self):
        client, _ = _client(lambda url, body: _FakeResponse([]))
        with pytest.raises(ValueError):
            client.compute_matrix(["A"], ["B"], "fly")


# ---------------------------------------------------------------------------
# TRANSIT — per-pair computeRoutes fallback
# ---------------------------------------------------------------------------

class TestTransit:
    def test_transit_uses_per_pair_routes(self):
        def responder(url, body):
            return _FakeResponse({"routes": [{"duration": "900s", "distanceMeters": 1500}]})

        client, rec = _client(responder)
        result = client.compute_matrix(["A", "B"], ["A", "B"], "transit")

        assert result[("A", "B")] == {"duration_seconds": 900, "distance_meters": 1500}
        assert result[("B", "A")] == {"duration_seconds": 900, "distance_meters": 1500}
        # Two per-pair calls (A→B, B→A), each hitting the directions endpoint.
        assert len(rec.calls) == 2
        assert all(url.endswith(":computeRoutes") for url, _, _ in rec.calls)
        assert all(body["travelMode"] == "TRANSIT" for _, body, _ in rec.calls)

    def test_transit_no_route_is_cached_negative(self):
        client, _ = _client(lambda url, body: _FakeResponse({"routes": []}))
        result = client.compute_matrix(["A", "B"], ["A", "B"], "transit")
        # Reachability resolved (request succeeded) but no route → None, not failure.
        assert result == {("A", "B"): None, ("B", "A"): None}

    def test_transit_all_pairs_failing_returns_none(self):
        def boom(url, body):
            raise httpx.HTTPError("boom")

        client, _ = _client(boom)
        assert client.compute_matrix(["A", "B"], ["A", "B"], "transit") is None

    def test_transit_partial_failure_keeps_successes(self):
        def responder(url, body):
            # Fail only the route *to* B; succeed otherwise.
            if body["destination"]["waypoint"]["placeId"] == "B":
                raise httpx.HTTPError("boom")
            return _FakeResponse({"routes": [{"duration": "600s", "distanceMeters": 800}]})

        client, _ = _client(responder)
        result = client.compute_matrix(["A", "B"], ["A", "B"], "transit")
        # A→B errored → omitted entirely (left uncached); B→A succeeded.
        assert ("A", "B") not in result
        assert result[("B", "A")] == {"duration_seconds": 600, "distance_meters": 800}


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------

class TestFactory:
    def test_returns_none_without_key(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
        assert routes_client_from_env() is None

    def test_builds_client_with_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "abc123")
        client = routes_client_from_env()
        assert isinstance(client, RoutesClient)
        client.close()
