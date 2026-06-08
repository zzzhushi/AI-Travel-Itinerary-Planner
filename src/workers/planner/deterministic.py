"""DeterministicPlanner: pure-Python schedule builder with day-window enforcement.

Strategy:
1. Filter to options with user_rating >= min_rating.
2. Distribute free items round-robin across num_days (build_schedule).
3. For each day, drop lowest-rated overflow items that don't fit the 09:00–21:00
   window (_fit_day_within_window).
4. Lay surviving items back-to-back in category order (_assign_start_times).
5. Locked items are never moved or dropped.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import obslog
from src.workers.planner.base import PlanResult
from src.workers.planner.types import (
    DAY_END_MINUTES,
    DAY_START_MINUTES,
    ScheduleItem,
    DayPlan,
    _CATEGORY_ORDER,
    category_default_duration,
)


def _assign_start_times(day_plan: DayPlan) -> None:
    """Sort by category preference and lay items back-to-back from DAY_START_MINUTES.

    Locked items with an existing start_minutes are kept in place. All other items
    are sorted by _CATEGORY_ORDER and placed sequentially.
    """
    free = [i for i in day_plan.items if not (i.is_locked and i.start_minutes is not None)]
    free.sort(key=lambda i: _CATEGORY_ORDER.get(i.category, 1))
    current = DAY_START_MINUTES
    for item in free:
        item.start_minutes = current
        current += item.duration_minutes or category_default_duration(item.category)


def _fit_day_within_window(
    day_plan: DayPlan,
    day_start: int = DAY_START_MINUTES,
    day_end: int = DAY_END_MINUTES,
) -> None:
    """Drop lowest-rated overflow items so the day fits within the time window.

    Locked items are never dropped — they are user-pinned anchors.
    Free items are ranked by user_rating (desc); the lowest-rated ones that push
    the day past day_end are dropped. This mirrors the LLM contract: fit as many
    high-priority items as possible, omit the rest.

    Mutates day_plan.items in place; calls _assign_start_times on survivors.
    """
    locked = [i for i in day_plan.items if i.is_locked]
    free = [i for i in day_plan.items if not i.is_locked]

    # Time already consumed by locked items (use their pinned start+duration).
    # We approximate by summing their durations since they may overlap with free items.
    locked_minutes = sum(
        (i.duration_minutes or category_default_duration(i.category))
        for i in locked
    )
    budget = max(0, (day_end - day_start) - locked_minutes)

    # Greedily keep free items in rating order (highest first), stop when full.
    free.sort(key=lambda i: (i.user_rating or 0), reverse=True)
    kept: list[ScheduleItem] = []
    used = 0
    for item in free:
        dur = item.duration_minutes or category_default_duration(item.category)
        if used + dur <= budget:
            kept.append(item)
            used += dur

    day_plan.items = locked + kept
    _assign_start_times(day_plan)


def build_schedule(
    options: list[dict],
    num_days: int,
    locked_items: Optional[list[dict]] = None,
    min_rating: int = 3,
) -> list[DayPlan]:
    """Raw distributor: round-robin free items across days, no window enforcement.

    options: list of dicts with keys: option_id, name, category, latitude, longitude,
             user_rating, is_locked, day_number (optional), default_duration_minutes (optional)
    Returns list of DayPlan sorted by day_number.
    """
    locked_items = locked_items or []

    schedule_items = [
        ScheduleItem(
            option_id=o["option_id"],
            name=o["name"],
            category=o.get("category", "other"),
            latitude=o.get("latitude"),
            longitude=o.get("longitude"),
            user_rating=o.get("user_rating") or 0,
            is_locked=o.get("is_locked", False),
            day_number=o.get("day_number"),
            time_slot=None,
            start_minutes=o.get("start_minutes"),
            duration_minutes=(
                o.get("default_duration_minutes")
                or category_default_duration(o.get("category"))
            ),
            opening_hours=o.get("opening_hours"),
            cluster_id=o.get("cluster_id"),
            cluster_name=o.get("cluster_name"),
        )
        for o in options
    ]

    locked = [i for i in schedule_items if i.is_locked and i.day_number is not None]
    free = [
        i for i in schedule_items
        if not (i.is_locked and i.day_number is not None)
        and (i.user_rating or 0) >= min_rating
    ]

    days: dict[int, DayPlan] = {d: DayPlan(day_number=d) for d in range(1, num_days + 1)}
    for item in locked:
        day = item.day_number
        if day not in days:
            days[day] = DayPlan(day_number=day)
        days[day].items.append(item)

    for idx, item in enumerate(free):
        day_num = (idx % num_days) + 1
        item.day_number = day_num
        days[day_num].items.append(item)

    for day_plan in days.values():
        _assign_start_times(day_plan)

    return sorted(days.values(), key=lambda d: d.day_number)


class DeterministicPlanner:
    """Pure-Python planner: distributes options round-robin and enforces the day window.

    No LLM calls. Suitable as a standalone planner (AI off) or as a fallback
    when the LLM planner fails or hits rate limits.
    """

    async def plan(
        self,
        options: list[dict],
        num_days: int,
        *,
        destination: Optional[str] = None,
        start_date: Optional[date] = None,
        min_rating: int = 1,
        travel_matrix: Optional[dict[str, dict[tuple[int, int], dict]]] = None,
    ) -> PlanResult:
        # travel_matrix is accepted for SchedulePlanner-interface parity but
        # unused: the deterministic planner does not reason about travel times.
        async with obslog.span("deterministic_plan", num_days=num_days) as sp:
            day_plans = build_schedule(options, num_days=num_days, min_rating=min_rating)
            for dp in day_plans:
                _fit_day_within_window(dp)
            sp.set(scheduled_items=sum(len(dp.items) for dp in day_plans))
            return PlanResult(day_plans=day_plans, source="deterministic")
