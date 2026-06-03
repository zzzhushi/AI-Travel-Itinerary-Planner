# Itinerary Planner

A multi-agent travel itinerary planner powered by Google Gemini. Create trips, research activities, rate options, and generate a day-by-day schedule — as a CLI or web UI (FastAPI + HTMX).

## How it works

1. **Create a trip** — destination and number of days
2. **Add activities** — vague ("ramen in Shinjuku") or specific ("Ichiran Ramen Shinjuku")
3. **Research** — Gemini + Google Search returns 4–5 real options per activity, optionally enriched with hours, ratings, and map links via Google Places
4. **Rate** — score each option 1–5; only options rated ≥ 3 are scheduled
5. **Schedule** — options are distributed across days; an optional AI pass assigns real clock times, respects opening hours, and minimises geographic backtracking. Lock any item to pin it to its day and time across regenerations.

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
| `GOOGLE_MAPS_API_KEY` | No | Google Places enrichment (hours, ratings, map links) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |

### 3. Database setup

```bash
createdb itinerary
alembic upgrade head
```

### 4. Run

**CLI:**
```bash
python run_cli.py            # interactive
python run_cli.py --dry-run  # validate config without API calls
```

**Web UI** (opens at `http://127.0.0.1:8000`):
```bash
python run_web.py
```

## Web UI

Open `http://127.0.0.1:8000`. Create a trip, then use the three-tab dashboard:

| Tab | What you can do |
|---|---|
| **Activities** | Add activities by query. Mark as specific if you want a particular place. Click **Research** to fetch options via Gemini + Google Search. |
| **Options** | Review options grouped by activity. Rate each 1–5 stars — only options rated ≥ 3 appear in the schedule. |
| **Schedule** | Click **Generate schedule**. Toggle **Use AI** for the Gemini refinement pass (real clock times, opening hours, geographic flow). Lock an item to pin it to its day and time across regenerations. |

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

Day 1  09:00  Ichiran Ramen
Day 2  09:00  Fuunji
...
```

## Running tests

```bash
pytest tests/unit/   # fast, no API keys or database needed
pytest               # all tests
```

## Coming soon

- Geo clustering — group nearby options onto the same day
- User preferences — trip-wide style and per-activity hints
- Map view — visualise schedule and options on a map
- Travel times between stops (Maps API)
- Flights and hotel bookings
