# CLAUDE.md

## Project Overview

Multi-agent itinerary planner powered by Google Gemini (via Google ADK). Users create trips, research activities, rate options, and generate day-by-day schedules. CLI-first; web UI (FastAPI + HTMX + PostgreSQL) to follow.

## Architecture

```
src/
  agents/
    providers/
      llm_provider.py   # LLMProvider ABC
      gemini.py         # GeminiProvider (ADK + Gemini backend)
      __init__.py       # re-exports LLMProvider, GeminiProvider
    base.py             # LlmAgent base class (holds a provider, delegates ask())
    researcher.py       # ResearcherAgent: research() + research_batch()
    planner/
      types.py          # ScheduleItem, DayPlan, category_default_duration, DAY_* consts (shared)
      base.py           # PlanResult dataclass + SchedulePlanner Protocol
      deterministic.py  # DeterministicPlanner: build_schedule, _fit_day_within_window, _assign_start_times
      llm.py            # LlmPlanner: refines a deterministic draft via Gemini; apply_llm_refinement
      __init__.py       # Public re-exports only (DeterministicPlanner, LlmPlanner, PlanResult, SchedulePlanner, shared types)
    orchestrator.py     # Thread-local singletons + coordinator: research(), research_batch(), generate_schedule() → PlanResult
  db/
    models.py           # SQLAlchemy ORM: Trip, Activity, Option, ScheduledItem, TripPreferences
    database.py         # Sync (CLI) and async (web) session factories
    queries.py          # All DB query helpers (plain functions, take Session)
  services/
    trip_service.py     # Business logic shared by CLI + web: research_activities(), generate_and_save_schedule()
  cli.py                # Interactive CLI — thin I/O wrapper over services + queries

run_cli.py              # Entry point: python run_cli.py [--dry-run]
alembic/                # PostgreSQL migrations
tests/
  conftest.py           # SQLite in-memory session fixture
  mocks/provider.py     # MockProvider(LLMProvider) for unit tests
  unit/                 # Unit tests (no network, no API keys required)
web/                    # FastAPI web app (coming soon)
```

## Data Model

- **Trip** — name, destination, num_days (nullable), start_date/end_date (nullable)
- **Activity** — user's query (vague or specific), category, is_specific flag
- **Option** — researched result: name, category, address, location, maps_link, lat/lng, user_rating (1–5), default_duration_minutes (user-overridable; NULL = category default). category comes from the researcher and is preferred over Activity.category when scheduling.
- **ScheduledItem** — option placed on a day_number + time_slot (+ start_minutes / duration_minutes for the timeline), with is_locked flag
- **TripPreferences** — per-trip interests list, notes (for future travel style expansion)

day_number is 1-based and nullable. When Trip.start_date is set, UI converts to real dates.

## Environment

```
GOOGLE_API_KEY=...           # required: Gemini + Google Search
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost/itinerary
```

Copy `.env.example` to `.env` and fill in values.

## Running

```bash
# Install dependencies
pip install -r requirements.txt

# CLI — interactive
python run_cli.py

# CLI — validate config without API calls
python run_cli.py --dry-run

# Web UI (FastAPI + HTMX, opens at http://127.0.0.1:8000)
python run_web.py

# DB migrations (requires DATABASE_URL)
alembic upgrade head

# Run all unit tests (no API keys or DATABASE_URL required)
pytest tests/unit/

# Run all tests
pytest
```

## Key Design Decisions

