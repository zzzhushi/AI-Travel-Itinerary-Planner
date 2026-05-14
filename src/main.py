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
import json
import sys
from pathlib import Path
import os
import re
from typing import Any

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.adk.tools import Any, google_search
from google.genai import types

# from src.input_parser import parse_trip_input, validate_has_activity
# from src.research_agent import run_research
# from src.excel_export import rows_to_excel, rows_to_planning_json


# def _parse_args() -> argparse.Namespace:
#     p = argparse.ArgumentParser(description="Research activities with Gemini + export Excel + JSON for planning.")
#     g = p.add_mutually_exclusive_group(required=True)
#     g.add_argument("--input", "-i", type=Path, help="Path to JSON file (country, city, activity/activities).")
#     g.add_argument("--json", "-j", type=str, help="Inline JSON string.")
#     p.add_argument("--out-dir", "-o", type=Path, default=Path("out"), help="Output directory (default: out).")
#     return p.parse_args()


async def _async_main() -> int:

    ## 1. get and validate args
    #### todo: move arg parsing to a separate function, and add support for inline JSON
    #### todo: take in file  

    # args = _parse_args()
    # if args.input:
    #     raw = args.input.read_text(encoding="utf-8")
    # else:
    #     raw = args.json

    # try:
    #     parsed = parse_trip_input(raw)
    #     validate_has_activity(parsed)
    # except Exception as e:
    #     print(f"Input error: {e}", file=sys.stderr)
    #     return 1

    parsed = json.loads('{"country": "South Korea", "city": "Seoul", "activity": "night market"}')

    ## 2. run research agent
    print("Running research agent…")

    rows, err = await run_research(parsed)
    if err:
        print(f"Research error: {err}", file=sys.stderr)
        return 1

    # args.out_dir.mkdir(parents=True, exist_ok=True)
    # xlsx_path = args.out_dir / "research_options.xlsx"
    # json_path = args.out_dir / "research_for_planning.json"

    # xlsx_path.write_bytes(rows_to_excel(rows))
    # json_path.write_text(rows_to_planning_json(rows), encoding="utf-8")

    # print(f"Wrote {xlsx_path}")
    # print(f"Wrote {json_path}")
    # return 0

def _retry_config() -> types.HttpRetryOptions:
    return types.HttpRetryOptions(
        attempts=5,
        exp_base=7,
        initial_delay=1,
        http_status_codes=[429, 500, 503, 504],
    )


RESEARCH_INSTRUCTION = """You are a travel research assistant. 
The user may only provide a vague activity; city and country may be missing — infer geography from the activity text (e.g. "salt bread, seoul" implies Seoul, Korea) and use google_search to find 2-4 popular, real options per activity in the right area.

You MUST respond with a single JSON array only — no markdown, no code fences, no extra text. Each element must be an object with keys:
- "activity_query" (string): the vague activity this row answers
- "option_name" (string): place or experience name
- "address" (string): street or area address if known, else best effort
- "location" (string): neighborhood, district, or area
- "link" (string): URL if found, else ""

Search in the local language or English as needed. When city/country are unknown, infer location from the activity wording before searching."""



async def run_research(parsed: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """
    Run the research agent. Returns (rows, error_message).
    error_message empty on success.
    """
    if os.getenv("GOOGLE_API_KEY"):
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

    agent = Agent(
        name="ResearchAgent",
        model=Gemini(model="gemini-2.5-flash-lite", retry_options=_retry_config()),
        instruction=RESEARCH_INSTRUCTION,
        tools=[google_search],
        output_key="research_results",
    )
    runner = InMemoryRunner(agent=agent)
    message = build_user_message(parsed)

    try:
        response = await runner.run_debug(message)
    except Exception as e:
        return [], str(e)

    text = ""
    if hasattr(response, "content") and response.content:
        text = response.content if isinstance(response.content, str) else str(response.content)
    elif hasattr(response, "events") and response.events:
        for ev in response.events:
            if hasattr(ev, "content") and ev.content:
                part = ev.content
                if hasattr(part, "text"):
                    text = part.text or ""
                elif isinstance(part, str):
                    text = part
                if text:
                    break
    elif isinstance(response, str):
        text = response

    # parsed_json = _extract_json_array(text)
    # if not parsed_json:
    #     return [], "Could not parse JSON array from model response."
    # rows = _normalize_rows(parsed_json)
    # if not rows:
    #     return [], "Model returned no valid rows."
    # return rows, ""


def build_user_message(parsed: dict[str, Any]) -> str:
    country = parsed.get("country") or ""
    city = parsed.get("city") or ""
    acts = parsed.get("activity") or []
    return (
        f"Trip context — country: {country or '(unknown)'}, city: {city or '(unknown)'}. "
        f"Research these activities and return the JSON array as specified: {json.dumps(acts)}"
    )


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    main()
