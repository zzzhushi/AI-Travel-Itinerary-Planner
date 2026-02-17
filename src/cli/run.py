"""
Windows CLI entry point for the itinerary multi-agent system.

Run from project root:
  python -m src.cli.run --trip path/to/trip.json --activities path/to/activities.csv --output-dir ./output

Or (if PYTHONPATH includes project root):
  python src/cli/run.py --trip trip.json --activities activities.csv
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run itinerary planner: research activities and build hour-by-hour plan. Outputs text files.",
    )
    parser.add_argument(
        "--trip",
        required=True,
        metavar="FILE",
        help="Path to trip info file (JSON or YAML). Required: country, city, days.",
    )
    parser.add_argument(
        "--activities",
        required=True,
        metavar="FILE",
        help="Path to activity CSV (vague activities and optional preference 1-5).",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        metavar="DIR",
        help="Directory for output text files (default: output).",
    )
    parser.add_argument(
        "--ratings",
        metavar="FILE",
        help="Optional JSON file with ratings: {\"0\": 5, \"1\": 3} for row index -> 1-5.",
    )
    parser.add_argument(
        "--feedback",
        default="",
        help="Optional feedback for itinerary (e.g. 'skip night market, add more rest').",
    )
    parser.add_argument(
        "--no-excel",
        action="store_true",
        help="Do not write research_options.xlsx.",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    # Resolve paths
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from src.api.session_store import (
        get_or_create_session,
        set_trip,
        set_activities,
        set_user_ratings,
        set_feedback,
    )
    from src.api.runner_service import run_research, run_itinerary
    from src.tools.trip_parser import parse_trip_file, TripParseError
    from src.tools.csv_parser import parse_activity_csv, CsvParseError
    from src.tools.excel_export import research_results_to_excel
    from src.cli.text_output import format_research_results, format_itinerary_plan

    trip_path = Path(args.trip)
    activities_path = Path(args.activities)
    output_dir = Path(args.output_dir)

    if not trip_path.is_file():
        print(f"Error: Trip file not found: {trip_path}", file=sys.stderr)
        return 1
    if not activities_path.is_file():
        print(f"Error: Activities file not found: {activities_path}", file=sys.stderr)
        return 1

    # Parse inputs
    try:
        trip_content = trip_path.read_bytes()
        trip = parse_trip_file(trip_content, trip_path.name)
    except TripParseError as e:
        print(f"Error parsing trip file: {e.message}", file=sys.stderr)
        return 1
    try:
        csv_content = activities_path.read_text(encoding="utf-8")
        rows = parse_activity_csv(csv_content)
        activity_list = [r.to_dict() for r in rows]
    except CsvParseError as e:
        print(f"Error parsing activities CSV: {e.message}", file=sys.stderr)
        return 1

    # Session and ratings
    session_id = get_or_create_session(None)
    set_trip(session_id, trip.to_dict())
    set_activities(session_id, activity_list)
    if args.ratings:
        ratings_path = Path(args.ratings)
        if ratings_path.is_file():
            try:
                ratings = json.loads(ratings_path.read_text(encoding="utf-8"))
                if isinstance(ratings, dict):
                    set_user_ratings(session_id, {k: int(v) for k, v in ratings.items() if str(v).isdigit()})
            except (json.JSONDecodeError, ValueError):
                pass
    if args.feedback:
        set_feedback(session_id, args.feedback)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Run research
    print("Running research agent...")
    results, err = await run_research(session_id)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    if not results:
        print("Error: No research results.", file=sys.stderr)
        return 1

    research_txt = output_dir / "research_results.txt"
    research_txt.write_text(format_research_results(results), encoding="utf-8")
    print(f"Wrote {research_txt}")

    if not args.no_excel:
        try:
            excel_bytes = research_results_to_excel(results)
            (output_dir / "research_options.xlsx").write_bytes(excel_bytes)
            print(f"Wrote {output_dir / 'research_options.xlsx'}")
        except Exception as e:
            print(f"Warning: Could not write Excel: {e}", file=sys.stderr)

    # Run itinerary
    print("Running itinerary agent...")
    plan, alternatives, err = await run_itinerary(session_id, step_mode="full", feedback=args.feedback)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    full_txt = output_dir / "itinerary_full.txt"
    full_txt.write_text(format_itinerary_plan(plan or [], "Itinerary (full)"), encoding="utf-8")
    print(f"Wrote {full_txt}")

    easy_txt = output_dir / "itinerary_take_it_easy.txt"
    easy_txt.write_text(
        format_itinerary_plan(alternatives or [], "Itinerary (take it easy)"),
        encoding="utf-8",
    )
    print(f"Wrote {easy_txt}")

    print("Done.")
    return 0


def main() -> int:
    args = _parse_args()
    if not os.getenv("GOOGLE_API_KEY"):
        print("Warning: GOOGLE_API_KEY not set. Set it in the environment or .env for agent calls.", file=sys.stderr)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
