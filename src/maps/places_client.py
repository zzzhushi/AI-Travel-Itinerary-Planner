"""
Google Places API (New) client for enriching Option records with structured place data.

Uses Text Search to resolve a maps_search string (e.g. "Ichiran Ramen Shinjuku Tokyo")
to a real place, returning lat/lng, address, rating, hours, and other details.

Field mask stays in Essentials + Pro SKU tiers to avoid higher billing costs.
Do not add places.reviews or places.photos without checking the billing tier.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.googleMapsUri",
    "places.rating",
    "places.priceLevel",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.regularOpeningHours",
])


class PlacesClient:
    """Thin wrapper around the Places API (New) Text Search endpoint."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def lookup(self, maps_search: str) -> Optional[dict]:
        """Resolve a search string to a place dict, or None on no-match / error.

        Returns a dict with keys: place_id, latitude, longitude, maps_link,
        formatted_address, google_rating, price_level, phone_number, website,
        opening_hours (JSON-encoded weekday strings list).
        """
        if not maps_search:
            return None
        try:
            response = httpx.post(
                _TEXT_SEARCH_URL,
                headers={
                    "X-Goog-Api-Key": self._api_key,
                    "X-Goog-FieldMask": _FIELD_MASK,
                    "Content-Type": "application/json",
                },
                json={"textQuery": maps_search},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("Places API request failed for %r: %s", maps_search, exc)
            return None

        places = data.get("places") or []
        if not places:
            logger.debug("Places API returned no results for %r", maps_search)
            return None

        place = places[0]
        return _parse_place(place)


def _parse_place(place: dict) -> dict:
    location = place.get("location") or {}
    hours_data = place.get("regularOpeningHours") or {}
    weekday_descriptions = hours_data.get("weekdayDescriptions") or []

    return {
        "place_id": place.get("id"),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "maps_link": place.get("googleMapsUri"),
        "formatted_address": place.get("formattedAddress"),
        "google_rating": place.get("rating"),
        "price_level": _parse_price_level(place.get("priceLevel")),
        "phone_number": place.get("internationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "opening_hours": json.dumps(weekday_descriptions) if weekday_descriptions else None,
    }


def _parse_price_level(value: Optional[str]) -> Optional[int]:
    """Convert Places API PRICE_LEVEL_* enum string to 0–4 integer."""
    mapping = {
        "PRICE_LEVEL_FREE": 0,
        "PRICE_LEVEL_INEXPENSIVE": 1,
        "PRICE_LEVEL_MODERATE": 2,
        "PRICE_LEVEL_EXPENSIVE": 3,
        "PRICE_LEVEL_VERY_EXPENSIVE": 4,
    }
    return mapping.get(value) if value else None
