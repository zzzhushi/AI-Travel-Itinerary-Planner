"""Coordinator-level tests: generate_schedule fallback chain and PlanResult shape.

Uses MockProvider to simulate LLM success/failure without real API calls.
"""

from __future__ import annotations

import json

import pytest

from src.agents.orchestrator import generate_schedule
from src.agents.planner import DAY_END_MINUTES, PlanResult
from tests.mocks.provider import MockProvider


def _options(n: int = 4, *, duration: int = 120) -> list[dict]:
    return [
        {
            "option_id": i,
            "name": f"Place {i}",
            "category": "sightseeing",
            "latitude": None,
            "longitude": None,
            "user_rating": max(1, 5 - i),  # descending ratings
            "is_locked": False,
            "day_number": None,
            "start_minutes": None,
            "default_duration_minutes": duration,
            "opening_hours": None,
        }
        for i in range(1, n + 1)
    ]


def _patch_llm_planner(monkeypatch, provider: MockProvider):
    """Swap the thread-local LlmPlanner's provider for testing."""
    from src.agents import orchestrator
    from src.agents.planner import LlmPlanner

    monkeypatch.setattr(orchestrator, "_get_llm_planner", lambda: LlmPlanner(provider))


class TestGenerateScheduleCoordinator:
    @pytest.mark.asyncio
    async def test_use_llm_false_returns_deterministic(self, monkeypatch):
        result = await generate_schedule(
            "Tokyo", _options(4), num_days=2, use_llm_refinement=False
        )
        assert result.source == "deterministic"
        assert result.day_plans

    @pytest.mark.asyncio
    async def test_deterministic_path_bounded(self, monkeypatch):
        # Many options, few days — all items must start before 21:00 even without LLM
        result = await generate_schedule(
            "Tokyo", _options(20, duration=120), num_days=2, use_llm_refinement=False
        )
        for dp in result.day_plans:
            for item in dp.items:
                assert item.start_minutes < DAY_END_MINUTES

    @pytest.mark.asyncio
    async def test_llm_success_returns_llm_source(self, monkeypatch):
        response = json.dumps([{"day": 1, "items": [
            {"option_id": 1, "start_minutes": 540, "note": "fine"},
            {"option_id": 2, "start_minutes": 700, "note": "fine"},
        ]}])
        _patch_llm_planner(monkeypatch, MockProvider([response]))
        result = await generate_schedule(
            "Tokyo", _options(2), num_days=1, use_llm_refinement=True
        )
        assert result.source == "llm"
        assert result.day_plans

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_deterministic(self, monkeypatch):
        _patch_llm_planner(monkeypatch, MockProvider([]))  # empty provider → error
        result = await generate_schedule(
            "Tokyo", _options(4), num_days=2, use_llm_refinement=True
        )
        assert result.source == "deterministic"
        assert result.day_plans
        assert result.warning  # warning carried from LLM failure

    @pytest.mark.asyncio
    async def test_llm_unusable_output_falls_back(self, monkeypatch):
        # LLM returns out-of-range day → apply_llm_refinement returns [] → fallback
        response = json.dumps([{"day": 99, "items": [
            {"option_id": 1, "start_minutes": 540, "note": "bad day"}
        ]}])
        _patch_llm_planner(monkeypatch, MockProvider([response]))
        result = await generate_schedule(
            "Tokyo", _options(2), num_days=2, use_llm_refinement=True
        )
        assert result.source == "deterministic"
        assert result.day_plans

    @pytest.mark.asyncio
    async def test_fallback_schedule_also_bounded(self, monkeypatch):
        # Even after LLM failure, the fallback must stay within the day window
        _patch_llm_planner(monkeypatch, MockProvider([]))
        result = await generate_schedule(
            "Tokyo", _options(20, duration=120), num_days=2, use_llm_refinement=True
        )
        assert result.source == "deterministic"
        for dp in result.day_plans:
            for item in dp.items:
                assert item.start_minutes < DAY_END_MINUTES
