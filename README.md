# Itinerary Planner

A multi-agent travel itinerary planner powered by Google Gemini. Create trips, research activities, rate options, and generate a day-by-day schedule. Available as both a CLI and a web UI (FastAPI + HTMX).

## How it works

1. **Create a trip** — give it a destination (e.g. "Tokyo, Japan") and optionally how many days
2. **Add activities** — vague ("ramen in Shinjuku") or specific ("Ichiran Ramen Shinjuku")
3. **Research** — the agent searches the web and returns 4–5 real options per activity
4. **Rate** — score each option 1–5 based on how much you want to go
5. **Schedule** — the planner distributes rated options across days and generates a day-by-day itinerary, with an optional AI pass for better ordering

## Local setup

**Requirements:** Python 3.12+, PostgreSQL (native install — no Docker).

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

| Variable | Required | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | Yes | Gemini + Google Search (research agent) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |

### 3. Database setup

```bash
createdb itinerary
alembic upgrade head
```

### 4. Run the app

**CLI:**
```bash
# Interactive mode
python run_cli.py

# Validate config without making API calls
python run_cli.py --dry-run
```

**Web UI** (opens at `http://127.0.0.1:8000`):
```bash
python run_web.py
```

## Web UI walkthrough

Open `http://127.0.0.1:8000` in your browser.

**Trip list** — shows all trips with their status (No activities / Ready to research / Ready to rate / Ready to schedule / Scheduled). Use the form at the top to create a new trip.

**Trip dashboard** — click any trip to open its dashboard. Fields (name, destination, days, dates) are inline-editable — click a value to edit it in place.

The dashboard has three tabs:

| Tab | What you can do |
|---|---|
| **Activities** | Add activities by typing a query (e.g. "ramen in Shinjuku"). Set a category and mark it as specific if you want a particular place. Edit or delete existing activities. Click **Research** to fetch 4–5 real options per activity via Gemini + Google Search. |
| **Options** | Review the researched options grouped by activity. Rate each option 1–5 stars — only options rated ≥ 3 are included in the schedule. |
| **Schedule** | Click **Generate schedule** to distribute rated options across days. Toggle **Use AI** to enable the Gemini refinement pass (better geographic ordering + notes). Each scheduled item shows its day, time slot, and an optional AI note. You can lock an item (padlock icon) to pin it in place, or edit its time slot and day number inline. |

## CLI walkthrough

```
Trip name: Japan September 2026
Destination: Tokyo, Japan
Number of days: 5
Seed activity: ramen

Researching 1 activity...
  ramen → 4 options

Rank Options
  Ichiran Ramen (Shibuya)   → 5
  Fuunji (Shinjuku)         → 4
  ...

Generating itinerary: 5 days, 4 options

Day 1  [afternoon] Ichiran Ramen
Day 2  [afternoon] Fuunji
...
```

## Project structure

```
src/
  agents/
    providers/          # LLMProvider ABC + GeminiProvider
    researcher.py       # ResearcherAgent — Gemini + Google Search
    planner.py          # PlannerAgent (LLM refinement) + build_schedule() (pure Python)
    orchestrator.py     # Public API: research(), research_batch(), generate_schedule()
  db/
    models.py           # SQLAlchemy ORM models
    queries.py          # DB query helpers
    database.py         # Sync + async session factories
  services/
    trip_service.py     # Shared business logic (CLI + web routes both use this)
  cli.py                # Interactive CLI

run_cli.py              # CLI entry point
run_web.py              # Web entry point (uvicorn)
alembic/                # DB migrations
tests/unit/             # Unit tests — no API keys or DB required
web/
  app.py                # FastAPI routes (trip CRUD, research, schedule, rating)
  deps.py               # DB session dependency
  templates/trips/      # Jinja2 + HTMX templates (list, dashboard, tab partials)
```

## Key design decisions

- **Idempotent research** — activities are hashed by `(trip_id, query)`; re-running won't create duplicate options
- **Injectable providers** — agents take a `LLMProvider` at construction; `MockProvider` in tests means no API keys needed to run the test suite
- **Service layer** — `src/services/trip_service.py` holds the business logic shared between the CLI and the coming web routes
- **Two-phase scheduling** — deterministic Python round-robin first, then an optional Gemini pass for human-readable ordering and notes
- **Locked items** — mark a scheduled item as locked and the planner (and later the drag-drop UI) will never move it
- **Day numbers vs dates** — the schedule uses `day_number` (1-based) until `start_date` is set on the trip

## Running tests

```bash
pytest tests/unit/   # fast, no API keys or database needed
pytest               # all tests
```

## Coming soon

- Geo clustering — group nearby activities onto the same day
- User and activity preferences — travel style, pace, duration hints
- Budget tracking
- Opening hours awareness
- Drag-drop calendar in the web UI
