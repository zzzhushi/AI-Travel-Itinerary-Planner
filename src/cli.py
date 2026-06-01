"""
Interactive CLI for the itinerary planner.

Usage:
    python run_cli.py                   # interactive mode
    python run_cli.py --dry-run         # validate imports and config without API calls
    python run_cli.py --json trip.json  # load a trip from JSON and run non-interactively
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

load_dotenv()

console = Console()


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def check_config(dry_run: bool = False) -> list[str]:
    """Return list of missing/invalid config items."""
    issues = []
    if not os.getenv("GOOGLE_API_KEY"):
        issues.append("GOOGLE_API_KEY not set (required for Gemini + Google Search)")
    if not os.getenv("GOOGLE_MAPS_API_KEY"):
        issues.append("GOOGLE_MAPS_API_KEY not set (optional — geo clustering will be skipped)")
    if not os.getenv("DATABASE_URL"):
        issues.append("DATABASE_URL not set (will use JSON-only output)")
    return issues


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------

def prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
    return val or default


def prompt_int(text: str, min_val: int = 1, max_val: int = 100, default: Optional[int] = None) -> Optional[int]:
    suffix = f" [{default}]" if default is not None else " (press Enter to skip)"
    while True:
        raw = prompt(text + suffix)
        if not raw:
            return default
        try:
            val = int(raw)
            if min_val <= val <= max_val:
                return val
            console.print(f"[red]Enter a number between {min_val} and {max_val}.[/red]")
        except ValueError:
            console.print("[red]Please enter a number.[/red]")


def prompt_list(text: str) -> list[str]:
    raw = prompt(text + " (comma-separated, or press Enter to skip)")
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Trip data model (in-memory for CLI)
# ---------------------------------------------------------------------------

class TripSession:
    def __init__(self, name: str, destination: str, num_days: Optional[int]):
        self.name = name
        self.destination = destination
        self.num_days = num_days
        self.activities: list[dict] = []   # [{query, is_specific, category}]
        self.options: list[dict] = []      # [{option_id, name, category, ...}]
        self._option_counter = 0

    def add_option(self, activity_query: str, opt: dict) -> dict:
        self._option_counter += 1
        record = {
            "option_id": self._option_counter,
            "activity_query": activity_query,
            "name": opt["name"],
            "address": opt.get("address", ""),
            "location": opt.get("location", ""),
            "maps_link": opt.get("maps_link", ""),
            "latitude": opt.get("latitude"),
            "longitude": opt.get("longitude"),
            "category": opt.get("category", "other"),
            "why": opt.get("why", ""),
            "user_rating": None,
            "is_locked": False,
        }
        self.options.append(record)
        return record

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "destination": self.destination,
            "num_days": self.num_days,
            "activities": self.activities,
            "options": self.options,
        }


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def display_options(options: list[dict], activity_query: str) -> None:
    table = Table(title=f"Options for: [bold]{activity_query}[/bold]", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold")
    table.add_column("Location")
    table.add_column("Category", style="cyan")
    table.add_column("Maps", style="blue")
    table.add_column("Why")

    for i, opt in enumerate(options, 1):
        maps = opt.get("maps_link", "")
        maps_display = "[link]" if maps else "—"
        table.add_row(
            str(i),
            opt["name"],
            opt.get("location", ""),
            opt.get("category", ""),
            maps_display,
            opt.get("why", "")[:60],
        )
    console.print(table)


def display_schedule(day_plans, llm_days: list[dict]) -> None:
    if llm_days:
        console.print(Panel("[bold green]AI-Refined Schedule[/bold green]"))
        for day in llm_days:
            console.print(f"\n[bold]Day {day['day']}[/bold]")
            for item in day.get("items", []):
                slot = item.get("time_slot", "")
                note = item.get("note", "")
                console.print(f"  [{slot}] {item['name']}" + (f" — {note}" if note else ""))
    else:
        console.print(Panel("[bold green]Schedule[/bold green]"))
        for dp in day_plans:
            console.print(f"\n[bold]Day {dp.day_number}[/bold]")
            for item in dp.items:
                slot = item.time_slot or "anytime"
                console.print(f"  [{slot}] {item.name}")


# ---------------------------------------------------------------------------
# Main CLI flow
# ---------------------------------------------------------------------------

async def run_interactive(dry_run: bool = False) -> None:
    console.print(Panel("[bold cyan]🗺  Itinerary Planner[/bold cyan]", expand=False))

    # Config check
    issues = check_config(dry_run)
    for issue in issues:
        console.print(f"[yellow]⚠  {issue}[/yellow]")

    if dry_run:
        console.print("\n[green]✓ Dry run complete — imports and config check passed.[/green]")
        _print_config_summary()
        return

    # --- Step 1: Create trip ---
    console.print("\n[bold]New Trip[/bold]")
    name = prompt("Trip name (e.g. Japan September 2026)")
    if not name:
        console.print("[red]Trip name is required.[/red]")
        return
    destination = prompt("Destination (e.g. Tokyo, Japan)")
    if not destination:
        console.print("[red]Destination is required.[/red]")
        return
    num_days = prompt_int("Number of days", min_val=1, max_val=90)

    trip = TripSession(name=name, destination=destination, num_days=num_days)

    # --- Step 2: Seed activities ---
    console.print("\n[bold]Activities[/bold]")
    console.print("Enter activities you want to do. Can be vague ('ramen') or specific ('Ichiran Ramen Shinjuku').")
    seed_queries = prompt_list("Seed activities")
    for q in seed_queries:
        trip.activities.append({"query": q, "is_specific": False})

    if not trip.activities:
        console.print("[yellow]No activities entered. Add them now:[/yellow]")
        while True:
            q = prompt("Activity (or press Enter to continue)")
            if not q:
                break
            trip.activities.append({"query": q, "is_specific": False})

    if not trip.activities:
        console.print("[red]No activities. Exiting.[/red]")
        return

    # --- Step 3: Research ---
    from src.agents.orchestrator import research_and_enrich

    console.print(f"\n[bold]Researching {len(trip.activities)} activities for {destination}...[/bold]")
    for act in trip.activities:
        query = act["query"]
        console.print(f"\n  Researching: [cyan]{query}[/cyan]")

        options, err = await research_and_enrich(
            trip_id=1,  # In-memory trip, ID is nominal
            destination=destination,
            query=query,
            is_specific=act.get("is_specific", False),
        )

        if err:
            console.print(f"  [red]Error: {err}[/red]")
            continue
        if not options:
            console.print(f"  [yellow]No options found.[/yellow]")
            continue

        display_options(options, query)

        # Add options to trip session
        for opt in options:
            trip.add_option(query, opt)

    if not trip.options:
        console.print("[red]No options were found. Check your API key and try again.[/red]")
        return

    # --- Step 4: Rate options ---
    console.print(Panel("[bold]Rate Options[/bold]\nRate each option 1–5 (or 0 to skip). Higher = more interested."))

    for opt in trip.options:
        rating = prompt_int(
            f"  [{opt['category']}] {opt['name']} ({opt.get('location', '')})",
            min_val=0,
            max_val=5,
            default=0,
        )
        opt["user_rating"] = rating if rating else None

    # --- Step 5: Generate schedule ---
    if num_days is None:
        num_days = prompt_int("How many days for the schedule?", min_val=1, max_val=90, default=5)
        trip.num_days = num_days

    console.print(f"\n[bold]Generating schedule for {num_days} days...[/bold]")

    from src.agents.orchestrator import generate_schedule

    use_llm = prompt("Use AI to refine schedule ordering? [Y/n]", default="y").lower() != "n"

    day_plans, llm_days, warn = await generate_schedule(
        destination=destination,
        options=trip.options,
        num_days=num_days,
        use_llm_refinement=use_llm,
    )

    if warn:
        console.print(f"[yellow]{warn}[/yellow]")

    display_schedule(day_plans, llm_days)

    # --- Step 6: Save output ---
    save = prompt("\nSave to JSON? [Y/n]", default="y").lower() != "n"
    if save:
        output_path = Path(f"trip_{name.lower().replace(' ', '_')}.json")
        data = trip.to_dict()
        data["schedule"] = llm_days if llm_days else [
            {
                "day": dp.day_number,
                "items": [
                    {
                        "option_id": i.option_id,
                        "name": i.name,
                        "time_slot": i.time_slot,
                        "category": i.category,
                    }
                    for i in dp.items
                ],
            }
            for dp in day_plans
        ]
        output_path.write_text(json.dumps(data, indent=2, default=str))
        console.print(f"[green]✓ Saved to {output_path}[/green]")


def _print_config_summary() -> None:
    console.print("\nConfig status:")
    for key in ["GOOGLE_API_KEY", "GOOGLE_MAPS_API_KEY", "DATABASE_URL"]:
        val = os.getenv(key, "")
        status = "[green]✓ set[/green]" if val else "[yellow]✗ not set[/yellow]"
        console.print(f"  {key}: {status}")


async def run_from_json(json_path: str) -> None:
    """Non-interactive mode: load trip config from JSON and run research + planning."""
    data = json.loads(Path(json_path).read_text())
    destination = data.get("destination", "")
    name = data.get("name", "trip")
    num_days = data.get("num_days", 3)
    activities = data.get("activities", [])

    if not destination or not activities:
        console.print("[red]JSON must have 'destination' and 'activities'.[/red]")
        return

    trip = TripSession(name=name, destination=destination, num_days=num_days)
    trip.activities = activities

    from src.agents.orchestrator import research_and_enrich, generate_schedule

    for act in activities:
        query = act["query"] if isinstance(act, dict) else act
        is_specific = act.get("is_specific", False) if isinstance(act, dict) else False
        options, err = await research_and_enrich(1, destination, query, is_specific)
        if err:
            console.print(f"[red]{query}: {err}[/red]")
            continue
        for opt in options:
            o = trip.add_option(query, opt)
            o["user_rating"] = 4  # Default rating for non-interactive mode

    day_plans, llm_days, warn = await generate_schedule(
        destination=destination,
        options=trip.options,
        num_days=num_days or 3,
        use_llm_refinement=True,
    )
    if warn:
        console.print(f"[yellow]{warn}[/yellow]")

    display_schedule(day_plans, llm_days)

    out_path = Path(f"trip_{name.lower().replace(' ', '_')}_output.json")
    out_path.write_text(json.dumps(trip.to_dict(), indent=2, default=str))
    console.print(f"[green]✓ Saved to {out_path}[/green]")
