"""Public API for the planner package.

Internal helpers (build_schedule, apply_llm_refinement, PlannerAgent,
_assign_start_times, _fit_day_within_window) are not exported — use the
strategy classes instead.
"""

from src.workers.planner.base import PlanResult, SchedulePlanner
from src.workers.planner.clustering import (
    Cluster,
    ClusterPoint,
    assign_clusters,
    cluster_points,
    haversine_km,
)
from src.workers.planner.deterministic import DeterministicPlanner
from src.workers.planner.llm import LlmPlanner, PLANNER_INSTRUCTION
from src.workers.planner.types import (
    DAY_END_MINUTES,
    DAY_START_MINUTES,
    DEFAULT_DURATION_MINUTES,
    SLOT_START_MINUTES,
    DayPlan,
    ScheduleItem,
    category_default_duration,
)

__all__ = [
    # Domain types
    "ScheduleItem",
    "DayPlan",
    # Geographic clustering
    "ClusterPoint",
    "Cluster",
    "cluster_points",
    "assign_clusters",
    "haversine_km",
    # Result + protocol
    "PlanResult",
    "SchedulePlanner",
    # Strategy implementations
    "DeterministicPlanner",
    "LlmPlanner",
    # Constants + helpers (used by web/app.py, cli.py, queries.py)
    "DAY_START_MINUTES",
    "DAY_END_MINUTES",
    "DEFAULT_DURATION_MINUTES",
    "SLOT_START_MINUTES",
    "category_default_duration",
    # LLM instruction (used by orchestrator singleton setup)
    "PLANNER_INSTRUCTION",
]
