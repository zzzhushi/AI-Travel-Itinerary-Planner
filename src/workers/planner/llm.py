"""LlmPlanner: Gemini-powered schedule refinement on top of a deterministic draft.

Composes build_schedule (internal) for the draft, then asks the LLM to assign
real clock times, respect opening hours, and minimise geographic backtracking.
Falls back gracefully: returns PlanResult([], "llm", warning) on any failure
so the coordinator can retry with the DeterministicPlanner.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import TYPE_CHECKING, Optional

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

if TYPE_CHECKING:
    from src.workers.preferences import Preferences

_PACE_HINT = {
    "relaxed": "Relaxed — fewer stops with generous buffers, spread across the full day into the evening; lower density, not a shorter day.",
    "balanced": "Balanced — a comfortable number of stops; neither rushed nor empty.",
    "packed": "Packed — fit as much as fits well; keep the day active toward day_end.",
}
_BUDGET_HINT = {
    "budget": "Budget — prefer affordable stops; a higher price_level is a mild negative.",
    "mid_range": "Mid-range — prefer moderately priced stops.",
    "splurge": "Splurge — premium/high price_level stops are welcome.",
}

logger = logging.getLogger(__name__)

PLANNER_INSTRUCTION = """You are a travel itinerary planner. You are given a POOL of candidate stops grouped by geographic cluster, the number of trip days (each with its weekday), and an inter-cluster travel matrix. Select the best stops and arrange them into a geographically coherent day-by-day itinerary. You will almost always have more candidates than fit — choose well and drop the rest.

Each stop has: option_id, name, category, user_rating (1–5, higher = more important), duration_minutes (how long to stay), opening_hours (full weekly schedule, or "unknown"), a cluster (cluster_id + name with an approximate centroid), and whether it is LOCKED.

The inter-cluster travel matrix gives one-way travel times (minutes) between cluster anchors. Stops in the SAME cluster are a short walk apart (~10 min); stops in DIFFERENT clusters cost the matrix time. Use this — not raw coordinates — to reason about travel.

How to plan:
1. Minimise total travel; group by area. A day may span up to ~3 clusters, but every move between clusters costs the matrix travel time and eats the day. Sequence a day's clusters so you flow through adjacent areas in one direction — never backtrack (no A→B→A). Crossing the city is worth it for a high-priority stop, not for a low-rated one; balance the best stops against wasted back-and-forth.
2. Keep clusters together. Stops sharing a cluster_id go on the same day and are visited consecutively; only split a cluster when it has more good stops than fit in one day.
3. Anchor each day. If a day has LOCKED items, its area is fixed — build around them and pull in their cluster's other stops. If nothing is locked, pick the highest-rated major attraction (sightseeing/culture/nature) in an area as that day's anchor, then add nearby stops.
4. Match the requested pace (see Traveler preferences below; default balanced). A packed pace fills the whole day window — keep placing stops through the evening rather than stopping after the afternoon, pulling in the next-best nearby stops (an adjacent cluster is fine) before leaving hours empty. A relaxed pace deliberately schedules fewer stops with breathing room between them; do not over-fill. Always prioritise rating 5 > 4 > 3, and only skip a stop when it would need a long detour for little payoff.
5. Budget real travel as hard arithmetic. Each stop's start_minutes MUST be at least the previous stop's start_minutes + its duration_minutes + travel from the previous stop (~10 min within a cluster; the matrix time between clusters). Never place two stops closer together than that — underestimating travel between neighborhoods is the most common mistake.
6. Durations are the user's choice — honor them as fact. Schedule each stop for exactly its given duration_minutes; never shorten or lengthen it to be "realistic" (if a theme park is set to 30 min, use 30 min).

Then schedule each day:
- Assign every chosen stop a start_minutes (minutes from midnight, e.g. 540 = 09:00), within the day window given below (day_start to day_end). Never start a stop at or after day_end, and do not stack items past it.
- Use each stop at most once in the entire trip: never repeat an option_id — not twice in one day, and not on two different days. If you want the same kind of stop again (e.g. another dinner), choose a different option_id.
- LOCKED items must keep their exact day and start_minutes — they are immovable anchors; schedule everything else around them.
- Respect opening_hours for the stop's weekday: only place a stop when it is open; if it is closed that day, move it to another day when it is open, or drop it if it is closed every day. If opening_hours is "unknown" or null, schedule freely.
- Meal cadence: unless the traveler says otherwise, give each day one lunch (~12:00–13:30) and one dinner (~18:00–19:30) drawn from food stops. Honor any explicit meal/snack requests in the Traveler preferences as ordered, time-bound slots:
  - "coffee/pastry in the morning/afternoon" → place a cafe or pastry stop before lunch or in the early afternoon.
  - "dessert after dinner" → on that day, place a dessert/sweets stop whose start_minutes is AFTER the dinner stop ends (dinner start + dinner duration), the same evening. Do NOT satisfy a post-dinner dessert with a daytime cafe — ordering matters.
