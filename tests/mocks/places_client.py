"""Mock PlacesClient for unit tests — no network calls."""

from __future__ import annotations

import json
from typing import Optional


SAMPLE_PLACE = {
    "place_id": "ChIJtest1234",
    "latitude": 35.6938,
    "longitude": 139.7034,
    "maps_link": "https://maps.google.com/?cid=test",
    "formatted_address": "1-2-3 Shinjuku, Shinjuku City, Tokyo",
    "google_rating": 4.3,
    "price_level": 2,
    "phone_number": "+81 3-1234-5678",
    "website": "https://example.com",
    "opening_hours": json.dumps([
        "Monday: 11:00 AM – 11:00 PM",
        "Tuesday: 11:00 AM – 11:00 PM",
    ]),
}


class MockPlacesClient:
    """Returns canned place data or None based on configuration.

    Args:
        return_data: dict to return from lookup(), or None to simulate no-match.
    """

    def __init__(self, return_data: Optional[dict] = None) -> None:
        self._return_data = return_data
        self.calls: list[str] = []

    def lookup(self, maps_search: str) -> Optional[dict]:
        self.calls.append(maps_search)
        return self._return_data

    def close(self) -> None:
        pass
