"""Unit tests: Excel and planning JSON export."""

from src.excel_export import rows_to_excel, rows_to_planning_json


def test_excel_nonempty() -> None:
    rows = [
        {
            "activity_query": "night market",
            "option_name": "Gwangjang",
            "address": "88 Changgyeonggung-ro",
            "location": "Jongno-gu",
            "link": "https://example.com",
            "user_rating": "",
        }
    ]
    data = rows_to_excel(rows)
    assert isinstance(data, bytes)
    assert len(data) > 100
    assert b"PK" in data[:4]  # xlsx zip signature


def test_planning_json_roundtrip() -> None:
    rows = [{"activity_query": "a", "option_name": "b", "address": "", "location": "", "link": "", "user_rating": ""}]
    s = rows_to_planning_json(rows)
    assert "activity_query" in s
    assert "a" in s
