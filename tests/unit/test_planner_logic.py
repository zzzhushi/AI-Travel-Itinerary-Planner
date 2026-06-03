"""Unit tests for the pure-Python scheduling logic in src/agents/planner.py.

No LLM calls, no mocking — all functions are deterministic.
"""

import pytest

from src.agents.planner import (
    DAY_START_MINUTES,
    DEFAULT_DURATION_MINUTES,
    DayPlan,
    ScheduleItem,
    _assign_start_times,
    apply_llm_refinement,
    build_schedule,
    category_default_duration,
)


def _opt(option_id: int, name: str, rating: int = 4, category: str = "sightseeing",
         locked: bool = False, day_number: int = None,
         default_duration_minutes: int = None) -> dict:
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
        "default_duration_minutes": default_duration_minutes,
    }


class TestBuildSchedule:
    def test_distributes_options_across_days(self):
        options = [_opt(i, f"Place {i}") for i in range(6)]
        plans = build_schedule(options, num_days=3)
        assert len(plans) == 3
        total = sum(len(p.items) for p in plans)
        assert total == 6

    def test_filters_below_min_rating(self):
        options = [
            _opt(1, "Great", rating=5),
            _opt(2, "OK", rating=3),
            _opt(3, "Bad", rating=1),
        ]
        plans = build_schedule(options, num_days=2, min_rating=3)
        total = sum(len(p.items) for p in plans)
        assert total == 2

    def test_preserves_locked_items_on_their_day(self):
        options = [
            _opt(1, "Locked Spot", rating=5, locked=True, day_number=1),
            _opt(2, "Free Spot", rating=4),
        ]
        plans = build_schedule(options, num_days=2)
        day1 = next(p for p in plans if p.day_number == 1)
        locked_ids = [i.option_id for i in day1.items if i.is_locked]
        assert 1 in locked_ids

    def test_empty_options_returns_empty_days(self):
        plans = build_schedule([], num_days=3)
        assert len(plans) == 3
        assert all(len(p.items) == 0 for p in plans)

    def test_more_days_than_options(self):
        options = [_opt(1, "A"), _opt(2, "B")]
        plans = build_schedule(options, num_days=5)
        total = sum(len(p.items) for p in plans)
        assert total == 2
        assert len(plans) == 5

    def test_days_are_sorted_by_day_number(self):
        options = [_opt(i, f"P{i}") for i in range(4)]
        plans = build_schedule(options, num_days=4)
        day_numbers = [p.day_number for p in plans]
        assert day_numbers == sorted(day_numbers)

    def test_all_items_get_start_minutes(self):
        # Every scheduled item should have a non-None start_minutes after build_schedule
        options = [_opt(i, f"P{i}", category=cat) for i, cat in
                   enumerate(["food", "sightseeing", "nightlife", "culture"])]
        plans = build_schedule(options, num_days=2)
        for plan in plans:
            for item in plan.items:
                assert item.start_minutes is not None

    def test_round_robin_distribution(self):
        options = [_opt(i, f"P{i}") for i in range(6)]
        plans = build_schedule(options, num_days=3)
        assert all(len(p.items) == 2 for p in plans)

    def test_uses_default_duration_minutes_from_option(self):
        # When the option carries a user-set duration, build_schedule propagates it
        options = [_opt(1, "Long Tour", category="sightseeing", default_duration_minutes=240)]
        plans = build_schedule(options, num_days=1)
        item = plans[0].items[0]
        assert item.duration_minutes == 240

    def test_falls_back_to_category_duration_when_none(self):
        # No default_duration_minutes → category default is used
        options = [_opt(1, "Nature Walk", category="nature")]
        plans = build_schedule(options, num_days=1)
        item = plans[0].items[0]
        assert item.duration_minutes == category_default_duration("nature")

    def test_time_slot_is_none(self):
        # P5: time_slot is soft-retired from the planning path
        options = [_opt(1, "Shrine", category="culture")]
        plans = build_schedule(options, num_days=1)
        assert plans[0].items[0].time_slot is None


