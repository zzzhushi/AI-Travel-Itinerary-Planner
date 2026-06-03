"""
Planner agent: takes rated options and produces a day-by-day schedule.

Strategy:
1. Filter to options with user_rating >= min_rating (default 3).
2. Distribute free items round-robin across num_days.
3. Deterministic fallback: assign sequential start times by category order.
4. LLM refinement: assign real clock times considering duration, rating, and day bounds.
5. Locked items are never moved.

The Gemini agent receives duration and rating per item and is expected to pack
each day intelligently within the DAY_START–DAY_END window, prioritising
higher-rated items and minimising geographic backtracking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from src.agents.base import LlmAgent, _extract_json
from src.agents.providers import LLMProvider

DAY_START_MINUTES = 540   # 09:00
DAY_END_MINUTES = 1260    # 21:00
DEFAULT_DURATION_MINUTES = 60  # fallback when a category has no specific default

# Typical visit length (minutes) by category. Keep in sync with ACTIVITY_CATEGORIES
# in src/db/models.py. Categories not listed fall back to DEFAULT_DURATION_MINUTES.
_CATEGORY_DURATION: dict[str, int] = {
    "food": 60,
    "shopping": 60,
    "sightseeing": 120,
    "culture": 120,
    "nature": 180,
}

# Used by the deterministic fallback to sort items within a day before
# assigning sequential start times.
_CATEGORY_ORDER: dict[str, int] = {
    "sightseeing": 0, "culture": 0, "nature": 0,
    "accommodation": 0, "transport": 0,
    "food": 1, "shopping": 1, "other": 1,
    "nightlife": 2,
}

# Legacy slot anchors — kept for reference / backfill; no longer written by the planner.
SLOT_START_MINUTES: dict[str, int] = {
    "morning": 540, "afternoon": 780, "evening": 1080,
}


def category_default_duration(category: Optional[str]) -> int:
    """Typical visit length (minutes) for a category; falls back to DEFAULT_DURATION_MINUTES.

    Accepts ANY category string (case-insensitive) — including future user-defined
    categories — and falls back gracefully.
    """
    return _CATEGORY_DURATION.get((category or "").lower(), DEFAULT_DURATION_MINUTES)


PLANNER_INSTRUCTION = """You are a travel itinerary planner. You will receive a draft schedule grouped by day, with each day labelled by its actual date and day of week. For each day, assign a realistic start_minutes (minutes from midnight, e.g. 540 = 09:00) to every item, fitting them within the day window (day_start: 540, day_end: 1260 = 21:00).

Each item includes option_id, name, category, duration_minutes (visit length in minutes), user_rating (1–5, higher = more important), current start_minutes, is_locked, and opening_hours (full weekly schedule from Google Places).

Rules:
- Schedule items back-to-back with roughly 10–15 minutes travel time between stops.
- Every item must start within the day window: never schedule an item to start at or after day_end (1260 = 21:00), and do not stack items past it.
- Fit as many high-priority items as realistically fit within each day's window, and omit the rest — you do NOT need to place every item. Prioritise higher-rated items (5 > 4 > 3); when items don't fit, drop the lowest-rated ones (omit them from the response entirely).
- Minimise backtracking: group geographically nearby items and order them so travel flows logically through neighbourhoods or districts.
- Locked items (is_locked: true) must keep their current start_minutes and day unchanged.
- Use opening_hours (full week) to schedule each item on a day when it is actually open. If an item is on a day it is closed, move it to any other day in the schedule when it is open. Only drop an item if it is closed on every available day or cannot otherwise fit. Do not place an item outside its open hours for the day it is scheduled.
- If opening_hours is null or unknown, schedule normally without restriction.
- Order items naturally by time of day: sightseeing/culture/nature early, food/shopping midday, nightlife/dinner in the evening — adjust when geographic flow or opening hours demand it.

Respond with a JSON array of days, each with:
- "day": integer (1-based)
- "items": array of objects with "option_id" (int), "name" (str), "start_minutes" (int, minutes from midnight), "note" (brief one-line reason for placement)

