"""Unit tests for CSV parser."""

import pytest
from src.tools.csv_parser import parse_activity_csv, ActivityRow, CsvParseError


def test_parse_single_column() -> None:
    content = "night market Seoul\nsalt bread\nGyeongbokgung"
    rows = parse_activity_csv(content)
    assert len(rows) == 3
    assert rows[0].activity == "night market Seoul"
    assert rows[0].preference == 3
    assert rows[1].activity == "salt bread"


def test_parse_two_columns() -> None:
    content = "activity,preference\nnight market,5\npalace,3"
    rows = parse_activity_csv(content)
    assert len(rows) == 2
    assert rows[0].activity == "night market"
    assert rows[0].preference == 5
    assert rows[1].preference == 3


def test_parse_empty() -> None:
    with pytest.raises(CsvParseError) as exc_info:
        parse_activity_csv("")
    assert "empty" in exc_info.value.message.lower()


def test_parse_no_valid_rows() -> None:
    content = "activity,preference\n\n  \n"
    with pytest.raises(CsvParseError) as exc_info:
        parse_activity_csv(content)
    assert "no valid" in exc_info.value.message.lower()


def test_preference_normalize() -> None:
    content = "a,high\nb,low\nc,medium"
    rows = parse_activity_csv(content)
    assert len(rows) >= 2
    # high -> 5, low -> 1, medium -> 3
    prefs = [r.preference for r in rows]
    assert all(1 <= p <= 5 for p in prefs)


def test_to_dict() -> None:
    row = ActivityRow("night market", 5)
    assert row.to_dict() == {"activity": "night market", "preference": 5}
