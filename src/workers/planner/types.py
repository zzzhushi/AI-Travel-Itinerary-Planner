"""Shared domain types and constants for the planner package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

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

# Used by the deterministic planner to sort items within a day before
# assigning sequential start times.
_CATEGORY_ORDER: dict[str, int] = {
    "sightseeing": 0, "culture": 0, "nature": 0,
    "accommodation": 0, "transport": 0,
    "food": 1, "shopping": 1, "other": 1,
    "nightlife": 2,
}

# Legacy slot anchors — kept for DB backfill reads; no longer written by the planner.
SLOT_START_MINUTES: dict[str, int] = {
    "morning": 540, "afternoon": 780, "evening": 1080,
}


def category_default_duration(category: Optional[str]) -> int:
    """Typical visit length (minutes) for a category; falls back to DEFAULT_DURATION_MINUTES.

    Accepts ANY category string (case-insensitive) — including future user-defined
    categories — and falls back gracefully.
    """
    return _CATEGORY_DURATION.get((category or "").lower(), DEFAULT_DURATION_MINUTES)


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
    opening_hours: Optional[str] = None   # raw Places API hours string
    # Geographic cluster this item belongs to, stamped by the clustering pass
    # (src/workers/planner/clustering.py). cluster_id is a per-trip index;
    # cluster_name is the modal-neighborhood label. Both None until clustered.
    cluster_id: Optional[int] = None
    cluster_name: Optional[str] = None


@dataclass
class DayPlan:
    day_number: int
    items: list[ScheduleItem] = field(default_factory=list)
