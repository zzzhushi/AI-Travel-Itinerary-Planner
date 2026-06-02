"""Unit tests for ResearcherAgent using MockProvider.

All tests use MockProvider — no Gemini API calls, no network, no env vars needed.
"""

import json

import pytest

from src.agents.researcher import ResearcherAgent, _normalize
from tests.mocks.provider import MockProvider


def _make_options_json(n: int = 3, include_name: bool = True) -> str:
    """Helper: build a JSON array string of n fake options."""
    options = []
    for i in range(n):
        opt = {
            "address": f"{i} Main St",
            "location": "Downtown",
            "maps_search": f"Place {i} Tokyo",
            "category": "sightseeing",
            "why": "Great spot",
        }
        if include_name:
            opt["name"] = f"Place {i}"
        options.append(opt)
    return json.dumps(options)


class TestResearcherAgentResearch:
    @pytest.mark.asyncio
    async def test_happy_path_returns_options(self):
        # Input: provider returns a valid JSON array of 3 options
        # Output: list of 3 dicts with name and other fields populated
        provider = MockProvider([_make_options_json(3)])
        agent = ResearcherAgent(provider)
        options, err = await agent.research("Tokyo", "museums")
        assert err == ""
        assert len(options) == 3
        assert all("name" in o for o in options)

    @pytest.mark.asyncio
    async def test_agent_error_returns_empty(self):
        # Input: provider returns an error (simulates 429, network failure, etc.)
        # Output: empty list and the error message propagated
        provider = MockProvider([])  # empty queue → error on first call
        agent = ResearcherAgent(provider)
        options, err = await agent.research("Tokyo", "ramen")
        assert options == []
        assert "no more responses" in err

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self):
        # Input: provider returns non-JSON text (LLM went off-script)
        # Output: empty list and a parse-error message
        provider = MockProvider(["Sorry, I cannot help with that."])
        agent = ResearcherAgent(provider)
        options, err = await agent.research("Tokyo", "ramen")
        assert options == []
        assert "Could not parse JSON" in err

    @pytest.mark.asyncio
    async def test_empty_array_returns_error(self):
        # Input: provider returns "[]" — valid JSON but no options found
        # Output: empty list and a "no valid options" error
        provider = MockProvider(["[]"])
        agent = ResearcherAgent(provider)
        options, err = await agent.research("Tokyo", "ramen")
        assert options == []
        assert "no valid options" in err.lower()

    @pytest.mark.asyncio
    async def test_options_without_name_are_filtered(self):
        # Input: 2 options with name, 1 without — _normalize should drop the nameless one
        mixed = json.dumps([
            {"name": "Good Place", "category": "food", "address": "", "location": "", "maps_search": "", "why": ""},
            {"category": "food", "address": "", "location": "", "maps_search": "", "why": ""},  # no name
            {"name": "Another Place", "category": "sightseeing", "address": "", "location": "", "maps_search": "", "why": ""},
        ])
        provider = MockProvider([mixed])
        agent = ResearcherAgent(provider)
        options, err = await agent.research("Tokyo", "food")
        assert err == ""
        assert len(options) == 2
        assert all(o["name"] for o in options)


