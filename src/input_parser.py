"""Parse trip/activity JSON strings; tolerate missing or partial fields."""

from __future__ import annotations

import json
from typing import Any


class InputParseError(Exception):
    """Invalid or empty input JSON."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def parse_trip_input(json_str: str) -> dict[str, Any]:
    """
    Parse a JSON string describing optional country/city and required activity(ies).

    **Only activity is required** for research: use ``activity`` (string) or ``activities``
    (list). ``country`` and ``city`` may be omitted; the research agent should infer
    location from the activity text when possible.

    Supported shapes:
      {"country": "...", "city": "...", "activity": "salt bread, seoul"}
      {"activity": "night market"}  # incomplete location
      {"activities": ["a", "b"], "city": "Seoul"}
    """
    if not json_str or not json_str.strip():
        raise InputParseError("Input is empty.")

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise InputParseError(f"Invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise InputParseError("JSON root must be an object.")

    out: dict[str, Any] = {}

    if "country" in data and data["country"] is not None:
        out["country"] = str(data["country"]).strip()
    if "city" in data and data["city"] is not None:
        out["city"] = str(data["city"]).strip()

    if "activities" in data and data["activities"] is not None:
        acts = data["activities"]
        if isinstance(acts, list):
            out["activities"] = [str(a).strip() for a in acts if str(a).strip()]
        else:
            out["activities"] = [str(acts).strip()] if str(acts).strip() else []
    elif "activity" in data and data["activity"] is not None:
        out["activities"] = [str(data["activity"]).strip()]
    else:
        out["activities"] = []

    return out


def activities_for_research(parsed: dict[str, Any]) -> list[str]:
    """Flatten activities list; use city/country hints in messages, not here."""
    return list(parsed.get("activities") or [])


def validate_has_activity(parsed: dict[str, Any]) -> None:
    """Raise if there is no activity to research (country/city are optional)."""
    if not activities_for_research(parsed):
        raise InputParseError(
            "Activity is required: set \"activity\" (string) or \"activities\" (list)."
        )
