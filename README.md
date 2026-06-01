# Itinerary Planner

A multi-agent travel itinerary planner powered by Google Gemini. Create trips, research activities, rate options, and generate geo-aware day-by-day schedules. CLI-first with a web UI coming soon.

## How it works

1. **Create a trip** — give it a destination (e.g. "Tokyo, Japan") and optionally how many days
2. **Add activities** — vague ("ramen in Shinjuku") or specific ("Ichiran Ramen Shinjuku") 
3. **Research** — the agent searches the web and returns 4–5 real options per activity, each with a Google Maps link
4. **Rate** — score each option 1–5 based on how much you want to go
5. **Schedule** — the planner clusters nearby options onto the same day and generates a day-by-day itinerary

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
| `GOOGLE_MAPS_API_KEY` | Optional | Geo clustering — groups nearby activities on the same day |
| `DATABASE_URL` | Optional | PostgreSQL persistence; falls back to JSON output without it |

### 3. Run the CLI

```bash
# Interactive mode
python run_cli.py

# Validate config without making API calls
python run_cli.py --dry-run

# Non-interactive from a JSON file
python run_cli.py --json examples/trip.json
```

### 4. Database setup (optional)

```bash
createdb itinerary
alembic upgrade head
```

## CLI walkthrough

```
Trip name: Japan September 2026
Destination: Tokyo, Japan
Number of days: 5
Seed activities: ramen, teamLab, Senso-ji temple, Shibuya crossing

Researching 4 activities...

Options for: ramen
  1. Ichiran Ramen (Shibuya)      [food]  ★ ?
  2. Fuunji (Shinjuku)            [food]  ★ ?
  ...

Rate each option 1–5 (0 to skip)

Generating schedule for 5 days...

Day 1  [morning] TeamLab Borderless  [afternoon] Shibuya Crossing
Day 2  [morning] Senso-ji Temple     [afternoon] Ichiran Ramen
...

Save to JSON? [Y/n]
```

## Project structure

```
src/
  agents/
    researcher.py     # Gemini + Google Search → options per activity
    planner.py        # Geo clustering + time slot assignment + LLM refinement
    orchestrator.py   # Coordinates research + planning, handles idempotency
  db/
    models.py         # SQLAlchemy ORM models
    database.py       # Sync (CLI) and async (web) session factories
  tools/
    maps.py           # Google Maps geocoding + haversine distance
  cli.py              # Interactive CLI

run_cli.py            # Entry point
alembic/              # DB migrations
web/                  # FastAPI + HTMX web app (coming soon)
```

## Key design decisions

- **Idempotent research** — activities are hashed by `(trip_id, query)`; re-running won't create duplicate options
- **Geo clustering** — the planner uses haversine distance to group nearby activities on the same day, avoiding cross-city back-and-forth; falls back to round-robin without a Maps API key
- **Two-phase scheduling** — deterministic Python clustering first, then an optional Gemini pass for human-readable ordering and notes
- **Locked items** — mark a scheduled item as locked and the planner (and later the drag-drop UI) will never move it
- **Day numbers vs dates** — the schedule uses `day_number` (1-based) until `start_date` is set on the trip

## Coming soon

- Web UI — trip dashboard, activity/options/schedule tabs, drag-drop calendar, star ratings
- User settings page — enter API keys, stored in `.env`
- Export to Google Doc
- Budget tracking
- Opening hours awareness
- Notes per activity
