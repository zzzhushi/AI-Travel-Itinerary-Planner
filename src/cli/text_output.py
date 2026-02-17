"""Format research and itinerary results as plain text for file output."""

from __future__ import annotations

from typing import Any


def format_research_results(results: list[dict[str, Any]]) -> str:
    """Format research results as human-readable text."""
    lines = [
        "Research results – activity options",
        "=" * 50,
        "",
    ]
    for i, row in enumerate(results, 1):
        lines.append(f"[{i}] {row.get('activity_query', '')}")
        lines.append(f"    Option: {row.get('option_name', '')}")
        lines.append(f"    Address: {row.get('address', '')}")
        lines.append(f"    Location: {row.get('location', '')}")
        if row.get("link"):
            lines.append(f"    Link: {row.get('link', '')}")
        lines.append("")
    return "\n".join(lines)


def format_itinerary_plan(plan: list[dict[str, Any]], title: str = "Itinerary") -> str:
    """Format itinerary plan (full or take-it-easy) as human-readable text."""
    lines = [
        title,
        "=" * 50,
        "",
    ]
    for day in plan:
        day_label = day.get("date_label") or f"Day {day.get('day', '?')}"
        lines.append(f"  {day_label}")
        lines.append("-" * 40)
        for slot in day.get("slots") or []:
            time_range = f"{slot.get('start', '')} – {slot.get('end', '')}"
            activity = slot.get("activity", "")
            location = slot.get("location", "")
            loc_str = f" ({location})" if location else ""
            lines.append(f"  {time_range}  {activity}{loc_str}")
            meta = []
            if slot.get("travel_buffer_mins"):
                meta.append(f"{slot['travel_buffer_mins']} min travel")
            if slot.get("is_meal"):
                meta.append("Meal")
            if slot.get("is_rest"):
                meta.append("Rest")
            if meta:
                lines.append(f"             {' | '.join(meta)}")
        lines.append("")
    return "\n".join(lines)
