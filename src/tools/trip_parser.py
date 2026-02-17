"""Parse trip info file (JSON or YAML). Required: country, city, days."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class TripParseError(Exception):
    """Raised when trip file is invalid or missing required fields."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class TripInfo:
    """Structured trip information."""

    def __init__(
        self,
        country: str,
        city: str,
        days: int,
        flight_info: dict[str, Any] | None = None,
    ) -> None:
        self.country = country
        self.city = city
        self.days = days
        self.flight_info = flight_info or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "city": self.city,
            "days": self.days,
            "flight_info": self.flight_info,
        }


def parse_trip_file(content: bytes | str, filename: str = "") -> TripInfo:
    """
    Parse trip info from file content (JSON or YAML).
    Required fields: country, city, days.
    """
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    content = content.strip()
    if not content:
        raise TripParseError("Trip file is empty.")

    ext = Path(filename).suffix.lower() if filename else ""
    if ext in (".yaml", ".yml"):
        if not HAS_YAML:
            raise TripParseError("YAML support requires pyyaml. Install with: pip install pyyaml")
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise TripParseError(f"Invalid YAML: {e}") from e
    else:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise TripParseError(f"Invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise TripParseError("Trip file must be a JSON/YAML object.")

    country = data.get("country")
    city = data.get("city")
    days = data.get("days")

    if not country or not isinstance(country, str):
        raise TripParseError("Missing required field: country")
    if not city or not isinstance(city, str):
        raise TripParseError("Missing required field: city")
    if days is None:
        raise TripParseError("Missing required field: days")
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise TripParseError("Field 'days' must be an integer")
    if days < 1:
        raise TripParseError("Field 'days' must be at least 1")

    flight_info = data.get("flight_info")
    if flight_info is not None and not isinstance(flight_info, dict):
        flight_info = {}

    return TripInfo(
        country=str(country).strip(),
        city=str(city).strip(),
        days=days,
        flight_info=flight_info if isinstance(flight_info, dict) else None,
    )
