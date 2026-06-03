"""Unit tests for src/db/queries.py using an in-memory SQLite session.

The `session` fixture (from conftest.py) creates a fresh SQLite DB per test.
No Postgres, no DATABASE_URL env var required.
"""

import pytest

from src.db.queries import (
    add_activity,
    create_trip,
    get_rated_options_for_schedule,
    get_trips,
    get_unresearched_activities,
    mark_researched,
    save_options,
    set_option_duration,
    set_rating,
    upsert_schedule,
)
from src.agents.planner import DayPlan, ScheduleItem


def _make_trip(session, name="Test Trip", destination="Tokyo", num_days=3):
    return create_trip(session, name=name, destination=destination, num_days=num_days)


def _make_activity(session, trip, query="ramen restaurants", category="food", is_specific=False):
    return add_activity(session, trip.id, query=query, category=category, is_specific=is_specific)


def _make_options(session, activity, n=2):
    opts = [{"name": f"Place {i}", "address": f"{i} St", "location": "Downtown",
              "maps_link": None, "latitude": 35.6 + i * 0.01, "longitude": 139.7,
              } for i in range(n)]
    return save_options(session, activity.id, opts)


class TestCreateTrip:
    def test_persists_and_returns_with_id(self, session):
        # Input: name, destination, num_days → returned trip has auto-assigned id
        trip = create_trip(session, name="Japan Trip", destination="Tokyo", num_days=7)
        assert trip.id is not None
        assert trip.name == "Japan Trip"
        assert trip.destination == "Tokyo"
        assert trip.num_days == 7

    def test_nullable_num_days(self, session):
        # num_days is optional (user hasn't decided yet)
        trip = create_trip(session, name="Open-ended", destination="Paris", num_days=None)
        assert trip.num_days is None


class TestGetTrips:
    def test_returns_all_trips(self, session):
        # get_trips returns all trips in the DB
        create_trip(session, "Trip A", "Paris", 3)
        create_trip(session, "Trip B", "Rome", 5)
        create_trip(session, "Trip C", "Berlin", 4)
        trips = get_trips(session)
        assert len(trips) == 3
        names = {t.name for t in trips}
        assert names == {"Trip A", "Trip B", "Trip C"}


class TestAddActivity:
    def test_links_to_trip_by_fk(self, session):
        # Activity's trip_id must match the parent trip's id
        trip = _make_trip(session)
        act = add_activity(session, trip.id, "sushi", "food", False)
        assert act.trip_id == trip.id
        assert act.query == "sushi"

    def test_optional_category(self, session):
        # category can be None
        trip = _make_trip(session)
        act = add_activity(session, trip.id, "parks", None, False)
        assert act.category is None


class TestGetUnresearchedActivities:
    def test_excludes_researched(self, session):
        # Activities with researched_at set should not appear in unresearched list
        trip = _make_trip(session)
        act1 = _make_activity(session, trip, "ramen")
        act2 = _make_activity(session, trip, "temples")
        mark_researched(session, act1.id)  # mark act1 as done
        unresearched = get_unresearched_activities(session, trip.id)
        ids = [a.id for a in unresearched]
        assert act1.id not in ids
        assert act2.id in ids

    def test_all_unresearched_when_none_marked(self, session):
        # Fresh activities with no researched_at → all appear in unresearched list
        trip = _make_trip(session)
        _make_activity(session, trip, "q1")
        _make_activity(session, trip, "q2")
        unresearched = get_unresearched_activities(session, trip.id)
        assert len(unresearched) == 2


class TestMarkResearched:
    def test_sets_researched_at(self, session):
        # After marking, researched_at is non-None and activity leaves unresearched list
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        assert act.researched_at is None
        mark_researched(session, act.id)
        session.refresh(act)
        assert act.researched_at is not None

    def test_noop_on_missing_id(self, session):
        # Calling with a non-existent id should not raise
        mark_researched(session, 99999)  # should silently do nothing


class TestSaveOptions:
    def test_bulk_saves_and_returns_with_ids(self, session):
        # Input: activity + list of 3 option dicts → all 3 returned with auto-assigned ids
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        opts = _make_options(session, act, n=3)
        assert len(opts) == 3
        assert all(o.id is not None for o in opts)
        assert all(o.activity_id == act.id for o in opts)

    def test_empty_list_returns_empty(self, session):
        # Saving an empty list is a no-op
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        opts = save_options(session, act.id, [])
        assert opts == []

    def test_persists_option_category(self, session):
        # The researcher's per-option category is stored on the Option
        trip = _make_trip(session)
        act = _make_activity(session, trip, category=None)
        [opt] = save_options(session, act.id, [{"name": "Ichiran", "category": "food"}])
        session.refresh(opt)
        assert opt.category == "food"


