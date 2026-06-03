"""Unit tests for LlmPlanner using MockProvider.

All tests use MockProvider — no Gemini API calls, no network, no env vars needed.
"""

from __future__ import annotations

import json

import pytest

from src.agents.planner import (
    DAY_END_MINUTES,
    DAY_START_MINUTES,
    DayPlan,
    LlmPlanner,
    ScheduleItem,
)
from tests.mocks.provider import MockProvider


def _make_options(n: int = 2) -> list[dict]:
    return [
        {
            "option_id": i,
            "name": f"Place {i}",
            "category": "sightseeing",
            "latitude": 35.6 + i * 0.01,
            "longitude": 139.7,
            "user_rating": 4,
            "is_locked": False,
            "day_number": None,
            "start_minutes": None,
            "default_duration_minutes": 120,
        }
        for i in range(1, n + 1)
    ]


def _valid_response(day: int, option_ids: list[int], start: int = 540) -> str:
    items = [
        {"option_id": oid, "name": f"Place {oid}", "start_minutes": start + i * 130, "note": "ok"}
        for i, oid in enumerate(option_ids)
    ]
    return json.dumps([{"day": day, "items": items}])


class TestLlmPlannerHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path_returns_llm_source(self):
        provider = MockProvider([_valid_response(1, [1, 2])])
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(2), num_days=1, destination="Tokyo")
        assert result.source == "llm"
        assert result.day_plans
        assert result.warning == ""

    @pytest.mark.asyncio
    async def test_uses_llm_start_minutes(self):
        response = json.dumps([{"day": 1, "items": [
            {"option_id": 1, "start_minutes": 600, "note": "first"},
            {"option_id": 2, "start_minutes": 800, "note": "second"},
        ]}])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(2), num_days=1, destination="Tokyo")
        by_id = {i.option_id: i for dp in result.day_plans for i in dp.items}
        assert by_id[1].start_minutes == 600
        assert by_id[2].start_minutes == 800

    @pytest.mark.asyncio
    async def test_llm_can_move_item_to_different_day(self):
        response = json.dumps([
            {"day": 1, "items": [{"option_id": 2, "start_minutes": 540, "note": ""}]},
            {"day": 2, "items": [{"option_id": 1, "start_minutes": 600, "note": ""}]},
        ])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(2), num_days=2, destination="Tokyo")
        by_day = {dp.day_number: {i.option_id for i in dp.items} for dp in result.day_plans}
        assert 2 in by_day[1]
        assert 1 in by_day[2]


class TestLlmPlannerFailureFallback:
    @pytest.mark.asyncio
    async def test_provider_error_returns_empty_with_warning(self):
        provider = MockProvider([])  # empty → triggers error
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(2), num_days=1, destination="Tokyo")
        assert result.day_plans == []
        assert result.warning != ""
        assert result.source == "llm"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty_with_warning(self):
        provider = MockProvider(["not valid json at all"])
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(2), num_days=1, destination="Tokyo")
        assert result.day_plans == []
        assert result.warning != ""

    @pytest.mark.asyncio
    async def test_json_dict_instead_of_list_returns_empty(self):
        provider = MockProvider(['{"day": 1, "items": []}'])
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(2), num_days=1, destination="Tokyo")
        assert result.day_plans == []

    @pytest.mark.asyncio
    async def test_all_empty_days_returns_empty(self):
        # LLM returns day shells with no items → useless schedule → empty
        response = json.dumps([{"day": 1, "items": []}, {"day": 2, "items": []}])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(2), num_days=2, destination="Tokyo")
        assert result.day_plans == []
        assert result.warning != ""

    @pytest.mark.asyncio
    async def test_out_of_range_day_returns_empty(self):
        response = json.dumps([{"day": 5, "items": [{"option_id": 1, "start_minutes": 540}]}])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(1), num_days=3, destination="Tokyo")
        assert result.day_plans == []


