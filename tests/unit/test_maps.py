"""Unit tests for pure utility functions in src/tools/maps.py.

No external API calls — only haversine_km and maps_search_link are tested here.
get_place_info requires a live Google Maps client and is not tested in unit tests.
"""

import pytest

from src.tools.maps import haversine_km, maps_search_link


class TestHaversineKm:
    def test_same_point_is_zero(self):
        # Distance from a point to itself must be 0
        assert haversine_km(48.8566, 2.3522, 48.8566, 2.3522) == 0.0

    def test_paris_to_london(self):
        # Paris (48.8566, 2.3522) → London (51.5074, -0.1278) ≈ 341 km
        dist = haversine_km(48.8566, 2.3522, 51.5074, -0.1278)
        assert 335 < dist < 345, f"Expected ~341 km, got {dist:.1f}"

    def test_symmetry(self):
        # Distance A→B must equal distance B→A
        d1 = haversine_km(35.6762, 139.6503, 37.5665, 126.9780)  # Tokyo → Seoul
        d2 = haversine_km(37.5665, 126.9780, 35.6762, 139.6503)  # Seoul → Tokyo
        assert abs(d1 - d2) < 0.001

    def test_short_distance(self):
        # Two points ~1 km apart in NYC
        dist = haversine_km(40.7484, -73.9967, 40.7580, -73.9855)
        assert 1.0 < dist < 2.0


class TestMapsSearchLink:
    def test_returns_string(self):
        # Output must always be a non-empty string
        link = maps_search_link("Eiffel Tower Paris")
        assert isinstance(link, str)
        assert len(link) > 0

    def test_contains_encoded_query(self):
        # Query terms should appear in the URL (possibly URL-encoded)
        link = maps_search_link("Ichiran Ramen Tokyo")
        # spaces become + or %20; the words themselves should appear
        assert "Ichiran" in link or "ichiran" in link.lower()

    def test_spaces_encoded(self):
        # Spaces must not appear literally in the URL
        link = maps_search_link("night market Seoul")
        assert " " not in link
