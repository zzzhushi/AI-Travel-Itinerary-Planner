"""SchedulePlanner Protocol and PlanResult — the shared interface for all planners."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Optional, Protocol

from src.workers.planner.types import DayPlan

if TYPE_CHECKING:
    from src.workers.preferences import Preferences


@dataclass
class PlanResult:
    day_plans: list[DayPlan]
    source: str            # "deterministic" | "llm"
    warning: str = ""


class SchedulePlanner(Protocol):
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
    ) -> PlanResult: ...
