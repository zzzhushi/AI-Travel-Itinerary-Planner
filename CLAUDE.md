# CLAUDE.md

## Project Overview

Multi-agent itinerary planner powered by Google Gemini (via Google ADK). Users create trips, research activities, rate options, and generate geo-aware day-by-day schedules. CLI-first; web UI (FastAPI + HTMX + PostgreSQL) to follow.

## Architecture

```
src/
  agents/
    researcher.py     # Gemini + Google Search → 4-5 real options per activity query
    planner.py        # Geo-aware day assignment + optional LLM refinement
    orchestrator.py   # Coordinates researcher + planner; idempotency logic
  db/
    models.py         # SQLAlchemy ORM: Trip, Activity, Option, ScheduledItem, TripPreferences
    database.py       # Sync (CLI) and async (web) session factories
  tools/
    maps.py           # Google Maps: geocoding, place details, haversine distance
  cli.py              # Interactive CLI flow

run_cli.py            # Entry point: python run_cli.py [--dry-run | --json trip.json]
alembic/              # PostgreSQL migrations
web/                  # FastAPI web app (coming soon)
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
GOOGLE_MAPS_API_KEY=...      # optional: geo clustering; falls back to search links
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost/itinerary  # optional for CLI
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

# DB migrations (requires DATABASE_URL)
alembic upgrade head
```

## Key Design Decisions

- **Idempotent research**: `Option.research_hash` = sha256(trip_id + query)[:16]. Orchestrator skips re-research if hash matches.
- **Geo clustering**: planner groups options by proximity (haversine) so nearby activities share a day. Falls back to round-robin if Maps API unavailable.
- **Two-phase planning**: deterministic Python schedule first, then optional Gemini LLM refinement pass for human-readable ordering and notes.
- **Locked items**: `ScheduledItem.is_locked = True` means the planner and (later) drag-drop UI will not move that item.
- **day_number vs real dates**: Schedule uses day_number (1-based) until Trip.start_date is provided.

## Do

- Keep `research_activity()` in `src/agents/researcher.py` as the single Gemini research call.
- Use `Optional[str]` (not `str | None`) in SQLAlchemy `Mapped` columns — Python 3.14 compat.
- Check `research_hash` before calling the researcher — idempotency is caller's responsibility.
- Add new activity categories to `ACTIVITY_CATEGORIES` in `src/db/models.py` and `_CATEGORY_SLOT` in `src/agents/planner.py`.

## Do Not

- Do not add Docker — local PostgreSQL native install is the intended setup.
- Do not call `asyncio.run()` inside FastAPI route handlers.
- Do not put business logic in route files.
- Do not use `Base.metadata.create_all()` in production — use Alembic migrations.
- Do not skip the Maps enrichment step — lat/lng is needed for geo clustering.

## Out of Scope (for now, add later)

- Authentication / multi-user
- Travel style preferences (budget, pace)
- Export (PDF, Google Calendar, Google Doc)
- Budget tracking
- Flights
- Opening hours awareness
- Notes per activity
- Undo/redo on schedule
