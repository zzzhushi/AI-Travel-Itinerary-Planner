"""
Google Routes API client for inter-cluster travel times.

Computes travel duration/distance between a *small* set of cluster anchor
place_ids (K×K, K≈4–6) — never the full N×N of options. Uses the same
`X-Goog-Api-Key` + `X-Goog-FieldMask` + persistent `httpx.Client` pattern as
PlacesClient.

Two endpoints are used:
  * computeRouteMatrix (one request per mode) for WALK and DRIVE — returns the
    full origins×destinations matrix in a single streamed response.
  * computeRoutes (one request per pair) for TRANSIT — the matrix endpoint does
    not support transit, so we fall back to per-pair directions.

Modes are the lowercase strings used by route_cache ("walk" | "drive" |
"transit"); they are mapped to the Routes API travelMode enum internally.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

# Cache-side mode strings → Routes API travelMode enum.
WALK = "walk"
DRIVE = "drive"
TRANSIT = "transit"
_API_TRAVEL_MODE = {WALK: "WALK", DRIVE: "DRIVE", TRANSIT: "TRANSIT"}

# Field masks kept minimal — only what route_cache stores.
_MATRIX_FIELD_MASK = ",".join([
    "originIndex",
    "destinationIndex",
    "duration",
    "distanceMeters",
    "condition",
])
_ROUTES_FIELD_MASK = "routes.duration,routes.distanceMeters"

# A travel leg: reachable → {"duration_seconds": int, "distance_meters": int|None};
# confirmed unreachable → None.
Leg = Optional[dict]


class RoutesClient:
    """Thin wrapper around the Google Routes API matrix + directions endpoints."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        # Persistent connection pool, mirroring PlacesClient: reuses sockets
        # across the per-mode matrix calls instead of re-handshaking each time.
        self._http = httpx.Client(timeout=15.0)

    def close(self) -> None:
        """Release the connection pool (httpx auto-closes idle conns otherwise)."""
        self._http.close()

    def compute_matrix(
        self,
        origin_place_ids: list[str],
        dest_place_ids: list[str],
        mode: str,
    ) -> Optional[dict[tuple[str, str], Leg]]:
        """Compute travel legs for every origin×destination pair in one mode.

        Returns a dict mapping (origin_place_id, dest_place_id) → leg, where a
        leg is a {"duration_seconds", "distance_meters"} dict for a reachable
        pair or None for a confirmed-unreachable pair. Same-place pairs
        (origin == destination) are omitted.

        Returns ``None`` (not an empty dict) when the API call fails outright, so
        the caller can distinguish "all pairs unreachable" (cache the negatives)
        from "request failed" (degrade gracefully, keep existing cache).
        """
        if mode not in _API_TRAVEL_MODE:
            raise ValueError(f"unknown travel mode: {mode!r}")
        if not origin_place_ids or not dest_place_ids:
            return {}
        if mode == TRANSIT:
            return self._compute_transit(origin_place_ids, dest_place_ids)
        return self._compute_route_matrix(origin_place_ids, dest_place_ids, mode)

    # -- WALK / DRIVE: single computeRouteMatrix call ------------------------

    def _compute_route_matrix(
        self, origins: list[str], destinations: list[str], mode: str
    ) -> Optional[dict[tuple[str, str], Leg]]:
        body: dict = {
            "origins": [_waypoint(p) for p in origins],
            "destinations": [_waypoint(p) for p in destinations],
            "travelMode": _API_TRAVEL_MODE[mode],
        }
        # routingPreference is only valid for DRIVE; sending it for WALK errors.
        if mode == DRIVE:
            body["routingPreference"] = "TRAFFIC_AWARE"
        try:
            response = self._http.post(
                _MATRIX_URL,
                headers={
                    "X-Goog-Api-Key": self._api_key,
                    "X-Goog-FieldMask": _MATRIX_FIELD_MASK,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            elements = response.json()
        except Exception as exc:
            logger.warning("Routes matrix request failed (mode=%s): %s", mode, exc)
            return None

        # computeRouteMatrix returns a flat list of elements, one per pair, each
        # carrying its originIndex / destinationIndex back into the input arrays.
        result: dict[tuple[str, str], Leg] = {}
        for element in elements or []:
            oi = element.get("originIndex")
            di = element.get("destinationIndex")
            if oi is None or di is None:
                continue
            origin, dest = origins[oi], destinations[di]
            if origin == dest:
                continue
            if element.get("condition") == "ROUTE_EXISTS":
                result[(origin, dest)] = {
                    "duration_seconds": _parse_duration(element.get("duration")),
                    "distance_meters": element.get("distanceMeters"),
                }
            else:
                # ROUTE_NOT_FOUND (or missing condition) → confirmed unreachable.
                result[(origin, dest)] = None
        return result

    # -- TRANSIT: per-pair computeRoutes calls ------------------------------

    def _compute_transit(
        self, origins: list[str], destinations: list[str]
    ) -> Optional[dict[tuple[str, str], Leg]]:
        result: dict[tuple[str, str], Leg] = {}
        attempted = 0
        any_ok = False
        for origin in origins:
            for dest in destinations:
                if origin == dest:
                    continue
                attempted += 1
                leg = self._compute_route_pair(origin, dest)
                if leg is _FAILED:
                    continue  # request errored — leave this pair uncached
                any_ok = True
                result[(origin, dest)] = leg
        # If we attempted at least one pair and every one errored, treat the whole
        # call as a failure so the caller falls back to cache instead of caching
        # spurious negatives. (No real pairs attempted → return the empty dict.)
        if attempted and not any_ok:
            return None
        return result

    def _compute_route_pair(self, origin: str, dest: str) -> Leg:
        """Return a leg, None (no route), or the _FAILED sentinel on error."""
        try:
            response = self._http.post(
                _ROUTES_URL,
                headers={
                    "X-Goog-Api-Key": self._api_key,
                    "X-Goog-FieldMask": _ROUTES_FIELD_MASK,
                    "Content-Type": "application/json",
                },
                json={
                    "origin": _waypoint(origin),
                    "destination": _waypoint(dest),
                    "travelMode": "TRANSIT",
                },
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("Routes transit request failed (%s->%s): %s", origin, dest, exc)
            return _FAILED

        routes = data.get("routes") or []
        if not routes:
            return None  # no transit route exists between this pair
        route = routes[0]
        return {
            "duration_seconds": _parse_duration(route.get("duration")),
            "distance_meters": route.get("distanceMeters"),
        }


# Sentinel distinguishing a per-pair request failure from a real "no route"
# (None). Module-private; never stored or returned to callers of compute_matrix.
_FAILED = object()


def routes_client_from_env() -> Optional[RoutesClient]:
    """Build a RoutesClient from GOOGLE_MAPS_API_KEY, or None if it is unset.

    Uses the same key as PlacesClient (the Routes API and Places API (New) are
    billed under one Google Maps Platform key), so enabling Maps enrichment also
    enables travel-time computation.
    """
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    return RoutesClient(key) if key else None


def _waypoint(place_id: str) -> dict:
    return {"waypoint": {"placeId": place_id}}


def _parse_duration(value: Optional[str]) -> Optional[int]:
    """Parse a Routes API duration string like '123s' into integer seconds."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return int(round(float(text)))
    except ValueError:
        logger.debug("Could not parse Routes duration %r", value)
        return None