- **Injectable providers**: Agents (`ResearcherAgent`, `PlannerAgent`) take a `LLMProvider` at construction. `GeminiProvider` is used in production; `MockProvider` (in `tests/mocks/`) is used in tests. No env vars needed for unit tests.
- **Thread-local singletons**: `orchestrator.py` owns the production agent instances via `threading.local()`. Each thread (web background thread or CLI run) gets its own `LlmPlanner` bound to its own event loop, preventing `RuntimeError("Event loop is closed")` on concurrent or repeated calls. `DeterministicPlanner` is stateless and held as a plain module-level singleton.
- **Strategy pattern for scheduling**: `DeterministicPlanner` and `LlmPlanner` both implement the `SchedulePlanner` Protocol (async `plan() → PlanResult`). `orchestrator.generate_schedule` is the coordinator: it tries `LlmPlanner` first, then falls back to `DeterministicPlanner` on any failure. Callers use `PlanResult.source` ("llm" | "deterministic") to know which ran.
- **Service layer**: `src/services/trip_service.py` holds business logic shared between the CLI and the coming web routes. CLI functions call services; services call queries + orchestrator.
- **Idempotent research**: `Activity.researched_at` is set by `mark_researched()` after a successful run. `get_unresearched_activities()` filters on `researched_at IS NULL`, so already-researched activities are never re-sent to the LLM.
- **Day-window enforcement**: Both planners bound each day to 09:00–21:00. `DeterministicPlanner` drops lowest-rated overflow items that don't fit the window (`_fit_day_within_window`). `LlmPlanner` rejects unlocked items starting at/after `DAY_END_MINUTES` in `apply_llm_refinement`. Locked items are never dropped by either planner.
- **Locked items**: `ScheduledItem.is_locked = True` pins an item to its day and `start_minutes`. The planner never moves or drops locked items — even if the LLM omits them, they are re-attached at their pinned time. The pinned `start_minutes` is plumbed from the DB through `get_rated_options_for_schedule` so it survives repeated regenerations.
- **day_number vs real dates**: Schedule uses day_number (1-based) until Trip.start_date is provided.

## Do

- Ensure code comments are updated and unit tests are written for larger changes.
- Use `Optional[str]` (not `str | None`) in SQLAlchemy `Mapped` columns — Python 3.14 compat.
- Add new activity categories to `ACTIVITY_CATEGORIES` in `src/db/models.py`, `_CATEGORY_ORDER`, and `_CATEGORY_DURATION` in `src/agents/planner/types.py`.
- New planner strategies must implement the `SchedulePlanner` Protocol (async `plan() → PlanResult`) and be wired into the coordinator in `src/agents/orchestrator.py`. New LLM-backed agents must accept a `LLMProvider` and use thread-local storage.
- Run `pytest tests/unit/` before merging — all unit tests must pass without `GOOGLE_API_KEY` or `DATABASE_URL`.
- When adding or changing agent behaviour, update the corresponding test in `tests/unit/test_researcher_agent.py` or `test_planner_agent.py`.
- New CLI commands or web routes that involve agents or DB writes should go through `src/services/trip_service.py`.

## Web routes (web/app.py)

- **Always guard entity lookups.** Every call to `get_trip()` or `session.get(Model, id)` can return `None`. Return `HTMLResponse("", status_code=404)` (or redirect) immediately before accessing any attribute or rendering a template. Missing guards cause `AttributeError` crashes.
- **Allowlist user-controlled field/path parameters** before passing them to `getattr()` or using them to select DB columns. Use `_EDITABLE_TRIP_FIELDS` as the model.
- **Background threads must receive job objects as arguments**, not read them from the module-level dict. Reading from the dict inside the thread creates a race: a second concurrent request can overwrite the dict entry between thread spawn and dict read, causing both threads to mutate the same job. Pattern: `threading.Thread(target=fn, args=(trip_id, job))`.
- **Avoid double-querying in route completions.** `_trip_context()` fetches `activities` and `schedule` internally. If the caller has already fetched them (e.g., in `research_status`, `schedule_status`), pass them via the keyword args `_trip_context(session, trip, activities=..., schedule=...)` to reuse the result.
- **Field display templates are the single source of truth.** `_field_display.html` is used both by `update_trip_field` responses and by `{% include %}` in `dashboard.html`. Do not create macro duplicates — keep one template.

## Do Not

- Do not add Docker — local PostgreSQL native install is the intended setup.
- Do not call `asyncio.run()` inside FastAPI route handlers, and do not use it in the CLI either — use the module-level `_run()` helper in `cli.py` which reuses a persistent event loop. Creating a new loop per call causes `RuntimeError("Event loop is closed")` on the second LLM invocation.
- Do not put business logic in route files — use the service layer.
- Do not use `Base.metadata.create_all()` in production — use Alembic migrations.
- Do not shadow imported type names with local variables (e.g., `Session = factory()` shadows the `Session` ORM type). Use descriptive names like `factory`.

## Out of Scope (for now, add later)

- Geo clustering / Google Maps API integration
- Authentication / multi-user
- Travel style preferences (budget, pace)
- Export (PDF, Google Calendar, Google Doc)
- Budget tracking
- Flights
- Notes per activity
- Undo/redo on schedule
