"""Unit tests for PlannerAgent.refine() using MockProvider.

All tests use MockProvider — no Gemini API calls, no network, no env vars needed.
"""

import json

import pytest

from src.agents.planner import DayPlan, PlannerAgent, ScheduleItem
from tests.mocks.provider import MockProvider


def _make_day_plans() -> list[DayPlan]:
    """Build a minimal 2-day schedule for use as refine() input."""
    day1 = DayPlan(day_number=1, items=[
        ScheduleItem(option_id=1, name="Temple A", category="culture",
                     latitude=35.6, longitude=139.7, user_rating=4, time_slot="morning"),
    ])
    day2 = DayPlan(day_number=2, items=[
        ScheduleItem(option_id=2, name="Ramen B", category="food",
                     latitude=35.7, longitude=139.8, user_rating=5, time_slot="afternoon"),
    ])
    return [day1, day2]


def _make_refined_json() -> str:
    """Valid JSON array that matches the planner's expected output format."""
    return json.dumps([
        {"day": 1, "items": [{"option_id": 1, "name": "Temple A", "time_slot": "morning", "note": "Start early"}]},
        {"day": 2, "items": [{"option_id": 2, "name": "Ramen B", "time_slot": "afternoon", "note": "Lunch spot"}]},
    ])


class TestPlannerAgentRefine:
    @pytest.mark.asyncio
    async def test_happy_path_returns_refined_list(self):
        # Input: valid refined schedule JSON from provider
        # Output: (list of day dicts, "") — error is empty on success
        provider = MockProvider([_make_refined_json()])
        agent = PlannerAgent(provider)
        result, err = await agent.refine(_make_day_plans(), "Tokyo")
        assert err == ""
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["day"] == 1

    @pytest.mark.asyncio
    async def test_agent_error_is_propagated(self):
        # Input: provider queue empty → error returned
        # Output: empty list and error string with "Planner agent error" prefix
        provider = MockProvider([])
        agent = PlannerAgent(provider)
        result, err = await agent.refine(_make_day_plans(), "Tokyo")
        assert result == []
        assert err != ""

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self):
        # Input: provider returns non-JSON text
        # Output: empty list and a parse-error message
        provider = MockProvider(["Here is your schedule: great itinerary!"])
        agent = PlannerAgent(provider)
        result, err = await agent.refine(_make_day_plans(), "Tokyo")
        assert result == []
        assert "Could not parse planner JSON" in err

    @pytest.mark.asyncio
    async def test_json_dict_instead_of_list_returns_empty(self):
        # Input: provider returns a JSON object (dict) instead of array
        # Output: empty list (the refine() method expects a list)
        provider = MockProvider(['{"day": 1, "items": []}'])
        agent = PlannerAgent(provider)
        result, err = await agent.refine(_make_day_plans(), "Tokyo")
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_days_input_still_calls_provider(self):
        # Input: empty day_plans list — still calls ask() and returns whatever provider gives
        provider = MockProvider(["[]"])
        agent = PlannerAgent(provider)
        result, err = await agent.refine([], "Tokyo")
        assert result == []
        assert err == ""