class TestSetRating:
    def test_updates_user_rating(self, session):
        # After set_rating, the option's user_rating reflects the new value
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        [opt] = _make_options(session, act, n=1)
        assert opt.user_rating is None
        set_rating(session, opt.id, 4)
        session.refresh(opt)
        assert opt.user_rating == 4

    def test_can_clear_rating(self, session):
        # set_rating with None clears a previously set rating
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        [opt] = _make_options(session, act, n=1)
        set_rating(session, opt.id, 5)
        set_rating(session, opt.id, None)
        session.refresh(opt)
        assert opt.user_rating is None


class TestGetRatedOptionsForSchedule:
    def test_returns_only_rated_options(self, session):
        # Input: 2 options, 1 rated and 1 unrated → only rated one appears in output
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        opts = _make_options(session, act, n=2)
        set_rating(session, opts[0].id, 4)
        # opts[1] has no rating
        result = get_rated_options_for_schedule(session, trip.id)
        assert len(result) == 1
        assert result[0]["option_id"] == opts[0].id

    def test_returned_dict_has_expected_keys(self, session):
        # Each dict should have all keys required by build_schedule()
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        [opt] = _make_options(session, act, n=1)
        set_rating(session, opt.id, 3)
        result = get_rated_options_for_schedule(session, trip.id)
        row = result[0]
        for key in ("option_id", "name", "category", "latitude", "longitude",
                    "user_rating", "is_locked", "day_number", "time_slot",
                    "default_duration_minutes"):
            assert key in row, f"Missing key: {key}"

    def test_prefers_option_category_over_activity_category(self, session):
        # When the option carries its own category, it wins over the activity's
        # (broad) category so the scheduler picks a better time slot.
        trip = _make_trip(session)
        act = _make_activity(session, trip, category="other")
        [opt] = save_options(session, act.id, [{"name": "Bar X", "category": "nightlife"}])
        set_rating(session, opt.id, 4)
        result = get_rated_options_for_schedule(session, trip.id)
        assert result[0]["category"] == "nightlife"

    def test_falls_back_to_activity_category_when_option_has_none(self, session):
        # Option without a category falls back to the activity's category.
        trip = _make_trip(session)
        act = _make_activity(session, trip, category="culture")
        [opt] = save_options(session, act.id, [{"name": "Museum"}])  # no category
        set_rating(session, opt.id, 4)
        result = get_rated_options_for_schedule(session, trip.id)
        assert result[0]["category"] == "culture"


class TestUpsertSchedule:
    def _make_day_plans(self, option_ids: list[int]) -> list[DayPlan]:
        items = [
            ScheduleItem(option_id=oid, name=f"Place {oid}", category="sightseeing",
                         latitude=None, longitude=None, user_rating=4,
                         day_number=1, time_slot="morning")
            for oid in option_ids
        ]
        return [DayPlan(day_number=1, items=items)]

    def test_inserts_schedule_items(self, session):
        # After upsert_schedule, ScheduledItems exist in the DB for the given options
        from src.db.queries import get_schedule
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        opts = _make_options(session, act, n=2)
        set_rating(session, opts[0].id, 4)
        set_rating(session, opts[1].id, 4)
        plans = self._make_day_plans([opts[0].id, opts[1].id])
        upsert_schedule(session, trip.id, plans)
        schedule = get_schedule(session, trip.id)
        scheduled_option_ids = {si.option_id for si in schedule}
        assert opts[0].id in scheduled_option_ids
        assert opts[1].id in scheduled_option_ids

    def test_replaces_unlocked_items(self, session):
        # Running upsert_schedule twice → unlocked items from first run are replaced
        from src.db.queries import get_schedule
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        opts = _make_options(session, act, n=2)
        set_rating(session, opts[0].id, 4)
        set_rating(session, opts[1].id, 4)
        # First upsert with opt[0]
        upsert_schedule(session, trip.id, self._make_day_plans([opts[0].id]))
        # Second upsert with opt[1] only
        upsert_schedule(session, trip.id, self._make_day_plans([opts[1].id]))
        schedule = get_schedule(session, trip.id)
        option_ids = {si.option_id for si in schedule}
        assert opts[0].id not in option_ids  # replaced
        assert opts[1].id in option_ids

    def test_preserves_locked_items(self, session):
        # Locked ScheduledItems must survive a subsequent upsert_schedule call
        from src.db.models import ScheduledItem
        from src.db.queries import get_schedule
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        opts = _make_options(session, act, n=2)
        set_rating(session, opts[0].id, 4)
        set_rating(session, opts[1].id, 4)
        # Manually insert a locked item for opts[0]
        locked_si = ScheduledItem(trip_id=trip.id, option_id=opts[0].id,
                                   day_number=1, time_slot="morning", is_locked=True)
        session.add(locked_si)
        session.commit()
        # Upsert with only opts[1] — locked item for opts[0] must survive
        upsert_schedule(session, trip.id, self._make_day_plans([opts[1].id]))
        schedule = get_schedule(session, trip.id)
        option_ids = {si.option_id for si in schedule}
        assert opts[0].id in option_ids  # locked → preserved
        assert opts[1].id in option_ids  # newly inserted

    def test_regenerate_does_not_crash_when_day_plans_include_locked_item(self, session):
        # Regression for issue #4: build_schedule puts locked items in day_plans.
        # upsert_schedule must not try to re-insert them (unique constraint on option_id).
        from src.db.models import ScheduledItem
        from src.db.queries import get_schedule
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        opts = _make_options(session, act, n=2)
        # Insert a locked ScheduledItem for opts[0]
        session.add(ScheduledItem(trip_id=trip.id, option_id=opts[0].id,
                                   day_number=1, time_slot="morning", is_locked=True))
        session.commit()
        # day_plans mirrors what build_schedule produces: locked AND free items together
        locked_item = ScheduleItem(
            option_id=opts[0].id, name="Place 0", category="sightseeing",
            latitude=None, longitude=None, user_rating=4,
            is_locked=True, day_number=1, time_slot="morning",
        )
        free_item = ScheduleItem(
            option_id=opts[1].id, name="Place 1", category="sightseeing",
            latitude=None, longitude=None, user_rating=4,
            is_locked=False, day_number=1, time_slot="afternoon",
        )
        plans = [DayPlan(day_number=1, items=[locked_item, free_item])]
        upsert_schedule(session, trip.id, plans)  # must not raise IntegrityError
        option_ids = {si.option_id for si in get_schedule(session, trip.id)}
        assert opts[0].id in option_ids  # locked item preserved
        assert opts[1].id in option_ids  # free item inserted


