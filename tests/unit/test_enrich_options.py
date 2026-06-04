"""Unit tests for enrich_options_with_places and get_unenriched_options.

Uses the SQLite in-memory session fixture and MockPlacesClient.
No network, no API keys required.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.db.queries import (
    PLACES_RETRY_DAYS,
    PLACES_STALE_DAYS,
    add_activity,
    create_trip,
    get_unenriched_options,
    save_options,
)
from src.services.trip_service import enrich_options_with_places
from tests.mocks.places_client import SAMPLE_PLACE, MockPlacesClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days_ago(n: int) -> datetime:
    return _utcnow() - timedelta(days=n)


def _make_option(name: str = "Ichiran Ramen", maps_search: str | None = "Ichiran Ramen Tokyo") -> dict:
    return {
        "name": name,
        "address": "1-2-3 Original St",
        "location": "Shinjuku",
        "maps_search": maps_search,
        "why": "Great ramen",
    }


def _setup(session, destination: str = "Tokyo") -> tuple:
    """Create a trip + activity and return (trip, activity)."""
    trip = create_trip(session, "Test Trip", destination, 3)
    act = add_activity(session, trip.id, "ramen", "food", False)
    return trip, act


# ---------------------------------------------------------------------------
# get_unenriched_options — query logic
# ---------------------------------------------------------------------------

class TestGetUnenrichedOptions:
    def test_never_enriched_is_returned(self, session):
        trip, act = _setup(session)
        save_options(session, act.id, [_make_option()])
        result = get_unenriched_options(session, trip.id)
        assert len(result) == 1

    def test_recently_enriched_with_place_id_is_skipped(self, session):
        # place_refreshed_at recent + place_id set → not stale, not failed → excluded
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        opt.place_id = "ChIJtest"
        opt.place_refreshed_at = _days_ago(1)
        session.commit()

        result = get_unenriched_options(session, trip.id)
        assert result == []

    def test_stale_enriched_option_is_returned(self, session):
        # Enriched more than PLACES_STALE_DAYS ago → needs refresh
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        opt.place_id = "ChIJtest"
        opt.place_refreshed_at = _days_ago(PLACES_STALE_DAYS + 1)
        session.commit()

        result = get_unenriched_options(session, trip.id)
        assert len(result) == 1

    def test_failed_lookup_within_retry_window_is_skipped(self, session):
        # place_id NULL but refreshed recently → retry cooldown still active
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        opt.place_id = None
        opt.place_refreshed_at = _days_ago(PLACES_RETRY_DAYS - 1)
        session.commit()

        result = get_unenriched_options(session, trip.id)
        assert result == []

    def test_failed_lookup_past_retry_window_is_returned(self, session):
        # place_id NULL and refreshed long enough ago → ready for retry
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        opt.place_id = None
        opt.place_refreshed_at = _days_ago(PLACES_RETRY_DAYS + 1)
        session.commit()

        result = get_unenriched_options(session, trip.id)
        assert len(result) == 1

    def test_only_returns_options_for_the_given_trip(self, session):
        trip1, act1 = _setup(session, "Tokyo")
        trip2, act2 = _setup(session, "Paris")
        save_options(session, act1.id, [_make_option("Ramen")])
        save_options(session, act2.id, [_make_option("Baguette")])

        result = get_unenriched_options(session, trip1.id)
        assert len(result) == 1
        assert result[0].name == "Ramen"


# ---------------------------------------------------------------------------
# get_all_options_for_trip — used by force re-enrichment
# ---------------------------------------------------------------------------

class TestGetAllOptionsForTrip:
    def test_returns_all_options_regardless_of_enrichment(self, session):
        from src.db.queries import get_all_options_for_trip

        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option("Enriched"), _make_option("Fresh")])
        opts[0].place_id = "ChIJx"
        opts[0].place_refreshed_at = _days_ago(1)  # recent → excluded by the staleness gate
        session.commit()

        assert {o.name for o in get_all_options_for_trip(session, trip.id)} == {"Enriched", "Fresh"}
        # Contrast: the default gate excludes the recently-enriched one.
        assert {o.name for o in get_unenriched_options(session, trip.id)} == {"Fresh"}

    def test_scoped_to_trip(self, session):
        from src.db.queries import get_all_options_for_trip

        trip1, act1 = _setup(session, "Tokyo")
        trip2, act2 = _setup(session, "Paris")
        save_options(session, act1.id, [_make_option("Ramen")])
        save_options(session, act2.id, [_make_option("Baguette")])

        assert {o.name for o in get_all_options_for_trip(session, trip1.id)} == {"Ramen"}


# ---------------------------------------------------------------------------
# enrich_options_with_places
# ---------------------------------------------------------------------------

class TestEnrichOptionsWithPlaces:
    @pytest.mark.asyncio
    async def test_no_client_is_noop(self, session):
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]

        result = await enrich_options_with_places(session, trip, places_client=None)

        session.refresh(opt)
        assert result == {"enriched": 0, "skipped": 0, "failed": 0}
        assert opt.place_refreshed_at is None

    @pytest.mark.asyncio
    async def test_no_unenriched_options_is_noop(self, session):
        # All options already enriched recently
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        opt.place_id = "ChIJtest"
        opt.place_refreshed_at = _days_ago(1)
        session.commit()

        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        result = await enrich_options_with_places(session, trip, client)

        assert result == {"enriched": 0, "skipped": 0, "failed": 0}
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_happy_path_writes_all_fields(self, session):
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]

        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        result = await enrich_options_with_places(session, trip, client)

        session.refresh(opt)
        assert result == {"enriched": 1, "skipped": 0, "failed": 0}
        assert opt.place_id == SAMPLE_PLACE["place_id"]
        assert opt.latitude == SAMPLE_PLACE["latitude"]
        assert opt.longitude == SAMPLE_PLACE["longitude"]
        assert opt.maps_link == SAMPLE_PLACE["maps_link"]
        assert opt.google_rating == SAMPLE_PLACE["google_rating"]
        assert opt.price_level == SAMPLE_PLACE["price_level"]
        assert opt.phone_number == SAMPLE_PLACE["phone_number"]
        assert opt.website == SAMPLE_PLACE["website"]
        assert opt.opening_hours == SAMPLE_PLACE["opening_hours"]
        assert opt.neighborhood == SAMPLE_PLACE["neighborhood"]
        assert opt.place_refreshed_at is not None

    @pytest.mark.asyncio
    async def test_force_re_enriches_recent_option(self, session):
        # A recently-enriched option is skipped by default, but force=True
        # re-fetches it — used to backfill newly added Places fields like neighborhood.
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        opt.place_id = "ChIJold"
        opt.neighborhood = None
        opt.place_refreshed_at = _days_ago(1)  # recent → default path skips it
        session.commit()

        client = MockPlacesClient(return_data=SAMPLE_PLACE)

        default_stats = await enrich_options_with_places(session, trip, client)
        assert default_stats == {"enriched": 0, "skipped": 0, "failed": 0}
        assert client.calls == []  # skipped by the staleness gate

        forced_stats = await enrich_options_with_places(session, trip, client, force=True)
        session.refresh(opt)
        assert forced_stats["enriched"] == 1
        assert len(client.calls) == 1
        assert opt.place_id == SAMPLE_PLACE["place_id"]  # re-fetched (was "ChIJold")
        assert opt.neighborhood == SAMPLE_PLACE["neighborhood"]  # backfilled

    @pytest.mark.asyncio
    async def test_missing_neighborhood_stays_null(self, session):
        # Places result without a neighborhood → column stays NULL.
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]

        place_no_hood = {**SAMPLE_PLACE, "neighborhood": None}
        client = MockPlacesClient(return_data=place_no_hood)
        await enrich_options_with_places(session, trip, client)

        session.refresh(opt)
        assert opt.neighborhood is None

    @pytest.mark.asyncio
    async def test_formatted_address_overwrites_original(self, session):
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        assert opt.address == "1-2-3 Original St"

        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        await enrich_options_with_places(session, trip, client)

        session.refresh(opt)
        assert opt.address == SAMPLE_PLACE["formatted_address"]

    @pytest.mark.asyncio
    async def test_missing_formatted_address_preserves_original(self, session):
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]

        place_no_address = {**SAMPLE_PLACE, "formatted_address": None}
        client = MockPlacesClient(return_data=place_no_address)
        await enrich_options_with_places(session, trip, client)

        session.refresh(opt)
        assert opt.address == "1-2-3 Original St"

    @pytest.mark.asyncio
    async def test_no_match_sets_refreshed_at_without_place_id(self, session):
        # Lookup returns None → place_id stays NULL, place_refreshed_at is set
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]

        client = MockPlacesClient(return_data=None)
        result = await enrich_options_with_places(session, trip, client)

        session.refresh(opt)
        assert result == {"enriched": 0, "skipped": 0, "failed": 1}
        assert opt.place_id is None
        assert opt.place_refreshed_at is not None

    @pytest.mark.asyncio
    async def test_result_without_place_id_counted_as_skipped(self, session):
        # API returns data but no place_id → fields written, counted as skipped not enriched
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]

        place_no_id = {**SAMPLE_PLACE, "place_id": None}
        client = MockPlacesClient(return_data=place_no_id)
        result = await enrich_options_with_places(session, trip, client)

        session.refresh(opt)
        assert result == {"enriched": 0, "skipped": 1, "failed": 0}
        assert opt.place_id is None
        assert opt.latitude == SAMPLE_PLACE["latitude"]  # other fields still written

    @pytest.mark.asyncio
    async def test_fallback_to_name_when_maps_search_is_none(self, session):
        # Pre-migration option: maps_search=None → lookup called with opt.name
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option("Old Place", maps_search=None)])

        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        await enrich_options_with_places(session, trip, client)

        assert client.calls == ["Old Place"]

    @pytest.mark.asyncio
    async def test_uses_maps_search_over_name_when_both_present(self, session):
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option("Ichiran Ramen", "Ichiran Ramen Shinjuku Tokyo")])

        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        await enrich_options_with_places(session, trip, client)

        assert client.calls == ["Ichiran Ramen Shinjuku Tokyo"]

    @pytest.mark.asyncio
    async def test_failed_option_not_retried_within_retry_window(self, session):
        # Option previously failed recently → still within cooldown → not retried
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        opt.place_id = None
        opt.place_refreshed_at = _days_ago(PLACES_RETRY_DAYS - 1)
        session.commit()

        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        result = await enrich_options_with_places(session, trip, client)

        assert result == {"enriched": 0, "skipped": 0, "failed": 0}
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_failed_option_retried_past_retry_window(self, session):
        # Option previously failed but cooldown has expired → retried
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        opt.place_id = None
        opt.place_refreshed_at = _days_ago(PLACES_RETRY_DAYS + 1)
        session.commit()

        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        result = await enrich_options_with_places(session, trip, client)

        assert result["enriched"] == 1
        assert len(client.calls) == 1

    @pytest.mark.asyncio
    async def test_stale_option_is_re_enriched(self, session):
        # Option enriched successfully but data is old → refreshed
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]
        opt.place_id = "ChIJold"
        opt.place_refreshed_at = _days_ago(PLACES_STALE_DAYS + 1)
        session.commit()

        client = MockPlacesClient(return_data=SAMPLE_PLACE)
        result = await enrich_options_with_places(session, trip, client)

        session.refresh(opt)
        assert result["enriched"] == 1
        assert opt.place_id == SAMPLE_PLACE["place_id"]

    @pytest.mark.asyncio
    async def test_multiple_options_all_processed_correct_counts(self, session):
        trip, act = _setup(session)
        opts = save_options(session, act.id, [
            _make_option("Place A", "Place A Tokyo"),
            _make_option("Place B", "Place B Tokyo"),
            _make_option("Place C", "Place C Tokyo"),
        ])

        calls = iter([SAMPLE_PLACE, None, {**SAMPLE_PLACE, "place_id": None}])

        class SequentialMock:
            self_calls: list[str] = []
            def lookup(self, q: str) -> dict | None:
                self.self_calls.append(q)
                return next(calls)

        client = SequentialMock()
        result = await enrich_options_with_places(session, trip, client)

        assert result == {"enriched": 1, "skipped": 1, "failed": 1}
        assert len(client.self_calls) == 3

    @pytest.mark.asyncio
    async def test_place_refreshed_at_always_updated_on_processed_options(self, session):
        # Even a no-match option gets place_refreshed_at set, preventing immediate retry
        trip, act = _setup(session)
        opts = save_options(session, act.id, [_make_option()])
        opt = opts[0]

        client = MockPlacesClient(return_data=None)
        before = _utcnow()
        await enrich_options_with_places(session, trip, client)
        after = _utcnow()

        session.refresh(opt)
        assert opt.place_refreshed_at is not None
        assert before <= opt.place_refreshed_at <= after
