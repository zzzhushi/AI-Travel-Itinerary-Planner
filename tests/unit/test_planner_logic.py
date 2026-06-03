"""Unit tests for DeterministicPlanner and the internal scheduling logic.

Tests go through DeterministicPlanner.plan() — the public strategy interface.
Internal helpers (_assign_start_times, _fit_day_within_window) are tested
indirectly via the planner's observable outputs.
"""

from __future__ import annotations

import json
import pytest

from src.workers.planner import (
    DAY_END_MINUTES,
    DAY_START_MINUTES,
    DEFAULT_DURATION_MINUTES,
    DayPlan,
    DeterministicPlanner,
    ScheduleItem,
    category_default_duration,
)
from src.workers.planner.deterministic import _assign_start_times, _fit_day_within_window


def _opt(
    option_id: int,
    name: str,
    rating: int = 4,
    category: str = "sightseeing",
    locked: bool = False,
    day_number: int = None,
    default_duration_minutes: int = None,
    start_minutes: int = None,
) -> dict:
    return {
        "option_id": option_id,
        "name": name,
        "category": category,
        "latitude": None,
        "longitude": None,
        "user_rating": rating,
        "is_locked": locked,
        "day_number": day_number,
        "start_minutes": start_minutes,
        "default_duration_minutes": default_duration_minutes,
    }


# ---------------------------------------------------------------------------
# DeterministicPlanner.plan — distribution behaviour
# ---------------------------------------------------------------------------

class TestDeterministicPlannerDistribution:
    @pytest.mark.asyncio
    async def test_distributes_options_across_days(self):
        options = [_opt(i, f"Place {i}") for i in range(6)]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=3)
        assert len(result.day_plans) == 3
        assert sum(len(dp.items) for dp in result.day_plans) == 6

    @pytest.mark.asyncio
    async def test_source_is_deterministic(self):
        planner = DeterministicPlanner()
        result = await planner.plan([_opt(1, "A")], num_days=1)
        assert result.source == "deterministic"

    @pytest.mark.asyncio
    async def test_filters_below_min_rating(self):
        options = [
            _opt(1, "Great", rating=5),
            _opt(2, "OK", rating=3),
            _opt(3, "Bad", rating=1),
        ]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=2, min_rating=3)
        assert sum(len(dp.items) for dp in result.day_plans) == 2

    @pytest.mark.asyncio
    async def test_preserves_locked_items_on_their_day(self):
        options = [
            _opt(1, "Locked Spot", rating=5, locked=True, day_number=1),
            _opt(2, "Free Spot", rating=4),
        ]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=2)
        day1 = next(dp for dp in result.day_plans if dp.day_number == 1)
        assert any(i.option_id == 1 and i.is_locked for i in day1.items)

    @pytest.mark.asyncio
    async def test_locked_item_keeps_pinned_start_minutes(self):
        options = [
            _opt(1, "Pinned Lunch", locked=True, day_number=1, start_minutes=720),
            _opt(2, "Free Spot", rating=4),
        ]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=2)
        day1 = next(dp for dp in result.day_plans if dp.day_number == 1)
        locked = next(i for i in day1.items if i.option_id == 1)
        assert locked.start_minutes == 720

    @pytest.mark.asyncio
    async def test_empty_options_returns_empty_days(self):
        planner = DeterministicPlanner()
        result = await planner.plan([], num_days=3)
        assert len(result.day_plans) == 3
        assert all(len(dp.items) == 0 for dp in result.day_plans)

    @pytest.mark.asyncio
    async def test_days_sorted_by_day_number(self):
        options = [_opt(i, f"P{i}") for i in range(4)]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=4)
        nums = [dp.day_number for dp in result.day_plans]
        assert nums == sorted(nums)

    @pytest.mark.asyncio
    async def test_round_robin_distribution(self):
        options = [_opt(i, f"P{i}") for i in range(6)]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=3)
        assert all(len(dp.items) == 2 for dp in result.day_plans)

    @pytest.mark.asyncio
    async def test_uses_default_duration_minutes_from_option(self):
        options = [_opt(1, "Long Tour", category="sightseeing", default_duration_minutes=240)]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=1)
        assert result.day_plans[0].items[0].duration_minutes == 240

    @pytest.mark.asyncio
    async def test_falls_back_to_category_duration_when_none(self):
        options = [_opt(1, "Nature Walk", category="nature")]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=1)
        assert result.day_plans[0].items[0].duration_minutes == category_default_duration("nature")

    @pytest.mark.asyncio
    async def test_time_slot_is_none(self):
        options = [_opt(1, "Shrine", category="culture")]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=1)
        assert result.day_plans[0].items[0].time_slot is None


# ---------------------------------------------------------------------------
# DeterministicPlanner.plan — day-window enforcement (#41)
# ---------------------------------------------------------------------------