- Use the whole day window regardless of pace: don't end a day in the early or mid-afternoon while suitable, open stops remain — the last stop should fall in the evening (within ~2 hours of day_end). Pace controls density, NOT the length of the day: a packed day fills the window with more stops; a relaxed day has fewer stops with larger buffers but still spans into the evening. Never compress everything into the morning and finish early. (If genuinely no worthwhile, open candidates remain, a shorter day is fine.)
- Order each day by time of day and geographic flow: sightseeing/culture/nature earlier, food/shopping midday, nightlife/dinner in the evening — adjust for opening hours and to avoid backtracking.

Before responding, silently verify each day against the traveler's requirements and fix any violation first:
- every requested meal/snack slot is present and correctly ordered (e.g. a post-dinner dessert starts after dinner ends);
- no option_id repeats within a day or across days;
- every start_minutes respects opening_hours, the day window, and the travel arithmetic in rule 5.
Repair the plan to satisfy these before emitting JSON. Do not include this checklist in your output.

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


def _hhmm(minutes: Optional[int]) -> str:
    """Format minutes-from-midnight as HH:MM (or ??:?? when unset)."""
    if minutes is None:
        return "??:??"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _centroid(members: list[ScheduleItem]) -> str:
    """Approximate ' (~lat,lng)' centroid of a cluster's members, or '' if no coords."""
    pts = [
        (m.latitude, m.longitude)
        for m in members
        if m.latitude is not None and m.longitude is not None
    ]
    if not pts:
        return ""
    lat = sum(p[0] for p in pts) / len(pts)
    lng = sum(p[1] for p in pts) / len(pts)
    return f" (~{lat:.3f},{lng:.3f})"


def _format_locks(items: list[ScheduleItem]) -> str:
    """Render the locked-items callout (fixed anchors), or the no-locks hint."""
    locked = [i for i in items if i.is_locked]
    if not locked:
        return (
            "Locked anchors: none — choose each day's anchor yourself "
            "(start from the highest-rated major attraction in an area)."
        )
    lines = [
        f"  - opt {i.option_id} \"{i.name}\" → day {i.day_number or 1}, "
        f"{_hhmm(i.start_minutes)}, cluster {i.cluster_id if i.cluster_id is not None else '?'}"
        for i in sorted(locked, key=lambda x: (x.day_number or 0, x.start_minutes or 0))
    ]
    return (
        "Locked anchors (FIXED day + time — build each day's area around these, "
        "they cannot move):\n" + "\n".join(lines)
    )


