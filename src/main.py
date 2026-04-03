"""
CLI: read JSON input, run research agent, write Excel + planning JSON.

Example:
  python -m src.main --input examples/trip.json --out-dir ./out

Or inline JSON:
  python -m src.main --json "{\"activity\": \"salt bread, seoul\", \"city\": \"Seoul\"}" --out-dir ./out
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from src.input_parser import parse_trip_input, validate_has_activity
from src.research_agent import run_research
from src.excel_export import rows_to_excel, rows_to_planning_json


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Research activities with Gemini + export Excel + JSON for planning.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", "-i", type=Path, help="Path to JSON file (country, city, activity/activities).")
    g.add_argument("--json", "-j", type=str, help="Inline JSON string.")
    p.add_argument("--out-dir", "-o", type=Path, default=Path("out"), help="Output directory (default: out).")
    return p.parse_args()


async def _async_main() -> int:
    args = _parse_args()
    if args.input:
        raw = args.input.read_text(encoding="utf-8")
    else:
        raw = args.json

    try:
        parsed = parse_trip_input(raw)
        validate_has_activity(parsed)
    except Exception as e:
        print(f"Input error: {e}", file=sys.stderr)
        return 1

    print("Running research agent…")
    rows, err = await run_research(parsed)
    if err:
        print(f"Research error: {err}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = args.out_dir / "research_options.xlsx"
    json_path = args.out_dir / "research_for_planning.json"

    xlsx_path.write_bytes(rows_to_excel(rows))
    json_path.write_text(rows_to_planning_json(rows), encoding="utf-8")

    print(f"Wrote {xlsx_path}")
    print(f"Wrote {json_path}")
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())
