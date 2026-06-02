"""
Planner agent: takes rated options and produces a day-by-day schedule.

Strategy:
1. Filter to options with user_rating >= min_rating (default 3).
2. Distribute free items round-robin across num_days.
3. Assign time slots (morning / afternoon / evening) based on category heuristics.
4. Locked items are never moved.

The Gemini agent is used to produce the final human-readable schedule summary
and resolve ambiguous ordering within a day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from src.agents.base import LlmAgent, _extract_json
from src.agents.providers import LLMProvider

# Time slot heuristics by category
_CATEGORY_SLOT: dict[str, str] = {
    "sightseeing": "morning",
    "culture": "morning",
    "nature": "morning",
    "food": "afternoon",
    "shopping": "afternoon",
    "nightlife": "evening",
    "accommodation": "morning",
    "transport": "morning",
    "other": "afternoon",
}

PLANNER_INSTRUCTION = """You are a travel itinerary planner. Given a list of activities grouped by day, produce an optimized day-by-day schedule.

Each item includes option_id, name, category, time_slot, and is_locked.

For each day:
- Minimize backtracking: order activities so travel flows logically by neighbourhood or direction, reducing unnecessary cross-city back-and-forth. Use your knowledge of the destination's geography.
- Locked items (is_locked: true) are fixed anchors — do not move them to a different day or time slot. Group geographically nearby unlocked items onto the same day as a locked anchor where possible.
- Order by time of day: sightseeing/culture/nature in the morning, food/shopping in the afternoon, food/nightlife in the evening — adjust when geographic flow demands it.

Respond with a JSON array of days, each with:
- "day": integer (1-based)
- "items": array of objects with "option_id", "name", "time_slot", "note" (brief one-line reason for placement)

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
    time_slot: Optional[str] = None


@dataclass
class DayPlan:
    day_number: int
    items: list[ScheduleItem] = field(default_factory=list)


def _assign_time_slots(day_plan: DayPlan) -> None:
    """Assign morning/afternoon/evening based on category, spread within a day."""
    slot_order = ["morning", "afternoon", "evening"]
    slot_counts: dict[str, int] = {"morning": 0, "afternoon": 0, "evening": 0}

    for item in day_plan.items:
        if item.is_locked and item.time_slot:
            continue  # Respect locked time slot
        preferred = _CATEGORY_SLOT.get(item.category, "afternoon")
        # If preferred slot is crowded (>2 items), move to next slot
        if slot_counts[preferred] >= 2:
            idx = slot_order.index(preferred)
            preferred = slot_order[min(idx + 1, 2)]
        item.time_slot = preferred
        slot_counts[preferred] += 1


def build_schedule(
    options: list[dict],
    num_days: int,
    locked_items: Optional[list[dict]] = None,
    min_rating: int = 3,
) -> list[DayPlan]:
    """
    Pure Python schedule builder (no LLM call). Used as the base schedule.

    options: list of dicts with keys: option_id, name, category, latitude, longitude,
             user_rating, is_locked, day_number (optional), time_slot (optional)
    locked_items: pre-placed items that must not move
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
            time_slot=o.get("time_slot"),
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

    # Assign time slots per day
    for day_plan in days.values():
        _assign_time_slots(day_plan)

    return sorted(days.values(), key=lambda d: d.day_number)


def apply_llm_refinement(
    llm_days: list[dict],
    original_day_plans: list[DayPlan],
) -> list[DayPlan]:
    """Convert LLM JSON output back into DayPlan objects.

    Returns [] if the output is malformed or empty — caller should fall back
    to the deterministic schedule.

    Guarantees:
    - Locked items keep their original day_number and time_slot.
    - Items the LLM dropped are re-added at their original placement.
    - Hallucinated option_ids (not in the original) are silently ignored.
    """
    if not llm_days:
        return []

    originals: dict[int, ScheduleItem] = {
        item.option_id: item
        for dp in original_day_plans
        for item in dp.items
    }

    seen_ids: set[int] = set()
    result: list[DayPlan] = []

    for day_dict in llm_days:
        day_num = day_dict.get("day")
        if not isinstance(day_num, int):
            return []
        dp = DayPlan(day_number=day_num)
        for item_dict in day_dict.get("items", []):
            opt_id = item_dict.get("option_id")
            orig = originals.get(opt_id)
            if orig is None or opt_id in seen_ids:
                continue
            seen_ids.add(opt_id)
            if orig.is_locked:
                dp.items.append(ScheduleItem(
                    option_id=orig.option_id, name=orig.name,
                    category=orig.category, latitude=orig.latitude,
                    longitude=orig.longitude, user_rating=orig.user_rating,
                    is_locked=True, day_number=orig.day_number,
                    time_slot=orig.time_slot,
                ))
            else:
                dp.items.append(ScheduleItem(
                    option_id=orig.option_id, name=orig.name,
                    category=orig.category, latitude=orig.latitude,
                    longitude=orig.longitude, user_rating=orig.user_rating,
                    is_locked=False, day_number=day_num,
                    time_slot=item_dict.get("time_slot") or orig.time_slot,
                ))
        result.append(dp)

    # Re-add items the LLM dropped at their original placement
    for opt_id, orig in originals.items():
        if opt_id not in seen_ids:
            day_num = orig.day_number or 1
            target = next((d for d in result if d.day_number == day_num), None)
            if target is None:
                target = DayPlan(day_number=day_num)
                result.append(target)
            target.items.append(orig)

    return sorted(result, key=lambda d: d.day_number)


def _build_refine_prompt(day_plans: list[DayPlan], destination: str) -> str:
    input_data = [
        {
            "day": dp.day_number,
            "items": [
                {
                    "option_id": i.option_id,
                    "name": i.name,
                    "category": i.category,
                    "time_slot": i.time_slot,
                    "is_locked": i.is_locked,
                }
                for i in dp.items
            ],
        }
        for dp in day_plans
    ]
    return (
        f"Destination: {destination}\n\n"
        f"Draft schedule:\n{json.dumps(input_data, indent=2)}\n\n"
        f"Improve the ordering and add a brief note for each item explaining the logic."
    )


class PlannerAgent(LlmAgent):
    def __init__(self, provider: LLMProvider) -> None:
        super().__init__(provider)

    async def refine(
        self,
        day_plans: list[DayPlan],
        destination: str,
    ) -> tuple[list[dict], str]:
        prompt = _build_refine_prompt(day_plans, destination)
        text, err = await self.ask(prompt)
        if err:
            return [], err.replace("Agent error", "Planner agent error")
        raw = _extract_json(text)
        if raw is None:
            return [], f"Could not parse planner JSON from response:\n{text[:300]}"
        return raw if isinstance(raw, list) else [], ""
