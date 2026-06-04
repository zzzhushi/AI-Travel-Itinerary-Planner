"""LlmPlanner: Gemini-powered schedule refinement on top of a deterministic draft.

Composes build_schedule (internal) for the draft, then asks the LLM to assign
real clock times, respect opening hours, and minimise geographic backtracking.
Falls back gracefully: returns PlanResult([], "llm", warning) on any failure
so the coordinator can retry with the DeterministicPlanner.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Optional

from src.agents.base import LlmAgent, _extract_json
from src.workers.planner.base import PlanResult
from src.workers.planner.deterministic import build_schedule
from src.workers.planner.types import (
    DAY_END_MINUTES,
    DAY_START_MINUTES,
    DayPlan,
    ScheduleItem,
    category_default_duration,
)
from src.agents.providers import LLMProvider

logger = logging.getLogger(__name__)

PLANNER_INSTRUCTION = """You are a travel itinerary planner. You will receive a draft schedule grouped by day, with each day labelled by its actual date and day of week. For each day, assign a realistic start_minutes (minutes from midnight, e.g. 540 = 09:00) to every item, fitting them within the day window (day_start: 540, day_end: 1260 = 21:00).

Each item includes option_id, name, category, duration_minutes (visit length in minutes), user_rating (1–5, higher = more important), current start_minutes, is_locked, opening_hours (full weekly schedule from Google Places), cluster_id and cluster_name (the geographic neighbourhood group it belongs to), and latitude/longitude.

You will also receive an inter-cluster travel matrix: one-way travel times (in minutes) between the anchor of each cluster. Stops within the SAME cluster are within walking distance of each other (treat them as a short ~10 minute walk); stops in DIFFERENT clusters cost the matrix travel time. Use this geometry — not the raw coordinates — to reason about travel.

Rules:
- Group by cluster, then assign clusters to days. Items sharing a cluster_id are close together, so keep them on the same day whenever possible and visit them consecutively. Avoid hopping back and forth between clusters within a day. One cluster across multiple days if there are multipe high-rated items that can't fit in the same day, but never split a cluster just to put one item on a different day.
- Order clusters within a day to minimise total travel: use the inter-cluster matrix to sequence them so you flow through adjacent clusters rather than backtracking. Within a cluster, order stops freely (they are a short walk apart).
- Account for real travel time between stops. For two consecutive stops in the same cluster, no additional travel time is needed. For consecutive stops in different clusters, allow the matrix travel time between those clusters — this consumes real time and reduces how many stops fit in a day.
- Locked items (is_locked: true) must keep their current start_minutes and day unchanged. When a locked item anchors a cluster on a given day, prefer pulling that cluster's other unscheduled items onto the same day so the anchor's neighbourhood is covered together. Locked items have the highest priority and must be unblocked for the entire duration. They are the immovable anchors around which you schedule everything else.
- Every item must start within the day window: never schedule an item to start at or after day_end (1260 = 21:00), and do not stack items past it.
- Fit as many high-priority items as realistically fit within each day's window, and omit the rest — you do NOT need to place every item. Prioritise higher-rated items (5 > 4 > 3); when items don't fit, drop the lowest-rated ones (omit them from the response entirely).
- Use opening_hours (full week) to schedule each item on a day when it is actually open. If an item is on a day it is closed, move it to any other day in the schedule when it is open. Only drop an item if it is closed on every available day or cannot otherwise fit. Do not place an item outside its open hours for the day it is scheduled.
- If opening_hours is null or unknown, schedule normally without restriction.
- Order items naturally by time of day: sightseeing/culture/nature early, food/shopping midday, nightlife/dinner in the evening — adjust when geographic flow or opening hours demand it.

Respond with a JSON array of days, each with:
- "day": integer (1-based)
- "items": array of objects with "option_id" (int), "name" (str), "start_minutes" (int, minutes from midnight), "note" (brief one-line reason for placement)

