"""Projecting trip logistics (travel + lodging) into planner inputs.

Decoupled from the DB: callers (the service layer) pass plain dicts, so the
workers package never imports ORM models — same boundary as the Preferences DTO.

Two projections:
- `build_day_window_fn` turns arrival/departure travel into a per-day
  `DayWindowFn` (arrival raises a day's start; departure lowers its end).
- `travel_locked_items` turns each travel row into a locked "transport"
  ScheduleItem so it is pinned on the timeline and shown to the LLM as an anchor.
"""

from __future__ import annotations

from src.workers.planner.types import (
    DAY_END_MINUTES,
    DAY_START_MINUTES,
    DayWindow,
    DayWindowFn,
)


def build_day_window_fn(
    travel: list[dict],
    *,
    base_start: int = DAY_START_MINUTES,
    base_end: int = DAY_END_MINUTES,
) -> DayWindowFn:
    """Build a per-day window resolver from arrival/departure travel rows.

    `travel` items are dicts with keys: kind ("arrival"|"departure"), day_number,
    time_minutes, transit_minutes. For a day:
    - arrival → start = max(base_start, time + transit) (can't start before you land + transit)
    - departure → end = min(base_end, time - transit) (must leave for the airport)
    Days without travel get the base window. With no travel at all, every day is
    the base window — identical to the previous global behavior.
    """
    start_by_day: dict[int, int] = {}
    end_by_day: dict[int, int] = {}
    for t in travel:
        day = t.get("day_number")
        time = t.get("time_minutes")
        if day is None or time is None:
            continue
        transit = t.get("transit_minutes") or 0
        if t.get("kind") == "arrival":
            cand = time + transit
            start_by_day[day] = max(start_by_day.get(day, base_start), cand)
        elif t.get("kind") == "departure":
            cand = time - transit
            end_by_day[day] = min(end_by_day.get(day, base_end), cand)

    def window(day_number: int) -> DayWindow:
        start = start_by_day.get(day_number, base_start)
        end = end_by_day.get(day_number, base_end)
        # Guard against an inverted window (e.g. an arrival late in the day):
        # never let start exceed end.
        if start > end:
            start = end
        return DayWindow(start, end)

    return window


# Synthetic option_ids for projected logistics use a distinct negative range so
# they never collide with real Option.id values (always positive).
_TRAVEL_ID_BASE = -1_000_000


def _travel_duration(t: dict) -> int:
    """How long the travel anchor blocks the timeline (the transit buffer)."""
    return max(int(t.get("transit_minutes") or 0), 15)


def travel_option_dicts(travel: list[dict]) -> list[dict]:
    """Project arrival/departure rows into locked "transport" option-dicts.

    Same shape as `get_rated_options_for_schedule` rows, so the service can just
    append them to `options`: both planners then pin them (is_locked + day_number
    + start_minutes) and the LLM sees them as immovable anchors. Rows missing a
    day or time are skipped (no fixed slot to anchor).
    """
    out: list[dict] = []
    for t in travel:
        day = t.get("day_number")
        time = t.get("time_minutes")
        if day is None or time is None:
            continue
        label = t.get("label") or t.get("mode") or t.get("kind")
        verb = "Arrive" if t.get("kind") == "arrival" else "Depart"
        out.append(
            {
                "option_id": _TRAVEL_ID_BASE - (t.get("id") or len(out)),
                "name": f"{verb} · {label}",
                "category": "transport",
                "latitude": t.get("latitude"),
                "longitude": t.get("longitude"),
                "user_rating": 5,
                "is_locked": True,
                "day_number": day,
                "start_minutes": time,
                "default_duration_minutes": _travel_duration(t),
            }
        )
    return out