class TestAssignStartTimes:
    def _make_day(self, categories: list[str],
                  durations: list[int] = None) -> DayPlan:
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
        # Two items with known durations → second starts after first ends
        day = self._make_day(["sightseeing", "food"], durations=[120, 60])
        _assign_start_times(day)
        items = sorted(day.items, key=lambda i: i.start_minutes)
        assert items[0].start_minutes == DAY_START_MINUTES
        assert items[1].start_minutes == DAY_START_MINUTES + 120

    def test_category_order_sightseeing_before_food_before_nightlife(self):
        # Items must be sorted: sightseeing(0) → food(1) → nightlife(2)
        day = self._make_day(["nightlife", "food", "sightseeing"])
        _assign_start_times(day)
        ordered = sorted(day.items, key=lambda i: i.start_minutes)
        cats = [i.category for i in ordered]
        assert cats.index("sightseeing") < cats.index("food")
        assert cats.index("food") < cats.index("nightlife")

    def test_locked_item_with_start_minutes_is_not_moved(self):
        # Locked items with an existing start_minutes are excluded from layout
        locked = ScheduleItem(
            option_id=1, name="Fixed", category="food",
            latitude=None, longitude=None, user_rating=5,
            is_locked=True, start_minutes=720, duration_minutes=60,
        )
        free = ScheduleItem(
            option_id=2, name="Free", category="sightseeing",
            latitude=None, longitude=None, user_rating=4,
            duration_minutes=120,
        )
        day = DayPlan(day_number=1, items=[locked, free])
        _assign_start_times(day)
        assert locked.start_minutes == 720   # untouched
        assert free.start_minutes == DAY_START_MINUTES  # placed from day start

    def test_falls_back_to_category_duration_when_item_has_none(self):
        # No duration_minutes on item → category_default_duration used for spacing
        day = self._make_day(["sightseeing", "food"], durations=[None, None])
        _assign_start_times(day)
        items = sorted(day.items, key=lambda i: i.start_minutes)
        expected_gap = category_default_duration("sightseeing")  # 120
        assert items[1].start_minutes == DAY_START_MINUTES + expected_gap