Return ONLY the JSON array. No markdown, no extra text."""


@dataclass
class ScheduleItem:
    option_id: int
    name: str
    category: str
    latitude: Optional[float]
    longitude: Optional[float]
    user_rating: int
    is_locked: bool = False
    day_number: Optional[int] = None
    time_slot: Optional[str] = None        # soft-retired; kept for DB backfill reads
    start_minutes: Optional[int] = None   # clock placement set by planner
    duration_minutes: Optional[int] = None
    note: Optional[str] = None            # LLM placement rationale
    opening_hours: Optional[str] = None  # raw Places API hours string


@dataclass
class DayPlan:
    day_number: int
    items: list[ScheduleItem] = field(default_factory=list)


def _assign_start_times(day_plan: DayPlan) -> None:
    """Deterministic fallback: sort by category preference, lay items back-to-back.

    Locked items with an existing start_minutes are kept in place. All other items
    are sorted by _CATEGORY_ORDER and placed sequentially from DAY_START_MINUTES.
    """
    free = [i for i in day_plan.items if not (i.is_locked and i.start_minutes is not None)]
    free.sort(key=lambda i: _CATEGORY_ORDER.get(i.category, 1))
    current = DAY_START_MINUTES
    for item in free:
        item.start_minutes = current
        current += item.duration_minutes or category_default_duration(item.category)


def build_schedule(
    options: list[dict],
    num_days: int,
    locked_items: Optional[list[dict]] = None,
    min_rating: int = 3,
) -> list[DayPlan]:
    """
    Pure Python schedule builder (no LLM call). Used as the base / fallback schedule.

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
            # Carry the existing placement time so locked items keep their pinned
            # start. _assign_start_times leaves locked items with a start_minutes
            # in place; non-locked items are re-laid regardless.
            start_minutes=o.get("start_minutes"),
            duration_minutes=(
                o.get("default_duration_minutes")
                or category_default_duration(o.get("category"))
            ),
            opening_hours=o.get("opening_hours"),
        )
        for o in options
    ]

    # Separate locked (pre-placed) from free items
    locked = [i for i in schedule_items if i.is_locked and i.day_number is not None]
    free = [
        i for i in schedule_items
        if not (i.is_locked and i.day_number is not None)
        and (i.user_rating or 0) >= min_rating
    ]

    # Build initial day buckets from locked items
    days: dict[int, DayPlan] = {d: DayPlan(day_number=d) for d in range(1, num_days + 1)}
    for item in locked:
        day = item.day_number
        if day not in days:
            days[day] = DayPlan(day_number=day)
        days[day].items.append(item)

    # Distribute free items round-robin across days
    for idx, item in enumerate(free):
        day_num = (idx % num_days) + 1
        item.day_number = day_num
        days[day_num].items.append(item)

    # Assign sequential start times per day (deterministic fallback)
    for day_plan in days.values():
        _assign_start_times(day_plan)

    return sorted(days.values(), key=lambda d: d.day_number)


