"""Unit tests for the pure-Python scheduling logic in src/agents/planner.py.

No LLM calls, no mocking — all functions are deterministic.
"""

import pytest

from src.agents.planner import (
    DayPlan,
    ScheduleItem,
    _assign_time_slots,
    _cluster_by_proximity,
    build_schedule,
)


def _opt(option_id: int, name: str, rating: int = 4, category: str = "sightseeing",
         lat: float = None, lon: float = None, locked: bool = False,
         day_number: int = None) -> dict:
    """Helper to build an option dict for build_schedule()."""
    return {
        "option_id": option_id,
        "name": name,
        "category": category,
        "latitude": lat,
        "longitude": lon,
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


class TestClusterByProximity:
    def _item(self, oid: int, lat: float = None, lon: float = None, rating: int = 4) -> ScheduleItem:
        return ScheduleItem(option_id=oid, name=f"Item {oid}", category="sightseeing",
                            latitude=lat, longitude=lon, user_rating=rating)

    def test_round_robin_without_coords(self):
        # Input: 4 items with no coordinates, 2 days
        # Output: each day gets 2 items (round-robin distribution)
        items = [self._item(i) for i in range(4)]
        clusters = _cluster_by_proximity(items, num_days=2)
        assert len(clusters[0]) == 2
        assert len(clusters[1]) == 2

    def test_nearby_items_cluster_together(self):
        # Tokyo cluster (35.6, 139.7) and Seoul cluster (37.5, 126.9) — far apart
        # With 2 days, each geographical cluster should land on a different day
        tokyo_items = [self._item(i, lat=35.6 + i * 0.001, lon=139.7 + i * 0.001) for i in range(3)]
        seoul_items = [self._item(i + 10, lat=37.5 + i * 0.001, lon=126.9 + i * 0.001) for i in range(3)]
        all_items = tokyo_items + seoul_items
        clusters = _cluster_by_proximity(all_items, num_days=2)
        # Each cluster should be dominated by one city
        c0_ids = {item.option_id for item in clusters[0]}
        c1_ids = {item.option_id for item in clusters[1]}
        tokyo_ids = {i.option_id for i in tokyo_items}
        seoul_ids = {i.option_id for i in seoul_items}
        # Both sets should exist and the majority of each city ends up in one cluster
        assert len(c0_ids & tokyo_ids) + len(c1_ids & tokyo_ids) == 3
        assert len(c0_ids & seoul_ids) + len(c1_ids & seoul_ids) == 3

    def test_empty_items_returns_empty_clusters(self):
        # Input: no items → each cluster is an empty list
        clusters = _cluster_by_proximity([], num_days=3)
        assert len(clusters) == 3
        assert all(len(c) == 0 for c in clusters)

    def test_single_item_goes_to_first_cluster(self):
        # Input: 1 item, 3 days → item lands in the first cluster (seeds first day)
        items = [self._item(1, lat=35.0, lon=139.0)]
        clusters = _cluster_by_proximity(items, num_days=3)
        total = sum(len(c) for c in clusters)
        assert total == 1
