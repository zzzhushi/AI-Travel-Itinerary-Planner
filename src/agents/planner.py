"""
Planner agent: takes rated options and produces a geo-aware day-by-day schedule.

Strategy:
1. Filter to options with user_rating >= min_rating (default 3).
2. If lat/lng available, cluster options by proximity so nearby places share a day.
3. Distribute clusters evenly across num_days.
4. Assign time slots (morning / afternoon / evening) based on category heuristics.
5. Locked items are never moved.

The Gemini agent is used to produce the final human-readable schedule summary
and resolve ambiguous ordering within a day. The clustering logic is deterministic Python.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Optional

from src.agents.base import LlmAgent, _extract_json
from src.tools.maps import haversine_km

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

PLANNER_INSTRUCTION = """You are a travel itinerary planner. Given a list of activities grouped by day,
produce a clear, friendly day-by-day schedule.

For each day:
- Order activities sensibly (morning sights before evening dinner, etc.)
- Note if activities are geographically close
- Keep it concise: one line per activity

Respond with a JSON array of days, each with:
- "day": integer (1-based)
- "items": array of objects with "option_id", "name", "time_slot", "note" (brief reason for ordering)

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


def _cluster_by_proximity(items: list[ScheduleItem], num_days: int) -> list[list[ScheduleItem]]:
    """
    Simple greedy clustering: group items so that nearby ones share a day.
    Items without coordinates are distributed round-robin.
    """
    with_coords = [i for i in items if i.latitude and i.longitude]
    without_coords = [i for i in items if not (i.latitude and i.longitude)]

    clusters: list[list[ScheduleItem]] = [[] for _ in range(num_days)]

    if not with_coords:
        # No geo data — distribute evenly
        for idx, item in enumerate(items):
            clusters[idx % num_days].append(item)
        return clusters

    # Sort by rating desc so high-rated items anchor each day
    with_coords.sort(key=lambda x: -(x.user_rating or 0))

    # Greedy: assign each item to the day whose centroid is closest
    # Seed each day with one item first
    seeds = with_coords[:num_days]
    remainder = with_coords[num_days:]
    for i, seed in enumerate(seeds):
        clusters[i].append(seed)

    for item in remainder:
        best_day = _nearest_cluster(item, clusters)
        clusters[best_day].append(item)

    # Distribute items without coords round-robin
    days_cycle = list(range(num_days))
    for idx, item in enumerate(without_coords):
        clusters[days_cycle[idx % num_days]].append(item)

    return clusters


def _nearest_cluster(item: ScheduleItem, clusters: list[list[ScheduleItem]]) -> int:
    best_day, best_dist = 0, float("inf")
    for day_idx, cluster in enumerate(clusters):
        if not cluster:
            return day_idx  # Empty cluster — fill it
        coords = [(c.latitude, c.longitude) for c in cluster if c.latitude and c.longitude]
        if not coords:
            continue
        centroid_lat = sum(c[0] for c in coords) / len(coords)
        centroid_lon = sum(c[1] for c in coords) / len(coords)
        dist = haversine_km(item.latitude, item.longitude, centroid_lat, centroid_lon)
        if dist < best_dist:
            best_dist, best_day = dist, day_idx
    return best_day


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

    # Cluster free items into days
    clusters = _cluster_by_proximity(free, num_days)
    for day_idx, cluster in enumerate(clusters):
        day_num = day_idx + 1
        for item in cluster:
            item.day_number = day_num
            days[day_num].items.append(item)

    # Assign time slots per day
    for day_plan in days.values():
        _assign_time_slots(day_plan)

    return sorted(days.values(), key=lambda d: d.day_number)


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
    def __init__(self) -> None:
        super().__init__(
            name="PlannerAgent",
            instruction=PLANNER_INSTRUCTION,
            tools=[],  # add google_search, maps tool here when ready
            retry_attempts=3,
            retry_exp_base=5,
        )

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


# Lazy singleton — constructed on first call so env vars are loaded before init.
_planner: PlannerAgent | None = None


async def refine_schedule_with_llm(
    day_plans: list[DayPlan],
    destination: str,
) -> tuple[list[dict], str]:
    """Optional LLM pass to improve ordering and add notes within each day.

    Returns (refined_days, error_message).
    Each day: {"day": int, "items": [{"option_id", "name", "time_slot", "note"}]}
    """
    global _planner
    if _planner is None:
        _planner = PlannerAgent()
    return await _planner.refine(day_plans, destination)
