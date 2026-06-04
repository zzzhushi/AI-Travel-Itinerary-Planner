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
import os
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
    # addressComponents carries the neighborhood / sublocality (e.g. "Shinjuku"),
    # used to name geographic clusters when scheduling. It sits in a lower billing
    # tier than the rating / opening-hours fields already requested above, so it
    # does not raise the Text Search SKU tier (billing = highest tier requested).
    "places.addressComponents",
])


class PlacesClient:
    """Thin wrapper around the Places API (New) Text Search endpoint."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        # Persistent HTTP client: keeps a connection pool alive across calls so
        # concurrent lookup() calls (via run_in_executor) reuse sockets instead
        # of opening a new TCP+TLS handshake each time. Under HTTP/1.1 each
        # in-flight request owns one socket (1:1); under HTTP/2 multiple
        # requests multiplex over a single socket. Either way, sockets are
        # returned to the pool after each response and reused by the next call.
        # Idle connections auto-close after keepalive_expiry (default 5 s); call
        # close() explicitly when done to release file descriptors immediately.
        self._http = httpx.Client(timeout=10.0)

    def close(self) -> None:
        """Release the connection pool.

        If not called, httpx auto-closes idle connections after keepalive_expiry
        (default 5 s), so file descriptors are not held indefinitely — but
        calling this explicitly is preferable in a long-running server process.
        """
        self._http.close()

    def lookup(self, maps_search: str) -> Optional[dict]:
        """Resolve a search string to a place dict, or None on no-match / error.

        Returns a dict with keys: place_id, latitude, longitude, maps_link,
        formatted_address, neighborhood, google_rating, price_level,
        phone_number, website, opening_hours (JSON-encoded weekday strings list).
        """
        if not maps_search:
            return None
        try:
            response = self._http.post(
                _TEXT_SEARCH_URL,
                headers={
                    "X-Goog-Api-Key": self._api_key,
                    "X-Goog-FieldMask": _FIELD_MASK,
                    "Content-Type": "application/json",
                },
                json={"textQuery": maps_search},
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


def places_client_from_env() -> Optional[PlacesClient]:
    """Build a PlacesClient from GOOGLE_MAPS_API_KEY, or None if it is unset.

    Shared by the CLI and the web app so both decide on enrichment the same way:
    enrichment runs only when a Maps key is configured.
    """
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    return PlacesClient(key) if key else None


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
        "neighborhood": _parse_neighborhood(place.get("addressComponents")),
        "google_rating": place.get("rating"),
        "price_level": _parse_price_level(place.get("priceLevel")),
        "phone_number": place.get("internationalPhoneNumber"),
        "website": place.get("websiteUri"),
        "opening_hours": json.dumps(weekday_descriptions) if weekday_descriptions else None,
    }


# Address-component types that name a neighborhood, most specific first. Google
# tags wards/districts (e.g. Tokyo's "Shinjuku") as sublocality_level_1, while a
# true "neighborhood" type is rarer — so we prefer the former where present.
_NEIGHBORHOOD_TYPES = (
    "neighborhood",
    "sublocality_level_1",
    "sublocality",
    "sublocality_level_2",
)


def _parse_neighborhood(components: Optional[list]) -> Optional[str]:
    """Pick the most specific neighborhood-like name from Places addressComponents.

    Each component carries a `types` list (e.g. ["sublocality_level_1",
    "sublocality", "political"]). We map type → text (first occurrence wins) and
    return the highest-priority match per _NEIGHBORHOOD_TYPES, or None if the
    response carried no neighborhood-like component.
    """
    text_by_type: dict[str, str] = {}
    for comp in components or []:
        text = comp.get("longText") or comp.get("shortText")
        if not text:
            continue
        for type_name in comp.get("types") or []:
            text_by_type.setdefault(type_name, text)
    for type_name in _NEIGHBORHOOD_TYPES:
        if type_name in text_by_type:
            return text_by_type[type_name]
    return None


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
