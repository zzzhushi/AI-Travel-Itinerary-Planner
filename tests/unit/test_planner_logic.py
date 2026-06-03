"""Unit tests for the pure-Python scheduling logic in src/agents/planner.py.

No LLM calls, no mocking — all functions are deterministic.
"""

import pytest

from src.agents.planner import (
    DEFAULT_DURATION_MINUTES,
    DayPlan,
    ScheduleItem,
    _assign_time_slots,
    apply_llm_refinement,
    build_schedule,
    category_default_duration,
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


def _make_original(option_id: int, day: int = 1, slot: str = "morning",
                   locked: bool = False) -> DayPlan:
    """Helper: build a single-item DayPlan for apply_llm_refinement tests."""
    item = ScheduleItem(
        option_id=option_id, name=f"Place {option_id}", category="sightseeing",
        latitude=None, longitude=None, user_rating=4,
        is_locked=locked, day_number=day, time_slot=slot,
    )
    return DayPlan(day_number=day, items=[item])


class TestApplyLlmRefinement:
    def _original(self, specs):
        """Build original day_plans from list of (option_id, day, slot, locked) tuples."""
        from collections import defaultdict
        buckets: dict[int, list] = defaultdict(list)
        for opt_id, day, slot, locked in specs:
            buckets[day].append(ScheduleItem(
                option_id=opt_id, name=f"Place {opt_id}", category="sightseeing",
                latitude=None, longitude=None, user_rating=4,
                is_locked=locked, day_number=day, time_slot=slot,
            ))
        return [DayPlan(day_number=d, items=items) for d, items in sorted(buckets.items())]

    def test_happy_path_applies_llm_ordering_and_slots(self):
        # LLM reorders two items across two days and changes a time slot
        original = self._original([(1, 1, "morning", False), (2, 2, "morning", False)])
        llm_days = [
            {"day": 1, "items": [{"option_id": 2, "name": "Place 2", "time_slot": "evening", "note": ""}]},
            {"day": 2, "items": [{"option_id": 1, "name": "Place 1", "time_slot": "afternoon", "note": ""}]},
        ]
        result = apply_llm_refinement(llm_days, original)
        assert len(result) == 2
        day1_ids = [i.option_id for i in result[0].items]
        day2_ids = [i.option_id for i in result[1].items]
        assert 2 in day1_ids  # LLM moved opt 2 to day 1
        assert 1 in day2_ids  # LLM moved opt 1 to day 2
        assert result[0].items[0].time_slot == "evening"  # LLM slot applied

    def test_dropped_item_is_added_back(self):
        # LLM output omits one item → it must appear in the result at its original placement
        original = self._original([(1, 1, "morning", False), (2, 1, "afternoon", False)])
        llm_days = [
            {"day": 1, "items": [{"option_id": 1, "name": "Place 1", "time_slot": "morning", "note": ""}]},
            # opt 2 was dropped by LLM
        ]
        result = apply_llm_refinement(llm_days, original)
        all_ids = {i.option_id for dp in result for i in dp.items}
        assert 1 in all_ids
        assert 2 in all_ids  # added back

    def test_hallucinated_option_id_is_ignored(self):
        # LLM adds an option_id that doesn't exist in the original → silently dropped
        original = self._original([(1, 1, "morning", False)])
        llm_days = [
            {"day": 1, "items": [
                {"option_id": 1, "name": "Place 1", "time_slot": "morning", "note": ""},
                {"option_id": 999, "name": "Ghost", "time_slot": "morning", "note": ""},
            ]},
        ]
        result = apply_llm_refinement(llm_days, original)
        all_ids = {i.option_id for dp in result for i in dp.items}
        assert 999 not in all_ids

    def test_locked_item_keeps_original_day_and_slot(self):
        # LLM tries to move locked item to a different day/slot → ignored, original preserved
        original = self._original([(1, 3, "afternoon", True), (2, 1, "morning", False)])
        llm_days = [
            {"day": 1, "items": [
                {"option_id": 1, "name": "Place 1", "time_slot": "morning", "note": ""},  # LLM moved locked item
                {"option_id": 2, "name": "Place 2", "time_slot": "morning", "note": ""},
            ]},
        ]
        result = apply_llm_refinement(llm_days, original)
        locked = next(i for dp in result for i in dp.items if i.option_id == 1)
        assert locked.day_number == 3       # original preserved
        assert locked.time_slot == "afternoon"  # original preserved

    def test_empty_llm_days_returns_empty(self):
        # Empty LLM output → caller should fall back to deterministic
        original = self._original([(1, 1, "morning", False)])
        assert apply_llm_refinement([], original) == []

    def test_malformed_day_dict_returns_empty(self):
        # Day dict missing the "day" key → returns [] for deterministic fallback
        original = self._original([(1, 1, "morning", False)])
        llm_days = [{"items": [{"option_id": 1, "time_slot": "morning"}]}]  # no "day"
        assert apply_llm_refinement(llm_days, original) == []


class TestCategoryDefaultDuration:
    def test_mapped_categories_return_seed_values(self):
        # Seed defaults from issue #28
        assert category_default_duration("food") == 60
        assert category_default_duration("shopping") == 60
        assert category_default_duration("sightseeing") == 120
        assert category_default_duration("culture") == 120
        assert category_default_duration("nature") == 180

    def test_is_case_insensitive(self):
        assert category_default_duration("NATURE") == 180

    def test_unmapped_category_falls_back(self):
        # Categories not in the seed map (incl. future user-defined ones) fall back
        assert category_default_duration("transport") == DEFAULT_DURATION_MINUTES
        assert category_default_duration("breakfast") == DEFAULT_DURATION_MINUTES

    def test_none_falls_back(self):
        assert category_default_duration(None) == DEFAULT_DURATION_MINUTES
