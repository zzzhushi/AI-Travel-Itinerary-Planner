"""Unit tests for Excel export."""

from src.tools.excel_export import research_results_to_excel


def test_export_empty() -> None:
    buf = research_results_to_excel([])
    assert isinstance(buf, bytes)
    assert len(buf) > 0


def test_export_with_rows() -> None:
    results = [
        {
            "activity_query": "night market",
            "option_name": "Gwangjang",
            "address": "88 Changgyeonggung-ro",
            "location": "Jongno-gu",
            "link": "https://example.com",
        },
    ]
    buf = research_results_to_excel(results)
    assert isinstance(buf, bytes)
    assert len(buf) > 100