class TestNormalize:
    def test_filters_non_dict_items(self):
        # Non-dict entries in the raw list are silently dropped
        raw = [{"name": "Valid"}, "not a dict", 42]
        result = _normalize(raw)
        assert len(result) == 1

    def test_category_defaults_to_other(self):
        # Missing category key falls back to "other"
        raw = [{"name": "Place", "address": "", "location": "", "maps_search": "", "why": ""}]
        result = _normalize(raw)
        assert result[0]["category"] == "other"

    def test_maps_search_passes_through(self):
        # maps_search is preserved so the enrichment step can use it for Places API lookup
        raw = [{"name": "Ichiran Ramen", "maps_search": "Ichiran Ramen Shinjuku Tokyo", "why": "", "address": "", "location": "", "category": "food"}]
        result = _normalize(raw)
        assert result[0]["maps_search"] == "Ichiran Ramen Shinjuku Tokyo"

    def test_maps_search_missing_normalizes_to_none(self):
        # Missing or empty maps_search must be stored as None (not "") so DB IS NULL queries work correctly
        raw = [{"name": "Place", "maps_search": "", "why": "", "address": "", "location": "", "category": "food"}]
        result = _normalize(raw)
        assert result[0]["maps_search"] is None

    def test_why_passes_through(self):
        # why is preserved so it can be saved and displayed as the researcher's rationale
        raw = [{"name": "Place", "maps_search": "", "why": "Famous for its broth.", "address": "", "location": "", "category": "food"}]
        result = _normalize(raw)
        assert result[0]["why"] == "Famous for its broth."

    def test_why_missing_normalizes_to_none(self):
        # Missing or empty why must be stored as None
        raw = [{"name": "Place", "maps_search": "", "why": "", "address": "", "location": "", "category": "food"}]
        result = _normalize(raw)
        assert result[0]["why"] is None


class TestResearchBatch:
    @pytest.mark.asyncio
    async def test_batch_happy_path(self):
        # Input: 2 activities; provider returns a JSON dict with keys "0" and "1"
        # Output: list of 2 (options, error) tuples in the same order
        batch_response = json.dumps({
            "0": [{"name": "Ramen Shop", "address": "", "location": "", "maps_search": "", "category": "food", "why": ""}],
            "1": [{"name": "Museum", "address": "", "location": "", "maps_search": "", "category": "culture", "why": ""}],
        })
        agent = ResearcherAgent(MockProvider([batch_response]))
        activities = [
            {"query": "ramen", "is_specific": False},
            {"query": "museums", "is_specific": False},
        ]
        results = await agent.research_batch("Tokyo", activities)

        assert len(results) == 2
        options0, err0 = results[0]
        options1, err1 = results[1]
        assert err0 == "" and len(options0) == 1
        assert err1 == "" and len(options1) == 1

    @pytest.mark.asyncio
    async def test_batch_fallback_on_missing_key(self):
        # Input: batch JSON missing key "1" → index 1 falls back to individual research() call
        # Provider queue: first response = batch dict (missing "1"), second = individual fallback
        batch_missing = json.dumps({
            "0": [{"name": "Ramen Shop", "address": "", "location": "", "maps_search": "", "category": "food", "why": ""}],
            # "1" intentionally absent
        })
        individual_fallback = json.dumps(
            [{"name": "Park", "address": "", "location": "", "maps_search": "", "category": "nature", "why": ""}]
        )
        agent = ResearcherAgent(MockProvider([batch_missing, individual_fallback]))
        activities = [
            {"query": "ramen", "is_specific": False},
            {"query": "parks", "is_specific": False},
        ]
        results = await agent.research_batch("Tokyo", activities)

        assert len(results) == 2
        options0, _ = results[0]
        options1, err1 = results[1]
        assert options0[0]["name"] == "Ramen Shop"
        assert options1[0]["name"] == "Park"
        assert err1 == ""

    @pytest.mark.asyncio
    async def test_single_activity_bare_array_fallback(self):
        # LLM sometimes returns a bare array instead of {"0": [...]} for single-activity batches.
        # The batch parser should recover and return the results rather than erroring.
        bare_array = json.dumps(
            [{"name": "Gotokuji Temple", "address": "", "location": "", "maps_search": "Gotokuji Temple Tokyo", "category": "culture", "why": "Lucky cat temple."}]
        )
        agent = ResearcherAgent(MockProvider([bare_array]))
        activities = [{"query": "cat shrines", "is_specific": False}]
        results = await agent.research_batch("Tokyo", activities)

        assert len(results) == 1
        options, err = results[0]
        assert err == ""
        assert options[0]["name"] == "Gotokuji Temple"

    @pytest.mark.asyncio
    async def test_empty_activities_returns_empty(self):
        # Edge case: calling with no activities should return [] immediately
        agent = ResearcherAgent(MockProvider([]))
        results = await agent.research_batch("Tokyo", [])
        assert results == []