class TestApplyLlmRefinement:
    def _make_items(self, specs) -> list[DayPlan]:
        """Build original day_plans from (option_id, day, start_minutes, locked, duration) tuples."""
        from collections import defaultdict
        buckets: dict[int, list] = defaultdict(list)
        for opt_id, day, start, locked, dur in specs:
            buckets[day].append(ScheduleItem(
                option_id=opt_id, name=f"Place {opt_id}", category="sightseeing",
                latitude=None, longitude=None, user_rating=4,
                is_locked=locked, day_number=day,
                start_minutes=start, duration_minutes=dur,
            ))
        return [DayPlan(day_number=d, items=items) for d, items in sorted(buckets.items())]

    def test_happy_path_uses_llm_start_minutes(self):
        # LLM assigns specific start_minutes → used on the resulting items
        original = self._make_items([(1, 1, 540, False, 120), (2, 1, 660, False, 60)])
        llm_days = [{"day": 1, "items": [
            {"option_id": 2, "name": "Place 2", "start_minutes": 600, "note": "first"},
            {"option_id": 1, "name": "Place 1", "start_minutes": 720, "note": "second"},
        ]}]
        result = apply_llm_refinement(llm_days, original, num_days=1)
        assert len(result) == 1
        by_id = {i.option_id: i for i in result[0].items}
        assert by_id[2].start_minutes == 600
        assert by_id[1].start_minutes == 720

    def test_llm_can_move_item_to_different_day(self):
        original = self._make_items([(1, 1, 540, False, 120), (2, 2, 540, False, 60)])
        llm_days = [
            {"day": 1, "items": [{"option_id": 2, "start_minutes": 540, "note": ""}]},
            {"day": 2, "items": [{"option_id": 1, "start_minutes": 600, "note": ""}]},
        ]
        result = apply_llm_refinement(llm_days, original, num_days=2)
        day1_ids = {i.option_id for i in result[0].items}
        day2_ids = {i.option_id for i in result[1].items}
        assert 2 in day1_ids
        assert 1 in day2_ids

    def test_locked_item_keeps_original_start_minutes(self):
        # LLM tries to change locked item's time → original start_minutes restored
        original = self._make_items([(1, 1, 720, True, 60), (2, 1, 540, False, 120)])
        llm_days = [{"day": 1, "items": [
            {"option_id": 1, "start_minutes": 900, "note": ""},   # LLM changed locked time
            {"option_id": 2, "start_minutes": 540, "note": ""},
        ]}]
        result = apply_llm_refinement(llm_days, original, num_days=1)
        locked = next(i for i in result[0].items if i.option_id == 1)
        assert locked.start_minutes == 720   # original preserved

    def test_dropped_item_is_excluded_from_result(self):
        # LLM intentionally drops an item (doesn't fit) → it stays out of the schedule
        original = self._make_items([(1, 1, 540, False, 120), (2, 1, 660, False, 60)])
        llm_days = [{"day": 1, "items": [
            {"option_id": 1, "start_minutes": 540, "note": ""},
            # opt 2 dropped by LLM — too low priority to fit
        ]}]
        result = apply_llm_refinement(llm_days, original, num_days=1)
        all_ids = {i.option_id for dp in result for i in dp.items}
        assert 1 in all_ids
        assert 2 not in all_ids   # LLM's drop decision is respected

    def test_hallucinated_option_id_is_ignored(self):
        original = self._make_items([(1, 1, 540, False, 60)])
        llm_days = [{"day": 1, "items": [
            {"option_id": 1, "start_minutes": 540, "note": ""},
            {"option_id": 999, "start_minutes": 600, "note": ""},  # hallucinated
        ]}]
        result = apply_llm_refinement(llm_days, original, num_days=1)
        all_ids = {i.option_id for dp in result for i in dp.items}
        assert 999 not in all_ids

    def test_invalid_start_minutes_falls_back_to_sequential(self):
        # LLM returns a start_minutes out of range → sequential time used (not original)
        original = self._make_items([(1, 1, 600, False, 60)])
        llm_days = [{"day": 1, "items": [
            {"option_id": 1, "start_minutes": 9999, "note": ""},  # invalid
        ]}]
        result = apply_llm_refinement(llm_days, original, num_days=1)
        # Should land at DAY_START_MINUTES, not the original 600
        assert result[0].items[0].start_minutes == DAY_START_MINUTES

    def test_missing_start_minutes_uses_llm_ordering_sequentially(self):
        # LLM reorders two items but omits start_minutes → placed sequentially
        # from DAY_START_MINUTES in LLM order (not original order)
        original = self._make_items([(1, 1, 540, False, 120), (2, 1, 660, False, 60)])
        llm_days = [{"day": 1, "items": [
            {"option_id": 2, "note": "first"},   # LLM puts 2 first, no time given
            {"option_id": 1, "note": "second"},  # LLM puts 1 second, no time given
        ]}]
        result = apply_llm_refinement(llm_days, original, num_days=1)
        by_id = {i.option_id: i for i in result[0].items}
        # Item 2 is placed first: starts at DAY_START_MINUTES
        assert by_id[2].start_minutes == DAY_START_MINUTES
        # Item 1 follows after item 2's duration (60 min)
        assert by_id[1].start_minutes == DAY_START_MINUTES + 60

    def test_day_number_above_num_days_returns_empty(self):
        # Day 4 in a 3-day trip → reject entirely (fixes #17)
        original = self._make_items([(1, 1, 540, False, 60)])
        llm_days = [{"day": 4, "items": [{"option_id": 1, "start_minutes": 540, "note": ""}]}]
        assert apply_llm_refinement(llm_days, original, num_days=3) == []

    def test_empty_llm_days_returns_empty(self):
        original = self._make_items([(1, 1, 540, False, 60)])
        assert apply_llm_refinement([], original, num_days=1) == []

    def test_malformed_day_dict_returns_empty(self):
        original = self._make_items([(1, 1, 540, False, 60)])
        llm_days = [{"items": [{"option_id": 1, "start_minutes": 540}]}]  # no "day"
        assert apply_llm_refinement(llm_days, original, num_days=1) == []


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