def apply_llm_refinement(
    llm_days: list[dict],
    original_day_plans: list[DayPlan],
    num_days: int,
) -> list[DayPlan]:
    """Merge the LLM's scheduling decisions with the original item metadata.

    The LLM is responsible for three things:
      1. Which day each item goes on (it can move items across days).
      2. What time each item starts (start_minutes, minutes from midnight).
      3. A one-line note explaining the placement.

    Everything else — name, category, lat/lng, duration, user_rating — comes
    from original_day_plans, which is the source of truth for item data.
    The LLM only knows what we told it in the prompt; we never trust it to
    invent or change item metadata.

    Time assignment priority per item:
      - Locked items: always keep their original start_minutes (the user pinned
        them). They are never moved, never window-capped, and never dropped.
      - Unlocked items: use the LLM's start_minutes if it's a valid int 0–1439;
        otherwise fall forward sequentially from the previous item's end time
        so the LLM's *ordering* still drives placement even when times are missing.

    Day-window enforcement:
      - Unlocked items whose start would be at or after DAY_END_MINUTES (21:00)
        are dropped. This is the safety net for an LLM that over-packs a day or
        omits times: without it the sequential fallback clock climbs unbounded and
        produces absurd, multi-day-long "days". Dropping unlocked overflow matches
        the LLM's own job of fitting as many high-priority items as realistically
        fit and omitting the rest.

    Safety guarantees:
      - Hallucinated option_ids (not in original) are silently ignored.
      - Duplicate option_ids in LLM output are ignored after the first occurrence.
      - A day number outside 1..num_days causes the entire output to be rejected
        (returns []) so the caller can fall back to the deterministic schedule.
      - Locked items the LLM omitted are re-attached to their original day at their
        pinned time, so a user-pinned item is never lost. Unlocked omissions stay
        dropped (the LLM's intentional "doesn't fit" decision).

    Returns [] on empty or malformed input — caller should use the deterministic
    schedule as a fallback.
    """
    if not llm_days:
        return []

    # Build a flat lookup of all original items by option_id.
    # This is the authoritative source for item metadata and locked times.
    originals: dict[int, ScheduleItem] = {
        item.option_id: item
        for dp in original_day_plans
        for item in dp.items
    }

    seen_ids: set[int] = set()  # guards against duplicates and tracks what the LLM covered
    result: list[DayPlan] = []

    for day_dict in llm_days:
        day_num = day_dict.get("day")
        # Reject the whole output if any day number is out of range.
        # A hallucinated day 5 on a 3-day trip would silently drop real items.
        if not isinstance(day_num, int) or not (1 <= day_num <= num_days):
            return []

        dp = DayPlan(day_number=day_num)

        # current_time is the sequential fallback clock for this day.
        # When the LLM gives a valid start_minutes we use it directly and
        # advance current_time past that item's end. When the LLM omits or
        # gives an invalid time we place the item at current_time instead,
        # so the LLM's ordering still determines the schedule rather than
        # silently reverting to the original deterministic times.
        current_time = DAY_START_MINUTES

        for item_dict in day_dict.get("items", []):
            opt_id = item_dict.get("option_id")
            orig = originals.get(opt_id)
            # Skip hallucinated option_ids and any item already placed.
            if orig is None or opt_id in seen_ids:
                continue

            # Duration comes from the original item; the LLM doesn't change it.
            duration = orig.duration_minutes or category_default_duration(orig.category)
            note = item_dict.get("note") or None

            if orig.is_locked:
                # Locked items are user-pinned anchors: the LLM may order around
                # them but cannot move their time, and they are never dropped.
                start = orig.start_minutes if orig.start_minutes is not None else current_time
                seen_ids.add(opt_id)
                dp.items.append(ScheduleItem(
                    option_id=orig.option_id, name=orig.name,
                    category=orig.category, latitude=orig.latitude,
                    longitude=orig.longitude, user_rating=orig.user_rating,
                    is_locked=True, day_number=orig.day_number,
                    start_minutes=start,
                    duration_minutes=orig.duration_minutes,
                    note=note,
                ))
                # Keep the fallback clock monotonic so a later untimed item lands
                # after this anchor rather than before it.
                current_time = max(current_time, start + duration)
                continue

            # Unlocked: use the LLM's time when valid; fall forward to current_time
            # otherwise, so the LLM's ordering still drives placement.
            raw_start = item_dict.get("start_minutes")
            valid_start = isinstance(raw_start, int) and 0 <= raw_start <= 1439
            start = raw_start if valid_start else current_time

            # Hard day-window cap: an item that would begin at/after the day end
            # cannot happen. Drop it (the LLM over-packed this day) instead of
            # appending and letting the day balloon past DAY_END_MINUTES. We don't
            # mark it seen, so the LLM may still place it on another day.
            if start >= DAY_END_MINUTES:
                continue

            seen_ids.add(opt_id)
            dp.items.append(ScheduleItem(
                option_id=orig.option_id, name=orig.name,
                category=orig.category, latitude=orig.latitude,
                longitude=orig.longitude, user_rating=orig.user_rating,
                is_locked=False, day_number=day_num,
                start_minutes=start,
                duration_minutes=orig.duration_minutes,
                note=note,
            ))
            # Advance the clock past this item's end so the next item without
            # a valid time starts immediately after.
            current_time = start + duration

        result.append(dp)

    # Locked items the LLM omitted entirely must still survive — they are
    # user-pinned and must never be dropped. Re-attach each missing locked item
    # to its original day at its pinned time. (Unlocked omissions stay dropped:
    # the LLM intentionally drops lower-priority items that don't fit the window.)
    result_by_day: dict[int, DayPlan] = {dp.day_number: dp for dp in result}
    for orig in originals.values():
        if not orig.is_locked or orig.option_id in seen_ids:
            continue
        day = orig.day_number if (orig.day_number and 1 <= orig.day_number <= num_days) else 1
        dp = result_by_day.get(day)
        if dp is None:
            dp = DayPlan(day_number=day)
            result_by_day[day] = dp
            result.append(dp)
        seen_ids.add(orig.option_id)
        dp.items.append(ScheduleItem(
            option_id=orig.option_id, name=orig.name,
            category=orig.category, latitude=orig.latitude,
            longitude=orig.longitude, user_rating=orig.user_rating,
            is_locked=True, day_number=day,
            start_minutes=orig.start_minutes if orig.start_minutes is not None else DAY_START_MINUTES,
            duration_minutes=orig.duration_minutes,
            note=None,
        ))

    # Render each day in time order (None start_minutes sorts last).
    for dp in result:
        dp.items.sort(key=lambda i: i.start_minutes if i.start_minutes is not None else 9999)

    return sorted(result, key=lambda d: d.day_number)


