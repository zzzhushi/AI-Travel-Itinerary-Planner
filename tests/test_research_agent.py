"""Unit tests for research_agent: parsing helpers, message building, and mocked run_research."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.research_agent import (
    RESEARCH_INSTRUCTION,
    _extract_json_array,
    _normalize_rows,
    build_user_message,
    run_research,
)


# --- build_user_message: partial input; only activities required ---


def test_build_user_message_full_context() -> None:
    parsed = {
        "country": "South Korea",
        "city": "Seoul",
        "activities": ["night market"],
    }
    msg = build_user_message(parsed)
    assert "South Korea" in msg
    assert "Seoul" in msg
    assert "night market" in msg


def test_build_user_message_activity_only_infer_location() -> None:
    """No city/country — agent must infer from activity text."""
    parsed = {"activities": ["salt bread, seoul"]}
    msg = build_user_message(parsed)
    assert "(unknown)" in msg or "unknown" in msg.lower()
    assert "salt bread" in msg


def test_build_user_message_vague_activity_no_geo_fields() -> None:
    parsed = {"activities": ["ramen shop"]}
    msg = build_user_message(parsed)
    assert "ramen shop" in msg
    assert "(unknown)" in msg


# --- _extract_json_array ---


def test_extract_plain_json_array() -> None:
    text = '[{"activity_query": "a", "option_name": "b", "address": "", "location": "", "link": ""}]'
    out = _extract_json_array(text)
    assert out is not None
    assert len(out) == 1
    assert out[0]["option_name"] == "b"


def test_extract_from_markdown_fence() -> None:
    text = """Here is data:
```json
[{"activity_query": "x", "option_name": "y", "address": "", "location": "", "link": ""}]
```
"""
    out = _extract_json_array(text)
    assert out is not None and len(out) == 1


def test_extract_embedded_array() -> None:
    text = 'Prefix text [{"a": 1}] suffix'
    out = _extract_json_array(text)
    assert out is not None and isinstance(out[0], dict)


def test_extract_empty_returns_none() -> None:
    assert _extract_json_array("") is None
    assert _extract_json_array("   ") is None


def test_extract_invalid_returns_none() -> None:
    assert _extract_json_array("not json") is None


# --- _normalize_rows ---


def test_normalize_rows_skips_non_dicts() -> None:
    raw: list = [{"activity_query": "a", "option_name": "b"}, "skip", 42]
    rows = _normalize_rows(raw)
    assert len(rows) == 1
    assert rows[0]["activity_query"] == "a"


def test_normalize_rows_defaults() -> None:
    rows = _normalize_rows([{}])
    assert rows[0]["option_name"] == ""
    assert rows[0]["user_rating"] == ""


# --- run_research (mocked; no live API) ---


@pytest.fixture
def sample_parsed() -> dict:
    return {"country": "", "city": "", "activities": ["salt bread, seoul"]}


def test_run_research_success_parses_response(sample_parsed: dict) -> None:
    payload = [
        {
            "activity_query": "salt bread, seoul",
            "option_name": "Cafe A",
            "address": "1 Main St",
            "location": "Gangnam",
            "link": "https://example.com",
        }
    ]
    fake_text = json.dumps(payload)

    async def _run() -> None:
        fake_response = MagicMock()
        fake_response.content = fake_text
        fake_response.events = None

        with patch("src.research_agent.Agent") as _agent, patch(
            "src.research_agent.InMemoryRunner"
        ) as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run_debug = AsyncMock(return_value=fake_response)
            mock_runner_cls.return_value = mock_runner

            rows, err = await run_research(sample_parsed)

        assert err == ""
        assert len(rows) == 1
        assert rows[0]["option_name"] == "Cafe A"
        assert rows[0]["location"] == "Gangnam"

    asyncio.run(_run())


def test_run_research_runner_raises(sample_parsed: dict) -> None:
    async def _run() -> None:
        with patch("src.research_agent.Agent"), patch("src.research_agent.InMemoryRunner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run_debug = AsyncMock(side_effect=RuntimeError("network down"))
            mock_runner_cls.return_value = mock_runner

            rows, err = await run_research(sample_parsed)

        assert rows == []
        assert "network down" in err

    asyncio.run(_run())


def test_run_research_unparseable_response(sample_parsed: dict) -> None:
    async def _run() -> None:
        fake_response = MagicMock()
        fake_response.content = "no json here"
        fake_response.events = None

        with patch("src.research_agent.Agent"), patch("src.research_agent.InMemoryRunner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run_debug = AsyncMock(return_value=fake_response)
            mock_runner_cls.return_value = mock_runner

            rows, err = await run_research(sample_parsed)

        assert rows == []
        assert "Could not parse" in err

    asyncio.run(_run())


def test_run_research_empty_rows_after_normalize(sample_parsed: dict) -> None:
    async def _run() -> None:
        fake_response = MagicMock()
        fake_response.content = "[1, 2, 3]"
        fake_response.events = None

        with patch("src.research_agent.Agent"), patch("src.research_agent.InMemoryRunner") as mock_runner_cls:
            mock_runner = MagicMock()
            mock_runner.run_debug = AsyncMock(return_value=fake_response)
            mock_runner_cls.return_value = mock_runner

            rows, err = await run_research(sample_parsed)

        assert rows == []
        assert "no valid rows" in err.lower()

    asyncio.run(_run())


def test_research_instruction_mentions_infer() -> None:
    assert "infer" in RESEARCH_INSTRUCTION.lower()
