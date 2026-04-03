# Itinerary research (simple iteration)

Google Gemini (via [Google ADK](https://google.github.io/adk-docs/)) research agent: given JSON with **country**, **city**, and **activity** (fields may be incomplete), the agent searches for concrete places and writes:

- **`research_options.xlsx`** — addresses, locations, optional `user_rating` column for downstream rating
- **`research_for_planning.json`** — same rows as JSON for another app to do hour-by-hour planning

See `prompt.md` for the product intent.

## Setup

```bash
pip install -r requirements.txt
```

Set `GOOGLE_API_KEY` (copy `.env.example` to `.env` or export in the shell).

## Run

```bash
python -m src.main --input examples/trip.json --out-dir out
```

Inline JSON:

```bash
python -m src.main --json "{\"activity\": \"salt bread, seoul\"}" --out-dir out
```

## Input JSON

- **Required:** `activity` (string) **or** `activities` (list of strings).
- **Optional:** `country`, `city` — omit when the activity text already implies location (e.g. `"salt bread, seoul"`). The research agent is instructed to infer geography from the wording when these are missing.

## Tests

```bash
pytest
```

Unit tests cover varied input quality (empty, invalid JSON, partial fields, lists) without calling the live API.