def _day_label(day_number: int, start_date: Optional[date]) -> str:
    """Return a human-readable label for a day number.

    If start_date is known, returns the real date and day-of-week (e.g.
    "Day 1 — Saturday, Jun 07"). Otherwise assumes Day 1 falls on a Saturday
    (a common travel day) so the LLM still has a concrete weekday to check
    opening hours against.
    """
    if start_date is not None:
        day_date = start_date + timedelta(days=day_number - 1)
        return f"Day {day_number} — {day_date.strftime('%A, %b %d')}"
    # No trip start date set: anchor Day 1 to Saturday so hours are meaningful.
    day_names = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    return f"Day {day_number} — {day_names[(day_number - 1) % 7]} (assumed)"


def _build_refine_prompt(
    day_plans: list[DayPlan],
    destination: str,
    start_date: Optional[date] = None,
) -> str:
    input_data = [
        {
            "day": dp.day_number,
            "date": _day_label(dp.day_number, start_date),
            "items": [
                {
                    "option_id": i.option_id,
                    "name": i.name,
                    "category": i.category,
                    "duration_minutes": i.duration_minutes or category_default_duration(i.category),
                    "user_rating": i.user_rating,
                    "start_minutes": i.start_minutes,
                    "is_locked": i.is_locked,
                    "opening_hours": i.opening_hours,
                }
                for i in dp.items
            ],
        }
        for dp in day_plans
    ]
    return (
        f"Destination: {destination}\n"
        f"Day window: {DAY_START_MINUTES} (09:00) – {DAY_END_MINUTES} (21:00)\n\n"
        f"Draft schedule:\n{json.dumps(input_data, indent=2)}\n\n"
        f"Each item's opening_hours is the full weekly schedule from Google Places. "
        f"Use it to verify the item is open on its scheduled day, and move it to a "
        f"different day if it is closed — only drop it if it is closed on every day. "
        f"Prioritise higher-rated items, minimise geographic backtracking, and "
        f"add a brief note per item explaining the placement."
    )


class PlannerAgent(LlmAgent):
    def __init__(self, provider: LLMProvider) -> None:
        super().__init__(provider)

    async def refine(
        self,
        day_plans: list[DayPlan],
        destination: str,
        start_date: Optional[date] = None,
    ) -> tuple[list[dict], str]:
        prompt = _build_refine_prompt(day_plans, destination, start_date)
        text, err = await self.ask(prompt)
        if err:
            return [], err.replace("Agent error", "Planner agent error")
        raw = _extract_json(text)
        if raw is None:
            return [], f"Could not parse planner JSON from response:\n{text[:300]}"
        return raw if isinstance(raw, list) else [], ""
