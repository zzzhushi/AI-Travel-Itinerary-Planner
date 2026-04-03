"""Unit tests: trip/activity JSON of varying quality."""

import json
import pytest

from src.input_parser import (
    InputParseError,
    parse_trip_input,
    activities_for_research,
    validate_has_activity,
)


def test_full_object() -> None:
    raw = '{"country": "South Korea", "city": "Seoul", "activity": "night market"}'
    p = parse_trip_input(raw)
    assert p["country"] == "South Korea"
    assert p["city"] == "Seoul"
    assert p["activities"] == ["night market"]
    validate_has_activity(p)


def test_partial_activity_only() -> None:
    """Incomplete location like prompt example."""
    raw = '{"activity": "salt bread, seoul"}'
    p = parse_trip_input(raw)
    assert "country" not in p or p.get("country") == ""
    assert p["activities"] == ["salt bread, seoul"]
    validate_has_activity(p)


def test_vague_string_in_activity() -> None:
    raw = json.dumps({"activity": "I want to go to a night market in Seoul"})
    p = parse_trip_input(raw)
    assert len(p["activities"]) == 1
    assert "Seoul" in p["activities"][0] or "seoul" in p["activities"][0].lower()


def test_activities_list() -> None:
    raw = '{"city": "Tokyo", "activities": ["ramen", "temple"]}'
    p = parse_trip_input(raw)
    assert p["city"] == "Tokyo"
    assert activities_for_research(p) == ["ramen", "temple"]


def test_whitespace_trimmed() -> None:
    raw = '  {  "activity"  :  "  cafe  "  }  '
    p = parse_trip_input(raw)
    assert p["activities"] == ["cafe"]


def test_empty_json_object() -> None:
    raw = "{}"
    p = parse_trip_input(raw)
    assert p["activities"] == []
    with pytest.raises(InputParseError):
        validate_has_activity(p)


def test_empty_string_raises() -> None:
    with pytest.raises(InputParseError) as e:
        parse_trip_input("")
    assert "empty" in e.value.message.lower()


def test_invalid_json_raises() -> None:
    with pytest.raises(InputParseError) as e:
        parse_trip_input("{not json")
    assert "Invalid JSON" in e.value.message or "json" in e.value.message.lower()


def test_array_root_raises() -> None:
    with pytest.raises(InputParseError):
        parse_trip_input("[1,2,3]")


def test_missing_activity_raises_validation() -> None:
    p = parse_trip_input('{"country": "Korea"}')
    with pytest.raises(InputParseError):
        validate_has_activity(p)