Return ONLY the JSON array. No markdown, no extra text."""


def _day_label(day_number: int, start_date: Optional[date]) -> str:
    if start_date is not None:
        day_date = start_date + timedelta(days=day_number - 1)
        return f"Day {day_number} — {day_date.strftime('%A, %b %d')}"
    day_names = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    return f"Day {day_number} — {day_names[(day_number - 1) % 7]} (assumed)"


def _format_travel_matrix(
    travel_matrix: Optional[dict[str, dict[tuple[int, int], dict]]],
) -> str:
    """Render the compact K×K inter-cluster travel matrix as prompt text.

    `travel_matrix` is ``{mode: {(origin_cluster_id, dest_cluster_id): leg}}``
    where a leg is ``{"duration_seconds", "distance_meters"}`` (the shape returned
    by `services.travel.compute_inter_cluster_matrix`). Same-cluster and
    unreachable pairs are already absent. Returns "" when there is nothing to
    show so callers can omit the section entirely.
    """
    if not travel_matrix:
        return ""
    blocks: list[str] = []
    for mode in sorted(travel_matrix):
        legs = travel_matrix[mode]
        lines = [
            f"  cluster {origin} -> cluster {dest}: {round(secs / 60)} min"
            for (origin, dest) in sorted(legs)
            if (secs := (legs[(origin, dest)] or {}).get("duration_seconds")) is not None
        ]
        if lines:
            blocks.append(f"{mode}:\n" + "\n".join(lines))
    if not blocks:
        return ""
    return (
        "\n\nInter-cluster travel times (one-way, minutes between cluster anchors). "
        "Stops within the same cluster are a short walk (~10 min) and are not listed:\n"
        + "\n".join(blocks)
    )


def _build_prompt(
    day_plans: list[DayPlan],
    destination: str,
    start_date: Optional[date] = None,
    travel_matrix: Optional[dict[str, dict[tuple[int, int], dict]]] = None,
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
                    "cluster_id": i.cluster_id,
                    "cluster_name": i.cluster_name,
                    "latitude": i.latitude,
                    "longitude": i.longitude,
                }
                for i in dp.items
            ],
        }
        for dp in day_plans
    ]
    prompt =  (
        f"Destination: {destination}\n"
        f"Day window: {DAY_START_MINUTES} (09:00) – {DAY_END_MINUTES} (21:00)\n\n"
        f"Draft schedule:\n{json.dumps(input_data, indent=2)}"
        f"{_format_travel_matrix(travel_matrix)}\n\n"
        f"Each item's opening_hours is the full weekly schedule from Google Places. "
        f"Use it to verify the item is open on its scheduled day, and move it to a "
        f"different day if it is closed — only drop it if it is closed on every day. "
        f"Keep items sharing a cluster_id together and on the same day, sequence "
        f"clusters using the inter-cluster travel matrix to minimise backtracking, "
        f"prioritise higher-rated items, and add a brief note per item explaining "
        f"the placement."
    )

    logger.warning(prompt)

    return prompt


class PlannerAgent(LlmAgent):
    """Low-level agent that calls the LLM and returns raw JSON. Internal to LlmPlanner."""

    def __init__(self, provider: LLMProvider) -> None:
        super().__init__(provider)

    async def generate_plan(
        self,
        day_plans: list[DayPlan],
        destination: str,
        start_date: Optional[date] = None,
        travel_matrix: Optional[dict[str, dict[tuple[int, int], dict]]] = None,
    ) -> tuple[list[dict], str]:
        prompt = _build_prompt(day_plans, destination, start_date, travel_matrix)
        text, err = await self.ask(prompt)
        if err:
            return [], err.replace("Agent error", "Planner agent error")
        raw = _extract_json(text)
        if raw is None:
            return [], f"Could not parse planner JSON from response:\n{text[:300]}"
        return raw if isinstance(raw, list) else [], ""


def apply_llm_refinement(
    llm_days: list[dict],
    original_day_plans: list[DayPlan],
    num_days: int,
) -> list[DayPlan]:
    """Merge the LLM's scheduling decisions with the original item metadata.

    The LLM controls: which day each item goes on, its start_minutes, and a note.
    Everything else (name, category, lat/lng, duration, user_rating) comes from
    original_day_plans — the authoritative source of truth.

    Validation — returns [] (signals fallback) when:
      - llm_days is empty
      - any day number is outside 1..num_days (hallucinated day)

    Window enforcement:
      - Unlocked items starting at/after DAY_END_MINUTES are dropped (day-window cap).
      - Locked items are never dropped and never window-capped.

    Locked item guarantees:
      - Pre-seeded first (Phase 0) on their pinned day at their pinned time, before
        any LLM output is processed. This means the LLM's unlocked items are always
        placed around the locked anchors, not on top of them.
      - The fallback clock for each day starts at the end of the latest locked anchor
        on that day, so untimed unlocked items naturally follow locked slots.
      - seen_ids prevents any option_id from appearing more than once in the result.
    """
    if not llm_days:
        return []

    # -----------------------------------------------------------------------
    # Phase 1: build a flat lookup of every original item by option_id.
    # This is the authoritative source for metadata (name, category, duration,
    # locked state, pinned start_minutes). We never trust the LLM to supply
    # or mutate these fields.
    # -----------------------------------------------------------------------
    originals: dict[int, ScheduleItem] = {
        item.option_id: item
        for dp in original_day_plans
        for item in dp.items
    }

    # seen_ids tracks items that have been placed. Every append is preceded by
    # seen_ids.add(), so the same option_id can never appear twice in the result.
    seen_ids: set[int] = set()
    result_by_day: dict[int, DayPlan] = {}
    # Per-day fallback clock seed: end time of the latest locked anchor per day.
    # Unlocked items without an explicit LLM time start here, flowing after locks.
    day_clock_seed: dict[int, int] = {}

    def _get_or_create_day(day_num: int) -> DayPlan:
        if day_num not in result_by_day:
            result_by_day[day_num] = DayPlan(day_number=day_num)
        return result_by_day[day_num]

    # -----------------------------------------------------------------------
    # Phase 0: pre-seed every locked item onto its pinned day before processing
    # LLM output. This ensures locked anchors are always in the result and that
    # Phase 2 never places an unlocked item on top of a locked one due to the
    # LLM moving the locked item to a different day in its output.
    # -----------------------------------------------------------------------
    for orig in originals.values():
        if not orig.is_locked:
            continue
        day = orig.day_number if (orig.day_number and 1 <= orig.day_number <= num_days) else 1
        start = orig.start_minutes if orig.start_minutes is not None else DAY_START_MINUTES
        duration = orig.duration_minutes or category_default_duration(orig.category)
        seen_ids.add(orig.option_id)
        _get_or_create_day(day).items.append(ScheduleItem(
            option_id=orig.option_id, name=orig.name,
            category=orig.category, latitude=orig.latitude,
            longitude=orig.longitude, user_rating=orig.user_rating,
            is_locked=True, day_number=day,
            start_minutes=start,
            duration_minutes=orig.duration_minutes,
            note=None,
        ))
        # Seed the fallback clock for this day past the end of this locked slot.
        day_clock_seed[day] = max(day_clock_seed.get(day, DAY_START_MINUTES), start + duration)

    # -----------------------------------------------------------------------
    # Phase 1: process the LLM's unlocked scheduling decisions day by day.
    # Locked items are already in seen_ids (Phase 0), so they are skipped here.
    # The fallback clock starts at the end of the last locked anchor for the day
    # so untimed unlocked items naturally follow the pinned slots.
    # If the LLM repeated the same day number, _get_or_create_day reuses the
    # existing DayPlan so all items land in the same bucket.
    # -----------------------------------------------------------------------
    for day_dict in llm_days:
        day_num = day_dict.get("day")
        if not isinstance(day_num, int) or not (1 <= day_num <= num_days):
            return []

        dp = _get_or_create_day(day_num)
        # Start the fallback clock after any locked anchors on this day.
        current_time = day_clock_seed.get(day_num, DAY_START_MINUTES)

        for item_dict in day_dict.get("items", []):
            opt_id = item_dict.get("option_id")
            orig = originals.get(opt_id)
            if orig is None or opt_id in seen_ids:
                continue  # hallucinated id, already placed (including all locked items)

            duration = orig.duration_minutes or category_default_duration(orig.category)
            note = item_dict.get("note") or None

            # Unlocked: use the LLM's start_minutes when valid (0–1439),
            # otherwise fall forward to current_time.
            raw_start = item_dict.get("start_minutes")
            valid_start = isinstance(raw_start, int) and 0 <= raw_start <= 1439
            start = raw_start if valid_start else current_time

            # Day-window cap: drop items that would start at or after 21:00.
            # Don't mark seen — the LLM may still place this item on another day.
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
            current_time = start + duration

    result = list(result_by_day.values())
    for dp in result:
        dp.items.sort(key=lambda i: i.start_minutes if i.start_minutes is not None else 9999)

    return sorted(result, key=lambda d: d.day_number)


class LlmPlanner:
    """Gemini-powered planner: builds a deterministic draft, refines via LLM.

    Returns PlanResult([], "llm", warning) on any failure so the coordinator
    can fall back to DeterministicPlanner transparently.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._agent = PlannerAgent(provider)

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
        draft = build_schedule(options, num_days=num_days, min_rating=min_rating)
        llm_days, err = await self._agent.generate_plan(
            draft, destination or "", start_date=start_date, travel_matrix=travel_matrix
        )
        if err:
            return PlanResult([], "llm", f"LLM refinement skipped: {err}")

        refined = apply_llm_refinement(llm_days, draft, num_days)
        if not refined or not any(dp.items for dp in refined):
            return PlanResult([], "llm", "LLM produced an unusable schedule.")

        return PlanResult(day_plans=refined, source="llm")