class TestDurationColumns:
    """P1 schema smoke: the new timeline/duration columns persist round-trip."""

    def test_option_default_duration_persists(self, session):
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        [opt] = _make_options(session, act, n=1)
        opt.default_duration_minutes = 90
        session.commit()
        session.refresh(opt)
        assert opt.default_duration_minutes == 90

    def test_scheduled_item_time_columns_persist(self, session):
        from src.db.models import ScheduledItem
        from src.db.queries import get_schedule
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        [opt] = _make_options(session, act, n=1)
        session.add(ScheduledItem(
            trip_id=trip.id, option_id=opt.id, day_number=1,
            start_minutes=540, duration_minutes=120,
        ))
        session.commit()
        [si] = get_schedule(session, trip.id)
        assert si.start_minutes == 540
        assert si.duration_minutes == 120

    def test_set_option_duration_sets_override(self, session):
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        [opt] = _make_options(session, act, n=1)
        set_option_duration(session, opt.id, 90)
        session.refresh(opt)
        assert opt.default_duration_minutes == 90

    def test_set_option_duration_none_clears_override(self, session):
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        [opt] = _make_options(session, act, n=1)
        set_option_duration(session, opt.id, 90)
        set_option_duration(session, opt.id, None)
        session.refresh(opt)
        assert opt.default_duration_minutes is None

    def test_upsert_schedule_persists_start_minutes_from_item(self, session):
        # P5: upsert_schedule writes start_minutes directly from ScheduleItem, not from time_slot
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        opts = _make_options(session, act, n=3)
        for opt in opts:
            from src.db.queries import set_rating
            set_rating(session, opt.id, 4)
        from src.agents.planner import DayPlan, ScheduleItem
        plans = [DayPlan(day_number=1, items=[
            ScheduleItem(option_id=opts[0].id, name="A", category="sightseeing",
                         latitude=None, longitude=None, user_rating=4,
                         day_number=1, start_minutes=540, duration_minutes=120),
            ScheduleItem(option_id=opts[1].id, name="B", category="food",
                         latitude=None, longitude=None, user_rating=4,
                         day_number=1, start_minutes=660, duration_minutes=60),
            ScheduleItem(option_id=opts[2].id, name="C", category="nightlife",
                         latitude=None, longitude=None, user_rating=4,
                         day_number=1, start_minutes=1080, duration_minutes=90),
        ])]
        upsert_schedule(session, trip.id, plans)
        from src.db.queries import get_schedule
        scheduled = {si.option_id: si for si in get_schedule(session, trip.id)}
        assert scheduled[opts[0].id].start_minutes == 540
        assert scheduled[opts[0].id].duration_minutes == 120
        assert scheduled[opts[1].id].start_minutes == 660
        assert scheduled[opts[2].id].start_minutes == 1080
        assert scheduled[opts[2].id].duration_minutes == 90

    def test_get_schedule_orders_by_start_minutes(self, session):
        # Items should come back ordered by start_minutes, not time_slot string
        from src.db.models import ScheduledItem
        from src.db.queries import get_schedule
        trip = _make_trip(session)
        act = _make_activity(session, trip)
        opts = _make_options(session, act, n=2)
        # Insert afternoon before morning (reversed insertion order)
        session.add(ScheduledItem(trip_id=trip.id, option_id=opts[0].id,
                                  day_number=1, start_minutes=780))  # afternoon
        session.add(ScheduledItem(trip_id=trip.id, option_id=opts[1].id,
                                  day_number=1, start_minutes=540))  # morning
        session.commit()
        result = get_schedule(session, trip.id)
        assert result[0].start_minutes == 540   # morning first
        assert result[1].start_minutes == 780   # afternoon second
