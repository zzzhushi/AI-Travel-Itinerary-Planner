"""Unit tests for trip parser."""

import pytest
from src.tools.trip_parser import parse_trip_file, TripInfo, TripParseError


def test_parse_valid_json() -> None:
    content = '{"country": "South Korea", "city": "Seoul", "days": 5}'
    trip = parse_trip_file(content)
    assert isinstance(trip, TripInfo)
    assert trip.country == "South Korea"
    assert trip.city == "Seoul"
    assert trip.days == 5
    assert trip.flight_info == {}


def test_parse_json_with_flight() -> None:
    content = """{"country": "Japan", "city": "Tokyo", "days": 3,
        "flight_info": {"arrival": "2025-03-10T14:00"}}"""
    trip = parse_trip_file(content)
    assert trip.country == "Japan"
    assert trip.city == "Tokyo"
    assert trip.days == 3
    assert trip.flight_info.get("arrival") == "2025-03-10T14:00"


def test_parse_missing_country() -> None:
    content = '{"city": "Seoul", "days": 5}'
    with pytest.raises(TripParseError) as exc_info:
        parse_trip_file(content)
    assert "country" in exc_info.value.message.lower()


def test_parse_missing_days() -> None:
    content = '{"country": "Korea", "city": "Seoul"}'
    with pytest.raises(TripParseError) as exc_info:
        parse_trip_file(content)
    assert "days" in exc_info.value.message.lower()


def test_parse_invalid_days() -> None:
    content = '{"country": "Korea", "city": "Seoul", "days": "five"}'
    with pytest.raises(TripParseError):
        parse_trip_file(content)


def test_parse_empty() -> None:
    with pytest.raises(TripParseError) as exc_info:
        parse_trip_file("")
    assert "empty" in exc_info.value.message.lower()


def test_to_dict() -> None:
    trip = TripInfo("Korea", "Seoul", 4, {"arrival": "10:00"})
    d = trip.to_dict()
    assert d["country"] == "Korea"
    assert d["city"] == "Seoul"
    assert d["days"] == 4
    assert d["flight_info"]["arrival"] == "10:00"
