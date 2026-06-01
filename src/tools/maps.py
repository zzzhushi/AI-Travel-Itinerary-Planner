"""
Google Maps utilities: geocoding, place details, distance matrix.
Falls back gracefully when GOOGLE_MAPS_API_KEY is not set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

try:
    import googlemaps
    _GMAPS_AVAILABLE = True
except ImportError:
    _GMAPS_AVAILABLE = False


@dataclass
class PlaceInfo:
    name: str
    address: str
    latitude: Optional[float]
    longitude: Optional[float]
    maps_link: str
    place_id: Optional[str] = None


def _client():
    key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key or not _GMAPS_AVAILABLE:
        return None
    return googlemaps.Client(key=key)


def get_place_info(search_query: str) -> Optional[PlaceInfo]:
    """
    Look up a place by search string.
    Returns PlaceInfo with lat/lng and a Maps link, or None if unavailable.
    """
    client = _client()
    if client is None:
        return None

    try:
        results = client.find_place(
            input=search_query,
            input_type="textquery",
            fields=["name", "formatted_address", "geometry", "place_id"],
        )
        candidates = results.get("candidates", [])
        if not candidates:
            return None

        place = candidates[0]
        loc = place.get("geometry", {}).get("location", {})
        lat = loc.get("lat")
        lng = loc.get("lng")
        place_id = place.get("place_id", "")
        maps_link = (
            f"https://www.google.com/maps/place/?q=place_id:{place_id}"
            if place_id
            else f"https://www.google.com/maps/search/?api=1&query={search_query.replace(' ', '+')}"
        )

        return PlaceInfo(
            name=place.get("name", search_query),
            address=place.get("formatted_address", ""),
            latitude=lat,
            longitude=lng,
            maps_link=maps_link,
            place_id=place_id,
        )
    except Exception:
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance in km between two lat/lng points."""
    import math

    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def maps_search_link(query: str) -> str:
    """Fallback Maps link when we don't have a place_id."""
    return f"https://www.google.com/maps/search/?api=1&query={query.replace(' ', '+')}"
