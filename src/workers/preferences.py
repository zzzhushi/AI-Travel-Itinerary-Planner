"""Traveler preferences DTO shared by the researcher and the planner.

`Preferences` is a plain, immutable value object decoupled from the ORM. Both
`ResearcherAgent` and the planner strategies consume it, so it lives at the
workers level (workers must not import from services). `from_orm` resolves the
nullable `TripPreferences` columns to concrete defaults — NULL window edges fall
back to the planner's DAY_START_MINUTES / DAY_END_MINUTES — so callers never have
to special-case "no preferences set".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.workers.planner.types import DAY_END_MINUTES, DAY_START_MINUTES


@dataclass(frozen=True)
class Preferences:
    pace: Optional[str] = None            # one of models.PACE_CHOICES, or None
    budget: Optional[str] = None          # one of models.BUDGET_CHOICES, or None
    interests: list[str] = field(default_factory=list)
    notes: Optional[str] = None
    day_start_minutes: int = DAY_START_MINUTES
    day_end_minutes: int = DAY_END_MINUTES

    @classmethod
    def from_orm(cls, prefs) -> "Preferences":
        """Build from a TripPreferences ORM row (or None → all defaults)."""
        if prefs is None:
            return cls()
        return cls(
            pace=prefs.pace,
            budget=prefs.budget,
            interests=list(prefs.interests or []),
            notes=prefs.notes,
            day_start_minutes=(
                prefs.day_start_minutes
                if prefs.day_start_minutes is not None
                else DAY_START_MINUTES
            ),
            day_end_minutes=(
                prefs.day_end_minutes
                if prefs.day_end_minutes is not None
                else DAY_END_MINUTES
            ),
        )

    def has_research_bias(self) -> bool:
        """True when any field that should bias the researcher prompt is set."""
        return bool(self.budget or self.interests or self.notes)
