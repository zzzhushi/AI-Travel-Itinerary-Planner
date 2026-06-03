"""Unit tests for src/services/trip_service.py.

Orchestrator calls (research_batch, generate_schedule) are patched with AsyncMock.
DB operations use the SQLite in-memory session fixture from conftest.py.
No network, no API keys required.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.planner import DayPlan, ScheduleItem
from src.db.queries import (
    add_activity,
    create_trip,
    get_schedule,
    get_unresearched_activities,
    mark_researched,
    save_options,
    set_rating,
)
from src.services.trip_service import (
    generate_and_save_schedule,
    research_activities,
    research_and_enrich,
)
from tests.mocks.places_client import MockPlacesClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_option_dict(name: str, category: str = "food") -> dict:
    return {
        "name": name, "address": "123 St", "location": "Downtown",
        "maps_search": f"{name} Tokyo", "category": category, "why": "Great",
    }


def _make_day_plans(trip_id: int, option_ids: list[int]) -> list[DayPlan]:
    items = [
        ScheduleItem(option_id=oid, name=f"Place {oid}", category="sightseeing",
                     latitude=None, longitude=None, user_rating=4,
                     day_number=1, time_slot="morning")
        for oid in option_ids
    ]
    return [DayPlan(day_number=1, items=items)]


# ---------------------------------------------------------------------------
# research_activities
# ---------------------------------------------------------------------------

class TestResearchActivities:
    @pytest.mark.asyncio
    async def test_happy_path_saves_options_and_marks_researched(self, session):
        # 2 unresearched activities; mock returns 1 option each
        # → both marked researched, options saved, summary shows correct counts
        trip = create_trip(session, "Test", "Tokyo", 3)
        act1 = add_activity(session, trip.id, "ramen", "food", False)
        act2 = add_activity(session, trip.id, "temples", "culture", False)

        mock_results = [
            ([_make_option_dict("Ramen Shop")], ""),
            ([_make_option_dict("Temple A"), _make_option_dict("Temple B")], ""),
        ]
        with patch("src.services.trip_service.research_batch", new_callable=AsyncMock) as m:
            m.return_value = mock_results
            summaries = await research_activities(session, trip)

        assert len(summaries) == 2
        assert summaries[0] == {"query": "ramen", "options_saved": 1, "error": ""}
        assert summaries[1] == {"query": "temples", "options_saved": 2, "error": ""}
        # Both should now be researched
        assert get_unresearched_activities(session, trip.id) == []

    @pytest.mark.asyncio
    async def test_agent_error_not_saved_and_included_in_summary(self, session):
        # Activity 1 succeeds; activity 2 has an agent error
        # → activity 1 saved and marked, activity 2 not saved, error in summary
        trip = create_trip(session, "Test", "Tokyo", 3)
        act1 = add_activity(session, trip.id, "ramen", "food", False)
        act2 = add_activity(session, trip.id, "temples", "culture", False)

        mock_results = [
            ([_make_option_dict("Ramen Shop")], ""),
            ([], "429 rate limited"),
        ]
        with patch("src.services.trip_service.research_batch", new_callable=AsyncMock) as m:
            m.return_value = mock_results
            summaries = await research_activities(session, trip)

        assert summaries[0]["error"] == ""
        assert summaries[0]["options_saved"] == 1
        assert summaries[1]["error"] == "429 rate limited"
        assert summaries[1]["options_saved"] == 0
        # act2 should still be unresearched since it errored
        unresearched = get_unresearched_activities(session, trip.id)
        assert any(a.id == act2.id for a in unresearched)

    @pytest.mark.asyncio
    async def test_zero_options_still_marks_researched(self, session):
        # Agent returns empty list with no error (valid but no results)
        # → activity marked researched, 0 options saved
        trip = create_trip(session, "Test", "Tokyo", 3)
        act = add_activity(session, trip.id, "obscure thing", "other", False)

        with patch("src.services.trip_service.research_batch", new_callable=AsyncMock) as m:
            m.return_value = [([], "")]
            summaries = await research_activities(session, trip)

        assert summaries[0] == {"query": "obscure thing", "options_saved": 0, "error": ""}
        assert get_unresearched_activities(session, trip.id) == []

    @pytest.mark.asyncio
    async def test_no_unresearched_returns_empty_without_calling_agent(self, session):
        # All activities already researched → returns [] immediately, no orchestrator call
        trip = create_trip(session, "Test", "Tokyo", 3)
        act = add_activity(session, trip.id, "ramen", "food", False)
        mark_researched(session, act.id)

        with patch("src.services.trip_service.research_batch", new_callable=AsyncMock) as m:
            summaries = await research_activities(session, trip)

        assert summaries == []
        m.assert_not_called()


# ---------------------------------------------------------------------------
# generate_and_save_schedule
# ---------------------------------------------------------------------------

class TestGenerateAndSaveSchedule:
    def _setup_rated_options(self, session, trip, n: int = 2) -> list:
        act = add_activity(session, trip.id, "things to do", "sightseeing", False)
        opts = save_options(session, act.id, [
            {"name": f"Place {i}", "address": "", "location": "", "maps_link": None,
             "latitude": None, "longitude": None}
            for i in range(n)
        ])
        for opt in opts:
            set_rating(session, opt.id, 4)
        return opts

    @pytest.mark.asyncio
    async def test_happy_path_saves_schedule(self, session):
        # Rated options exist; mock returns a schedule → schedule saved, returns data
        trip = create_trip(session, "Test", "Tokyo", 3)
        opts = self._setup_rated_options(session, trip, n=2)
        day_plans = _make_day_plans(trip.id, [opts[0].id, opts[1].id])

        with patch("src.services.trip_service.generate_schedule", new_callable=AsyncMock) as m:
            m.return_value = (day_plans, [], "")
            result_plans, llm_days, warn = await generate_and_save_schedule(
                session, trip, num_days=3
            )

        assert result_plans is day_plans
        assert llm_days == []
        assert warn == ""
        # Verify schedule was saved to DB
        saved = get_schedule(session, trip.id)
        assert len(saved) == 2

    @pytest.mark.asyncio
    async def test_llm_error_still_returns_deterministic_schedule(self, session):
        # generate_schedule returns a warning (LLM failed) but deterministic plans
        trip = create_trip(session, "Test", "Tokyo", 3)
        opts = self._setup_rated_options(session, trip, n=1)
        day_plans = _make_day_plans(trip.id, [opts[0].id])

        with patch("src.services.trip_service.generate_schedule", new_callable=AsyncMock) as m:
            m.return_value = (day_plans, [], "LLM refinement skipped: timeout")
            result_plans, llm_days, warn = await generate_and_save_schedule(
                session, trip, num_days=2
            )

        assert result_plans is day_plans
        assert "LLM refinement skipped" in warn
        assert get_schedule(session, trip.id)  # schedule was still saved

    @pytest.mark.asyncio
    async def test_no_rated_options_returns_empty(self, session):
        # No options have been rated → returns empty without calling generate_schedule
        trip = create_trip(session, "Test", "Tokyo", 3)
        add_activity(session, trip.id, "ramen", "food", False)  # unrated

        with patch("src.services.trip_service.generate_schedule", new_callable=AsyncMock) as m:
            result_plans, llm_days, warn = await generate_and_save_schedule(
                session, trip, num_days=3
            )

        assert result_plans == []
        assert llm_days == []
        assert warn == ""
        m.assert_not_called()


# ---------------------------------------------------------------------------
# research_and_enrich
# ---------------------------------------------------------------------------

class TestResearchAndEnrich:
    @pytest.mark.asyncio
    async def test_no_client_skips_enrichment(self, session):
        # Without a Places client, research runs but enrichment is skipped.
        trip = create_trip(session, "Test", "Tokyo", 3)
        summary = [{"query": "ramen", "options_saved": 1, "error": ""}]
        with patch("src.services.trip_service.research_activities", new_callable=AsyncMock) as r, \
             patch("src.services.trip_service.enrich_options_with_places", new_callable=AsyncMock) as e:
            r.return_value = summary
            summaries, stats = await research_and_enrich(session, trip, None)
        assert summaries == summary
        assert stats == {"enriched": 0, "skipped": 0, "failed": 0}
        e.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_client_runs_enrichment(self, session):
        # With a Places client, enrichment runs and its stats are returned.
        trip = create_trip(session, "Test", "Tokyo", 3)
        with patch("src.services.trip_service.research_activities", new_callable=AsyncMock) as r, \
             patch("src.services.trip_service.enrich_options_with_places", new_callable=AsyncMock) as e:
            r.return_value = []
            e.return_value = {"enriched": 2, "skipped": 0, "failed": 1}
            summaries, stats = await research_and_enrich(session, trip, MockPlacesClient())
        e.assert_awaited_once()
        assert stats == {"enriched": 2, "skipped": 0, "failed": 1}

    @pytest.mark.asyncio
    async def test_enrichment_failure_preserves_research(self, session):
        # A Places failure must not discard research results (best-effort enrichment).
        trip = create_trip(session, "Test", "Tokyo", 3)
        summary = [{"query": "ramen", "options_saved": 1, "error": ""}]
        with patch("src.services.trip_service.research_activities", new_callable=AsyncMock) as r, \
             patch("src.services.trip_service.enrich_options_with_places", new_callable=AsyncMock) as e:
            r.return_value = summary
            e.side_effect = RuntimeError("places boom")
            summaries, stats = await research_and_enrich(session, trip, MockPlacesClient())
        assert summaries == summary
        assert stats == {"enriched": 0, "skipped": 0, "failed": 0}
