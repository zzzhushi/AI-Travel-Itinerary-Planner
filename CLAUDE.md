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
    planner.py          # PlannerAgent: refine(); build_schedule() pure Python
    orchestrator.py     # Singletons + public API: research(), research_batch(), generate_schedule()
  db/
    models.py           # SQLAlchemy ORM: Trip, Activity, Option, ScheduledItem, TripPreferences
    database.py         # Sync (CLI) and async (web) session factories
    queries.py          # All DB query helpers (plain functions, take Session)
  services/
    trip_service.py     # Business logic shared by CLI + web: research_activities(), generate_and_save_schedule()
  cli.py                # Interactive CLI — thin I/O wrapper over services + queries

run_cli.py              # Entry point: python run_cli.py [--dry-run | --json trip.json]
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
- **Option** — researched result: name, address, location, maps_link, lat/lng, user_rating (1–5)
- **ScheduledItem** — option placed on a day_number + time_slot, with is_locked flag
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

# CLI — non-interactive from JSON
python run_cli.py --json examples/trip.json

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
- **Singleton ownership**: `orchestrator.py` owns the production singletons (`_get_researcher()`, `_get_planner()`). Agent classes themselves hold no singleton state.
- **Service layer**: `src/services/trip_service.py` holds business logic shared between the CLI and the coming web routes. CLI functions call services; services call queries + orchestrator.
- **Idempotent research**: `Option.research_hash` = sha256(trip_id + query)[:16]. Orchestrator skips re-research if hash matches.
- **Two-phase planning**: deterministic Python round-robin schedule first, then optional Gemini LLM refinement pass for human-readable ordering and notes.
- **Locked items**: `ScheduledItem.is_locked = True` means the planner and (later) drag-drop UI will not move that item.
- **day_number vs real dates**: Schedule uses day_number (1-based) until Trip.start_date is provided.

## Do

- Ensure code comments are updated and unit tests are written for larger changes.
- Use `Optional[str]` (not `str | None`) in SQLAlchemy `Mapped` columns — Python 3.14 compat.
- Check `research_hash` before calling the researcher — idempotency is caller's responsibility.
- Add new activity categories to `ACTIVITY_CATEGORIES` in `src/db/models.py` and `_CATEGORY_SLOT` in `src/agents/planner.py`.
- New agents must accept a `LLMProvider` and follow the singleton pattern in `src/agents/orchestrator.py`.
- Run `pytest tests/unit/` before merging — all unit tests must pass without `GOOGLE_API_KEY` or `DATABASE_URL`.
- When adding or changing agent behaviour, update the corresponding test in `tests/unit/test_researcher_agent.py` or `test_planner_agent.py`.
- New CLI commands or web routes that involve agents or DB writes should go through `src/services/trip_service.py`.

## Do Not

- Do not add Docker — local PostgreSQL native install is the intended setup.
- Do not call `asyncio.run()` inside FastAPI route handlers.
- Do not put business logic in route files — use the service layer.
- Do not use `Base.metadata.create_all()` in production — use Alembic migrations.

## Out of Scope (for now, add later)

- Geo clustering / Google Maps API integration
- Authentication / multi-user
- Travel style preferences (budget, pace)
- Export (PDF, Google Calendar, Google Doc)
- Budget tracking
- Flights
- Opening hours awareness
- Notes per activity
- Undo/redo on schedule