class TestDayWindowEnforcement:
    @pytest.mark.asyncio
    async def test_all_items_start_before_day_end(self):
        # 10 items (120 min each) → only 6 fit in 09:00–21:00 (720 min budget)
        options = [_opt(i, f"P{i}", rating=4, category="sightseeing") for i in range(1, 11)]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=1)
        for dp in result.day_plans:
            for item in dp.items:
                assert item.start_minutes < DAY_END_MINUTES, (
                    f"{item.name} starts at {item.start_minutes} >= DAY_END_MINUTES"
                )

    @pytest.mark.asyncio
    async def test_lowest_rated_overflow_dropped(self):
        # 4 items of 240 min each — only 3 fit (720 min). Lowest-rated should be gone.
        options = [
            _opt(1, "Top", rating=5, category="sightseeing", default_duration_minutes=240),
            _opt(2, "Good", rating=4, category="sightseeing", default_duration_minutes=240),
            _opt(3, "OK", rating=3, category="sightseeing", default_duration_minutes=240),
            _opt(4, "Low", rating=2, category="sightseeing", default_duration_minutes=240),
        ]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=1, min_rating=1)
        ids = {i.option_id for dp in result.day_plans for i in dp.items}
        assert 1 in ids
        assert 2 in ids
        assert 3 in ids
        assert 4 not in ids  # lowest-rated dropped

    @pytest.mark.asyncio
    async def test_locked_items_never_dropped(self):
        # Locked item plus enough free items to overflow — locked must survive.
        options = [
            _opt(1, "Locked", rating=1, locked=True, day_number=1, start_minutes=720,
                 default_duration_minutes=120),
        ] + [_opt(i, f"Free {i}", rating=4, category="sightseeing",
                  default_duration_minutes=120) for i in range(2, 10)]
        planner = DeterministicPlanner()
        result = await planner.plan(options, num_days=1)
        day1 = next(dp for dp in result.day_plans if dp.day_number == 1)
        assert any(i.option_id == 1 for i in day1.items)


# ---------------------------------------------------------------------------
# _assign_start_times (internal helper — imported directly for targeted tests)
# ---------------------------------------------------------------------------

class TestAssignStartTimes:
    def _make_day(self, categories: list[str], durations: list[int] = None) -> DayPlan:
        durations = durations or [None] * len(categories)
        items = [
            ScheduleItem(option_id=i, name=f"Item {i}", category=cat,
                         latitude=None, longitude=None, user_rating=4,
                         duration_minutes=dur)
            for i, (cat, dur) in enumerate(zip(categories, durations))
        ]
        return DayPlan(day_number=1, items=items)

    def test_first_item_starts_at_day_start(self):
        day = self._make_day(["sightseeing"])
        _assign_start_times(day)
        assert day.items[0].start_minutes == DAY_START_MINUTES

    def test_sequential_times_use_duration(self):
        day = self._make_day(["sightseeing", "food"], durations=[120, 60])
        _assign_start_times(day)
        items = sorted(day.items, key=lambda i: i.start_minutes)
        assert items[0].start_minutes == DAY_START_MINUTES
        assert items[1].start_minutes == DAY_START_MINUTES + 120

    def test_category_order_sightseeing_before_food_before_nightlife(self):
        day = self._make_day(["nightlife", "food", "sightseeing"])
        _assign_start_times(day)
        ordered = sorted(day.items, key=lambda i: i.start_minutes)
        cats = [i.category for i in ordered]
        assert cats.index("sightseeing") < cats.index("food")
        assert cats.index("food") < cats.index("nightlife")

    def test_locked_item_with_start_minutes_is_not_moved(self):
        locked = ScheduleItem(option_id=1, name="Fixed", category="food",
                              latitude=None, longitude=None, user_rating=5,
                              is_locked=True, start_minutes=720, duration_minutes=60)
        free = ScheduleItem(option_id=2, name="Free", category="sightseeing",
                            latitude=None, longitude=None, user_rating=4,
                            duration_minutes=120)
        day = DayPlan(day_number=1, items=[locked, free])
        _assign_start_times(day)
        assert locked.start_minutes == 720
        assert free.start_minutes == DAY_START_MINUTES

    def test_falls_back_to_category_duration_when_item_has_none(self):
        day = self._make_day(["sightseeing", "food"], durations=[None, None])
        _assign_start_times(day)
        items = sorted(day.items, key=lambda i: i.start_minutes)
        assert items[1].start_minutes == DAY_START_MINUTES + category_default_duration("sightseeing")


# ---------------------------------------------------------------------------
# category_default_duration
# ---------------------------------------------------------------------------

class TestCategoryDefaultDuration:
    def test_mapped_categories_return_seed_values(self):
        assert category_default_duration("food") == 60
        assert category_default_duration("shopping") == 60
        assert category_default_duration("sightseeing") == 120
        assert category_default_duration("culture") == 120
        assert category_default_duration("nature") == 180

    def test_is_case_insensitive(self):
        assert category_default_duration("NATURE") == 180

    def test_unmapped_category_falls_back(self):
        assert category_default_duration("transport") == DEFAULT_DURATION_MINUTES
        assert category_default_duration("breakfast") == DEFAULT_DURATION_MINUTES

    def test_none_falls_back(self):
        assert category_default_duration(None) == DEFAULT_DURATION_MINUTES
