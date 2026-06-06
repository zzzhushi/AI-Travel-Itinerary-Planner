"""Unit tests for the Preferences DTO."""

from __future__ import annotations

from types import SimpleNamespace

from src.workers.planner.types import DAY_END_MINUTES, DAY_START_MINUTES
from src.workers.preferences import Preferences


def _row(**kw):
    """A stand-in for a TripPreferences ORM row."""
    base = dict(
        pace=None, budget=None, interests=None, notes=None,
        day_start_minutes=None, day_end_minutes=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestFromOrm:
    def test_none_yields_all_defaults(self):
        p = Preferences.from_orm(None)
        assert p.pace is None and p.budget is None
        assert p.interests == [] and p.notes is None
        assert p.day_start_minutes == DAY_START_MINUTES
        assert p.day_end_minutes == DAY_END_MINUTES

    def test_null_window_falls_back_to_defaults(self):
        p = Preferences.from_orm(_row(pace="relaxed"))
        assert p.pace == "relaxed"
        assert p.day_start_minutes == DAY_START_MINUTES
        assert p.day_end_minutes == DAY_END_MINUTES

    def test_custom_window_preserved(self):
        p = Preferences.from_orm(_row(day_start_minutes=600, day_end_minutes=1200))
        assert p.day_start_minutes == 600
        assert p.day_end_minutes == 1200

    def test_interests_copied_into_list(self):
        src = ["food", "temples"]
        p = Preferences.from_orm(_row(interests=src))
        assert p.interests == ["food", "temples"]
        assert p.interests is not src  # defensive copy


class TestHasResearchBias:
    def test_false_when_empty(self):
        assert Preferences.from_orm(None).has_research_bias() is False

    def test_true_when_budget_or_interests_or_notes(self):
        assert Preferences(budget="splurge").has_research_bias() is True
        assert Preferences(interests=["food"]).has_research_bias() is True
        assert Preferences(notes="with parents").has_research_bias() is True

    def test_pace_and_window_alone_are_not_research_bias(self):
        # Pace/window affect scheduling, not what gets researched.
        assert Preferences(pace="packed", day_start_minutes=600).has_research_bias() is False