class TestLlmPlannerWindowAndLocking:
    @pytest.mark.asyncio
    async def test_locked_item_keeps_original_start_minutes(self):
        options = [
            {**_make_options(1)[0], "option_id": 1, "is_locked": True,
             "day_number": 1, "start_minutes": 720},
            {**_make_options(1)[0], "option_id": 2},
        ]
        response = json.dumps([{"day": 1, "items": [
            {"option_id": 1, "start_minutes": 900, "note": "LLM tried to move it"},
            {"option_id": 2, "start_minutes": 600, "note": "free"},
        ]}])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(options, num_days=1, destination="Tokyo")
        locked = next(i for dp in result.day_plans for i in dp.items if i.option_id == 1)
        assert locked.start_minutes == 720

    @pytest.mark.asyncio
    async def test_locked_item_preserved_when_llm_omits_it(self):
        options = [
            {**_make_options(1)[0], "option_id": 1, "is_locked": True,
             "day_number": 1, "start_minutes": 720},
            {**_make_options(1)[0], "option_id": 2},
        ]
        response = json.dumps([{"day": 1, "items": [
            {"option_id": 2, "start_minutes": 540, "note": "only free item"},
        ]}])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(options, num_days=1, destination="Tokyo")
        all_ids = {i.option_id for dp in result.day_plans for i in dp.items}
        assert 1 in all_ids

    @pytest.mark.asyncio
    async def test_unlocked_item_at_day_end_dropped(self):
        options = _make_options(2)
        response = json.dumps([{"day": 1, "items": [
            {"option_id": 1, "start_minutes": 600, "note": "in window"},
            {"option_id": 2, "start_minutes": DAY_END_MINUTES, "note": "at limit"},
        ]}])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(options, num_days=1, destination="Tokyo")
        ids = {i.option_id for dp in result.day_plans for i in dp.items}
        assert 1 in ids
        assert 2 not in ids

    @pytest.mark.asyncio
    async def test_duplicate_llm_day_entries_no_duplicate_dayplans(self):
        # #45: LLM returns day 1 twice — result must have exactly one DayPlan for day 1
        response = json.dumps([
            {"day": 1, "items": [{"option_id": 1, "start_minutes": 540, "note": ""}]},
            {"day": 1, "items": [{"option_id": 2, "start_minutes": 700, "note": ""}]},
        ])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(2), num_days=1, destination="Tokyo")
        day1_plans = [dp for dp in result.day_plans if dp.day_number == 1]
        assert len(day1_plans) == 1

    @pytest.mark.asyncio
    async def test_locked_item_placed_on_correct_day_not_llm_day(self):
        # #44: LLM lists locked item under day 2, but it's pinned to day 1
        options = [
            {**_make_options(1)[0], "option_id": 1, "is_locked": True,
             "day_number": 1, "start_minutes": 720},
            {**_make_options(1)[0], "option_id": 2},
        ]
        response = json.dumps([
            {"day": 2, "items": [{"option_id": 1, "start_minutes": 540, "note": "LLM moved it"}]},
            {"day": 1, "items": [{"option_id": 2, "start_minutes": 600, "note": ""}]},
        ])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(options, num_days=2, destination="Tokyo")
        day1 = next(dp for dp in result.day_plans if dp.day_number == 1)
        assert any(i.option_id == 1 for i in day1.items)

    @pytest.mark.asyncio
    async def test_items_sorted_by_start_minutes_within_day(self):
        response = json.dumps([{"day": 1, "items": [
            {"option_id": 2, "start_minutes": 800, "note": "later"},
            {"option_id": 1, "start_minutes": 600, "note": "earlier"},
        ]}])
        provider = MockProvider([response])
        planner = LlmPlanner(provider)
        result = await planner.plan(_make_options(2), num_days=1, destination="Tokyo")
        starts = [i.start_minutes for i in result.day_plans[0].items]
        assert starts == sorted(starts)
