"""Haversine-based travel-time estimator — zero API calls.

Implements the same compute_matrix() interface as RoutesClient using straight-
line (haversine) distances and assumed travel speeds. Works offline with no
API key; suitable until a real routing API (Google Routes, OSRM, etc.) is
available.

Accuracy notes:
  - Haversine gives straight-line distance; actual road distance is typically
    1.2–1.4× longer in city grids. Durations will be *underestimates* by that
    factor — good enough for the LLM to order clusters by travel time, not for
    precise timetabling.
  - The same caveat applies to "transit": we have no network topology, so the
    estimate is purely distance-based. Real transit is often longer due to
    detours, waiting, and transfers.

Assumed speeds (km/h):
  walk:    5  (typical pedestrian planning assumption)
  drive:  30  (conservative city average with signals and traffic)
  transit: 20  (slower than driving: detours, stops, waiting time)

When swapping in a real routing client later, the caller only needs to change
which client it constructs — travel.py and the rest of the stack are unchanged.
"""

from __future__ import annotations

import math
from typing import Optional

# Mean Earth radius used by the haversine formula (km).
_EARTH_RADIUS_KM = 6371.0088

# Assumed travel speeds in km/h per mode.
_SPEED_KMH = {
    "walk": 5.0,
    "drive": 30.0,
    "transit": 20.0,
}

Coords = tuple[float, float]  # (latitude, longitude)


class HaversineRoutesClient:
    """Estimates inter-place travel times from lat/lng using haversine + fixed speeds.

    Args:
        coords_by_place_id: mapping of place_id -> (latitude, longitude).
            Place IDs not in this dict are silently skipped (no leg produced).
    """

    def __init__(self, coords_by_place_id: dict[str, Coords]) -> None:
        self._coords = coords_by_place_id

    def compute_matrix(
        self,
        origin_place_ids: list[str],
        dest_place_ids: list[str],
        mode: str,
    ) -> Optional[dict[tuple[str, str], Optional[dict]]]:
        """Return estimated travel legs for every routable origin×destination pair.

        Pairs where either endpoint has no coordinates in the lookup are omitted
        (same behaviour as the Routes API returning ROUTE_NOT_FOUND). Same-place
        pairs are always omitted. Returns an empty dict (not None) on bad mode —
        this is an estimation client, not a network call, so there is nothing to
        degrade from.
        """
        speed = _SPEED_KMH.get(mode)
        if speed is None:
            raise ValueError(f"unknown travel mode: {mode!r}")

        result: dict[tuple[str, str], Optional[dict]] = {}
        for origin in origin_place_ids:
            for dest in dest_place_ids:
                if origin == dest:
                    continue
                leg = self._leg(origin, dest, speed)
                if leg is not None:
                    result[(origin, dest)] = leg
        return result

    def close(self) -> None:
        pass

    def _leg(self, origin: str, dest: str, speed_kmh: float) -> Optional[dict]:
        oc = self._coords.get(origin)
        dc = self._coords.get(dest)
        if oc is None or dc is None:
            return None
        km = _haversine_km(oc[0], oc[1], dc[0], dc[1])
        seconds = int(round(km / speed_kmh * 3600))
        meters = int(round(km * 1000))
        return {"duration_seconds": seconds, "distance_meters": meters}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2.0 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))
