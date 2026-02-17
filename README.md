# Itinerary Multi-Agent System

A Google ADK (Agent Development Kit) + Gemini multi-agent system that plans full travel itineraries from the **Windows CLI**. It reads a trip info file and an activity CSV, runs a research agent and an itinerary agent, and **writes results to text files** (and optionally Excel for research).

**Requirements:** Python 3.10+

## Setup

1. **Create a virtual environment (recommended)**

   ```cmd
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. **Install dependencies**

   ```cmd
   pip install -r requirements.txt
   ```

3. **Set your Gemini API key**

   Copy `.env.example` to `.env` and set:

   ```
   GOOGLE_API_KEY=your_api_key_here
   ```

   Get a key at [Google AI Studio](https://aistudio.google.com/apikey). You can also set `GOOGLE_API_KEY` in the environment (e.g. `set GOOGLE_API_KEY=...` in Windows CLI). Do not commit `.env` or your key.

## Run from Windows CLI

From the project root:

```cmd
python run_itinerary.py --trip path\to\trip.json --activities path\to\activities.csv --output-dir output
```

**Arguments:**

| Argument          | Required | Description |
|-------------------|----------|-------------|
| `--trip`          | Yes      | Path to trip info file (JSON or YAML). Must include `country`, `city`, `days`. |
| `--activities`    | Yes      | Path to activity CSV (vague activities; optional preference column 1–5). |
| `--output-dir`    | No       | Directory for output files (default: `output`). |
| `--ratings`       | No       | Path to JSON file with row-index ratings, e.g. `{"0": 5, "1": 3}`. |
| `--feedback`     | No       | Free-text feedback for the itinerary (e.g. "skip night market"). |
| `--no-excel`      | No       | Do not write `research_options.xlsx`. |

**Output files** (written into `--output-dir`):

- `research_results.txt` — Research options per activity (name, address, location, link).
- `research_options.xlsx` — Same data in Excel (unless `--no-excel`).
- `itinerary_full.txt` — Hour-by-hour plan (activities, times, travel buffers, meals/rest).
- `itinerary_take_it_easy.txt` — Lighter alternative plan.

**Example:**

```cmd
python run_itinerary.py --trip sample_trip.json --activities activities.csv --output-dir my_trip
```

Alternative entry point (with project root on `PYTHONPATH`):

```cmd
set PYTHONPATH=%CD%
python -m src.cli.run --trip sample_trip.json --activities activities.csv --output-dir my_trip
```

## File formats

- **Trip info:** JSON or YAML with required fields `country`, `city`, `days`. Optional `flight_info`. See `docs/TRIP_AND_CSV_FORMAT.md`.
- **Activity CSV:** One or two columns (activity description, optional preference 1–5). See `docs/TRIP_AND_CSV_FORMAT.md`.

## Tests

From the project root:

```cmd
pytest tests/ -v
```

## Project layout

- `run_itinerary.py` — CLI launcher (run this from Windows CLI).
- `src/cli/` — CLI logic and text output formatting.
- `docs/` — PRD, implementation checklist, trip/CSV format.
- `src/agents/` — Research agent, Itinerary agent.
- `src/tools/` — Trip parser, CSV parser, Excel export, travel time stub.
- `src/api/` — Optional FastAPI app and session store (used by CLI; can run API separately if needed).
- `tests/` — Unit and integration tests.
- `main.py` — Original ADK demo (unchanged).
