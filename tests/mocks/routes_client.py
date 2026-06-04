"""Mock RoutesClient for unit tests — no network calls.

Returns canned travel legs computed from a flat per-mode "seconds between any two
distinct anchors" rule, so tests can assert on durations without hardcoding a
full matrix. Records every compute_matrix() call so tests can prove the cache
short-circuits the API on repeat lookups.
"""

from __future__ import annotations

from typing import Optional

# Default fabricated travel seconds per mode for any distinct origin/dest pair.
_DEFAULT_SECONDS = {"walk": 1200, "drive": 600, "transit": 900}


class MockRoutesClient:
    """Canned RoutesClient.

    Args:
        seconds_by_mode: override the per-mode fabricated duration.
        legs: explicit {(origin, dest, mode): leg-or-None} overrides; a value of
            None marks that pair unreachable. Pairs not listed fall back to
            seconds_by_mode.
        fail_modes: modes for which compute_matrix() returns None (simulating a
            hard API failure → graceful degradation).
    """

    def __init__(
        self,
        seconds_by_mode: Optional[dict] = None,
        legs: Optional[dict] = None,
        fail_modes: Optional[set] = None,
    ) -> None:
        self._seconds = {**_DEFAULT_SECONDS, **(seconds_by_mode or {})}
        self._legs = legs or {}
        self._fail_modes = fail_modes or set()
        # One entry per compute_matrix() call: (origins, destinations, mode).
        self.calls: list[tuple] = []

    def compute_matrix(
        self,
        origin_place_ids: list[str],
        dest_place_ids: list[str],
        mode: str,
    ) -> Optional[dict]:
        self.calls.append((list(origin_place_ids), list(dest_place_ids), mode))
        if mode in self._fail_modes:
            return None
        result: dict[tuple[str, str], Optional[dict]] = {}
        for origin in origin_place_ids:
            for dest in dest_place_ids:
                if origin == dest:
                    continue
                if (origin, dest, mode) in self._legs:
                    result[(origin, dest)] = self._legs[(origin, dest, mode)]
                else:
                    result[(origin, dest)] = {
                        "duration_seconds": self._seconds[mode],
                        "distance_meters": self._seconds[mode],  # 1 m/s, arbitrary
                    }
        return result

    def close(self) -> None:
        pass
