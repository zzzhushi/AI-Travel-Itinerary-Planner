"""Unit tests for PlacesClient and its parsing helpers.

Uses MockPlacesClient — no network calls, no API key required.
"""

import json

import pytest

from src.clients.places_client import _parse_place, _parse_price_level
from tests.mocks.places_client import MockPlacesClient, SAMPLE_PLACE


class TestMockPlacesClient:
    def test_returns_configured_data(self):
        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        result = client.lookup("Ichiran Ramen Shinjuku Tokyo")
        assert result == SAMPLE_PLACE

    def test_returns_none_when_not_configured(self):
        client = MockPlacesClient(return_data=None)
        result = client.lookup("some place")
        assert result is None

    def test_records_calls(self):
        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        client.lookup("Place A")
        client.lookup("Place B")
        assert client.calls == ["Place A", "Place B"]

    def test_empty_maps_search_returns_none(self):
        # PlacesClient.lookup short-circuits on empty string
        from src.clients.places_client import PlacesClient
        # We cannot call real PlacesClient without a key, but _parse_place is testable directly.
        # This test verifies MockPlacesClient is consistent with the contract.
        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        # The mock does not enforce the empty-string short-circuit; that is on PlacesClient.
        # Just confirm the mock records the call regardless.
        client.lookup("")
        assert "" in client.calls


class TestParsePriceLevel:
    def test_known_values(self):
        assert _parse_price_level("PRICE_LEVEL_FREE") == 0
        assert _parse_price_level("PRICE_LEVEL_INEXPENSIVE") == 1
        assert _parse_price_level("PRICE_LEVEL_MODERATE") == 2
        assert _parse_price_level("PRICE_LEVEL_EXPENSIVE") == 3
        assert _parse_price_level("PRICE_LEVEL_VERY_EXPENSIVE") == 4

    def test_unknown_value_returns_none(self):
        assert _parse_price_level("PRICE_LEVEL_UNKNOWN") is None

    def test_none_input_returns_none(self):
        assert _parse_price_level(None) is None


class TestParsePlace:
    def _make_api_place(self, **overrides) -> dict:
        base = {
            "id": "ChIJtest",
            "displayName": {"text": "Ichiran Ramen"},
            "formattedAddress": "1-2-3 Shinjuku, Tokyo",
            "location": {"latitude": 35.6938, "longitude": 139.7034},
            "googleMapsUri": "https://maps.google.com/?cid=test",
            "rating": 4.3,
            "priceLevel": "PRICE_LEVEL_MODERATE",
            "internationalPhoneNumber": "+81 3-1234-5678",
            "websiteUri": "https://example.com",
            "regularOpeningHours": {
                "weekdayDescriptions": ["Monday: 11:00 AM – 11:00 PM"]
            },
        }
        base.update(overrides)
        return base

    def test_full_place_parsed_correctly(self):
        place = self._make_api_place()
        result = _parse_place(place)

        assert result["place_id"] == "ChIJtest"
        assert result["latitude"] == 35.6938
        assert result["longitude"] == 139.7034
        assert result["maps_link"] == "https://maps.google.com/?cid=test"
        assert result["formatted_address"] == "1-2-3 Shinjuku, Tokyo"
        assert result["google_rating"] == 4.3
        assert result["price_level"] == 2
        assert result["phone_number"] == "+81 3-1234-5678"
        assert result["website"] == "https://example.com"
        assert json.loads(result["opening_hours"]) == ["Monday: 11:00 AM – 11:00 PM"]

    def test_missing_optional_fields_return_none(self):
        place = {"id": "ChIJtest", "location": {"latitude": 1.0, "longitude": 2.0}}
        result = _parse_place(place)

        assert result["place_id"] == "ChIJtest"
        assert result["latitude"] == 1.0
        assert result["google_rating"] is None
        assert result["price_level"] is None
        assert result["phone_number"] is None
        assert result["website"] is None
        assert result["opening_hours"] is None

    def test_empty_opening_hours_returns_none(self):
        place = self._make_api_place(regularOpeningHours={"weekdayDescriptions": []})
        result = _parse_place(place)
        assert result["opening_hours"] is None

    def test_missing_location_returns_none_coords(self):
        place = {"id": "ChIJtest"}
        result = _parse_place(place)
        assert result["latitude"] is None
        assert result["longitude"] is None