def _format_candidate_pool(items: list[ScheduleItem]) -> str:
    """Group every candidate stop by cluster, each with a centroid and compact lines.

    Clusters are listed by id; members within a cluster are listed highest-rated
    first so the best picks are surfaced. Items with no cluster_id fall under a
    trailing 'Unclustered' heading.
    """
    by_cluster: dict[Optional[int], list[ScheduleItem]] = defaultdict(list)
    for i in items:
        by_cluster[i.cluster_id].append(i)

    ordered_ids = sorted(c for c in by_cluster if c is not None)
    if None in by_cluster:
        ordered_ids.append(None)

    blocks: list[str] = []
    for cid in ordered_ids:
        members = by_cluster[cid]
        if cid is None:
            header = "Unclustered (no geographic group):"
        else:
            name = next((m.cluster_name for m in members if m.cluster_name), str(cid))
            header = f"Cluster {cid} — {name}{_centroid(members)}:"
        lines = [header]
        for m in sorted(members, key=lambda x: (-(x.user_rating or 0), x.option_id)):
            dur = m.duration_minutes or category_default_duration(m.category)
            tag = (
                f" [LOCKED day {m.day_number or 1} @ {_hhmm(m.start_minutes)}]"
                if m.is_locked else ""
            )
            lines.append(
                f"  - opt {m.option_id}: {m.name} "
                f"[{m.category}, rating {m.user_rating}, {dur} min]{tag}"
            )
            lines.append(f"    hours: {m.opening_hours or 'unknown'}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _format_preferences(preferences: Optional[Preferences]) -> str:
    """Render the Traveler preferences block, or "" when nothing is set.

    Pace/budget/interests/notes bias selection and pacing; the day window is
    rendered in the header line, not here.
    """
    if preferences is None:
        return ""
    lines: list[str] = []
    if preferences.pace:
        lines.append(f"- Pace: {_PACE_HINT.get(preferences.pace, preferences.pace)}")
    if preferences.budget:
        lines.append(f"- Budget: {_BUDGET_HINT.get(preferences.budget, preferences.budget)}")
    if preferences.interests:
        lines.append(
            f"- Interests: {', '.join(preferences.interests)} "
            f"(use as a tie-breaker when choosing among nearby candidates)."
        )
    if preferences.notes:
        lines.append(f"- Notes: {preferences.notes} (honor when feasible).")
    if not lines:
        return ""
    return "\n\nTraveler preferences:\n" + "\n".join(lines)


def _build_prompt(
    day_plans: list[DayPlan],
    destination: str,
    start_date: Optional[date] = None,
    travel_matrix: Optional[dict[str, dict[tuple[int, int], dict]]] = None,
    preferences: Optional[Preferences] = None,
) -> str:
    # Flatten the round-robin draft into a single candidate pool. The draft's
    # per-day binning is arbitrary (round-robin by option_id) and is deliberately
    # NOT shown to the model — it must assign days from geography. Locked items
    # keep their pinned day/time via the locks callout.
    items = [item for dp in day_plans for item in dp.items]
    num_days = max((dp.day_number for dp in day_plans), default=1)
    day_line = "; ".join(_day_label(d, start_date) for d in range(1, num_days + 1))

    day_start = preferences.day_start_minutes if preferences else DAY_START_MINUTES
    day_end = preferences.day_end_minutes if preferences else DAY_END_MINUTES

    prompt = (
        f"Destination: {destination}\n"
        f"Trip length: {num_days} day(s) — {day_line}\n"
        f"Day window each day: day_start {day_start} ({_hhmm(day_start)}) – "
        f"day_end {day_end} ({_hhmm(day_end)})\n"
        f"{_format_preferences(preferences)}\n\n"
        f"{_format_locks(items)}\n\n"
        f"Candidate stops grouped by geographic cluster (you have more than fit — "
        f"select the best and drop the rest):\n\n"
        f"{_format_candidate_pool(items)}"
        f"{_format_travel_matrix(travel_matrix)}\n\n"
        f"Build a {num_days}-day itinerary: assign each chosen stop to a day and a "
        f"start_minutes. Group stops by cluster, sequence clusters to minimise total "
        f"travel (subtract inter-cluster travel time from the day), respect opening "
        f"hours and locked anchors, keep each stop's given duration, and add a brief "
        f"note per item explaining the placement."
    )

    logger.warning("Prompt length: %d characters", len(prompt))

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
        preferences: Optional[Preferences] = None,
    ) -> tuple[list[dict], str]:
        prompt = _build_prompt(day_plans, destination, start_date, travel_matrix, preferences)
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
    day_start: int = DAY_START_MINUTES,
    day_end: int = DAY_END_MINUTES,
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
        start = orig.start_minutes if orig.start_minutes is not None else day_start
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
        day_clock_seed[day] = max(day_clock_seed.get(day, day_start), start + duration)

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
        current_time = day_clock_seed.get(day_num, day_start)

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

            # Day-window cap: drop items that would start at or after day_end.
            # Don't mark seen — the LLM may still place this item on another day.
            if start >= day_end:
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
        preferences: Optional[Preferences] = None,
    ) -> PlanResult:
        day_start = preferences.day_start_minutes if preferences else DAY_START_MINUTES
        day_end = preferences.day_end_minutes if preferences else DAY_END_MINUTES
        draft = build_schedule(
            options, num_days=num_days, min_rating=min_rating, day_start=day_start,
        )
        llm_days, err = await self._agent.generate_plan(
            draft, destination or "", start_date=start_date,
            travel_matrix=travel_matrix, preferences=preferences,
        )
        if err:
            return PlanResult([], "llm", f"LLM refinement skipped: {err}")

        refined = apply_llm_refinement(llm_days, draft, num_days, day_start, day_end)
        if not refined or not any(dp.items for dp in refined):
            return PlanResult([], "llm", "LLM produced an unusable schedule.")

        return PlanResult(day_plans=refined, source="llm")
