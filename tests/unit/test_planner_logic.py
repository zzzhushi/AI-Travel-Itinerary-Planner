"""Unit tests for the pure-Python scheduling logic in src/agents/planner.py.

No LLM calls, no mocking — all functions are deterministic.
"""

import pytest

from src.agents.planner import (
    DayPlan,
    ScheduleItem,
    _assign_time_slots,
    build_schedule,
)


def _opt(option_id: int, name: str, rating: int = 4, category: str = "sightseeing",
         locked: bool = False, day_number: int = None) -> dict:
    """Helper to build an option dict for build_schedule()."""
    return {
        "option_id": option_id,
        "name": name,
        "category": category,
        "latitude": None,
        "longitude": None,
        "user_rating": rating,
        "is_locked": locked,
        "day_number": day_number,
    }


class TestBuildSchedule:
    def test_distributes_options_across_days(self):
        # Input: 6 options, 3 days → each day should have roughly 2 items
        options = [_opt(i, f"Place {i}") for i in range(6)]
        plans = build_schedule(options, num_days=3)
        assert len(plans) == 3
        total = sum(len(p.items) for p in plans)
        assert total == 6

    def test_filters_below_min_rating(self):
        # Input: 3 options rated 5, 3, 1 with min_rating=3 → only 2 should appear
        options = [
            _opt(1, "Great", rating=5),
            _opt(2, "OK", rating=3),
            _opt(3, "Bad", rating=1),
        ]
        plans = build_schedule(options, num_days=2, min_rating=3)
        total = sum(len(p.items) for p in plans)
        assert total == 2

    def test_preserves_locked_items_on_their_day(self):
        # Input: one locked item pinned to day 1 → must remain on day 1 after scheduling
        options = [
            _opt(1, "Locked Spot", rating=5, locked=True, day_number=1),
            _opt(2, "Free Spot", rating=4),
        ]
        plans = build_schedule(options, num_days=2)
        day1 = next(p for p in plans if p.day_number == 1)
        locked_ids = [i.option_id for i in day1.items if i.is_locked]
        assert 1 in locked_ids

    def test_empty_options_returns_empty_days(self):
        # Input: no options → each DayPlan exists but has 0 items
        plans = build_schedule([], num_days=3)
        assert len(plans) == 3
        assert all(len(p.items) == 0 for p in plans)

    def test_more_days_than_options(self):
        # Input: 2 options, 5 days → total items still equals 2, rest of days are empty
        options = [_opt(1, "A"), _opt(2, "B")]
        plans = build_schedule(options, num_days=5)
        total = sum(len(p.items) for p in plans)
        assert total == 2
        assert len(plans) == 5

    def test_days_are_sorted_by_day_number(self):
        # Output DayPlans should be sorted 1, 2, 3, ...
        options = [_opt(i, f"P{i}") for i in range(4)]
        plans = build_schedule(options, num_days=4)
        day_numbers = [p.day_number for p in plans]
        assert day_numbers == sorted(day_numbers)

    def test_all_items_get_time_slots(self):
        # Every scheduled item should have a non-None time_slot after build_schedule
        options = [_opt(i, f"P{i}", category=cat) for i, cat in
                   enumerate(["food", "sightseeing", "nightlife", "culture"])]
        plans = build_schedule(options, num_days=2)
        for plan in plans:
            for item in plan.items:
                assert item.time_slot in ("morning", "afternoon", "evening")

    def test_round_robin_distribution(self):
        # 6 items across 3 days → 2 items per day (round-robin)
        options = [_opt(i, f"P{i}") for i in range(6)]
        plans = build_schedule(options, num_days=3)
        assert all(len(p.items) == 2 for p in plans)


class TestAssignTimeSlots:
    def _make_day(self, categories: list[str]) -> DayPlan:
        items = [
            ScheduleItem(option_id=i, name=f"Item {i}", category=cat,
                         latitude=None, longitude=None, user_rating=4)
            for i, cat in enumerate(categories)
        ]
        return DayPlan(day_number=1, items=items)

    def test_food_gets_afternoon(self):
        # food category → preferred slot is afternoon
        day = self._make_day(["food"])
        _assign_time_slots(day)
        assert day.items[0].time_slot == "afternoon"

    def test_nightlife_gets_evening(self):
        # nightlife category → preferred slot is evening
        day = self._make_day(["nightlife"])
        _assign_time_slots(day)
        assert day.items[0].time_slot == "evening"

    def test_sightseeing_gets_morning(self):
        # sightseeing category → preferred slot is morning
        day = self._make_day(["sightseeing"])
        _assign_time_slots(day)
        assert day.items[0].time_slot == "morning"

    def test_overflow_moves_to_next_slot(self):
        # 3 food items: first 2 get afternoon, 3rd overflows to evening
        day = self._make_day(["food", "food", "food"])
        _assign_time_slots(day)
        slots = [item.time_slot for item in day.items]
        assert slots.count("afternoon") == 2
        assert slots.count("evening") == 1

    def test_locked_time_slot_is_preserved(self):
        # Locked items with an existing time_slot are never reassigned
        item = ScheduleItem(option_id=1, name="Fixed", category="food",
                            latitude=None, longitude=None, user_rating=5,
                            is_locked=True, time_slot="morning")
        day = DayPlan(day_number=1, items=[item])
        _assign_time_slots(day)
        assert day.items[0].time_slot == "morning"
