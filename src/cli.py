"""DB-backed interactive CLI for the itinerary planner."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional

# Persistent event loop for the CLI session.
# _run() creates and closes a new loop on every call. ADK's InMemoryRunner
# binds its HTTP sessions to whichever loop was active when it was first awaited;
# a new loop on the second call sees those sessions as belonging to a closed loop
# and raises RuntimeError("Event loop is closed"). Reusing one loop avoids this.
_loop: asyncio.AbstractEventLoop | None = None


def _run(coro):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv(override=True)

console = Console()


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


def _hhmm(minutes: Optional[int]) -> str:
    """Format minutes-from-midnight as HH:MM (e.g. 540 → '09:00')."""
    if minutes is None:
        return "??:??"
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}"


def prompt_int(
    text: str,
    min_val: int = 1,
    max_val: int = 100,
    default: Optional[int] = None,
) -> Optional[int]:
    suffix = f" [{default}]" if default is not None else " (Enter to skip)"
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


# ---------------------------------------------------------------------------
# Command: Add activities
# ---------------------------------------------------------------------------

def cmd_add_activities(session, trip) -> None:
    from src.db.models import ACTIVITY_CATEGORIES
    from src.db.queries import add_activity

    console.print(f"\n[bold]Add Activities[/bold]")
    console.print(f"Categories: {', '.join(ACTIVITY_CATEGORIES)}")
    console.print("Press Enter with no input to finish.\n")

    added = 0
    while True:
        query = prompt("Activity (or Enter to finish)")
        if not query:
            break
        cat = prompt("Category (or Enter to skip)").lower()
        category = cat if cat in ACTIVITY_CATEGORIES else None
        is_specific = prompt("Specific place? [y/N]", default="n").lower() == "y"
        add_activity(session, trip.id, query, category, is_specific)
        console.print(f"  [green]Added:[/green] {query!r}")
        added += 1

    if added:
        console.print(f"[green]{added} activit{'y' if added == 1 else 'ies'} added.[/green]")


# ---------------------------------------------------------------------------
# Command: Research options
# ---------------------------------------------------------------------------

def cmd_research(session, trip) -> None:
    from src.clients.places_client import places_client_from_env
    from src.services.trip_service import research_and_enrich

    client = places_client_from_env()
    try:
        summaries, stats = _run(
            research_and_enrich(session, trip, client)
        )
    finally:
        if client is not None:
            client.close()

    if not summaries:
        console.print("[yellow]All activities have already been researched.[/yellow]")
        return

    console.print(f"\n[bold]Researching {len(summaries)} activit{'y' if len(summaries) == 1 else 'ies'}...[/bold]")

    total_saved = 0
    for s in summaries:
        if s["error"]:
            console.print(f"  [cyan]{s['query']}[/cyan] [red]error: {s['error']}[/red]")
        else:
            total_saved += s["options_saved"]
            count_str = f"{s['options_saved']} option{'s' if s['options_saved'] != 1 else ''}"
            console.print(f"  [cyan]{s['query']}[/cyan] [green]→ {count_str}[/green]")

    console.print(f"\n[green]Done. {total_saved} options saved.[/green]")
    if stats["enriched"] or stats["failed"]:
        console.print(
            f"[dim]Places enrichment: {stats['enriched']} enriched, "
            f"{stats['failed']} not found.[/dim]"
        )


# ---------------------------------------------------------------------------
# Command: Enrich options with Places (Maps) data
# ---------------------------------------------------------------------------

def cmd_enrich(session, trip) -> None:
    from src.clients.places_client import places_client_from_env
    from src.services.trip_service import enrich_options_with_places

    client = places_client_from_env()
    if client is None:
        console.print("[yellow]GOOGLE_MAPS_API_KEY not set — Maps enrichment is unavailable.[/yellow]")
        return

    # Force re-fetches every option (ignoring the staleness gate) so newly added
    # fields like neighborhood are backfilled onto already-enriched options.
    force = prompt(
        "Force refresh ALL options (re-fetch even already-enriched)? [y/N]",
        default="n",
    ).lower() == "y"

    console.print("\n[bold]Enriching options with Maps data...[/bold]")
    try:
        stats = _run(enrich_options_with_places(session, trip, client, force=force))
    finally:
        client.close()

    if not (stats["enriched"] or stats["skipped"] or stats["failed"]):
        console.print(
            "[yellow]Nothing to enrich — all options are already up to date. "
            "Use force refresh to re-fetch them.[/yellow]"
        )
        return

    console.print(
        f"[green]Done.[/green] {stats['enriched']} enriched, "
        f"{stats['skipped']} matched without a place id, {stats['failed']} not found."
    )


# ---------------------------------------------------------------------------
# Command: Show options (with enriched fields)
# ---------------------------------------------------------------------------

def _price_label(level: Optional[int]) -> str:
    """Format a Places price_level (0–4) as Free / $..$$$$, or — when unknown."""
    if level is None:
        return "—"
    return "Free" if level == 0 else "$" * level


def cmd_show_options(session, trip) -> None:
    """Show every option grouped by activity, with its enriched Places fields.

    Useful for validating enrichment (neighborhood, Google rating, price, maps
    link). Neighborhood and the researcher's location are shown side by side.
    """
    from src.db.queries import get_options_for_trip

    activity_options = get_options_for_trip(session, trip.id)
    if not activity_options:
        console.print("[yellow]No options yet. Run research first.[/yellow]")
        return

    for act, options in activity_options:
        table = Table(title=f"[bold cyan]{act.query}[/bold cyan]", show_lines=False)
        table.add_column("#", width=3, style="dim")
        table.add_column("Name", style="bold", min_width=18)
        table.add_column("Neighborhood", min_width=10)
        table.add_column("Location", min_width=10)
        table.add_column("★", width=3)
        table.add_column("Google", width=6)
        table.add_column("Price", width=5)
        table.add_column("Maps", width=4)
        for i, opt in enumerate(options, 1):
            # neighborhood column is added in #76; getattr keeps this working on
            # branches/DBs without it (shows "—" until that lands).
            neighborhood = getattr(opt, "neighborhood", None) or "—"
            user_rating = f"★{opt.user_rating}" if opt.user_rating else "—"
            google = f"{opt.google_rating:.1f}" if opt.google_rating else "—"
            maps = "✓" if opt.maps_link else "—"
            table.add_row(
                str(i), opt.name, neighborhood, opt.location or "—",
                user_rating, google, _price_label(opt.price_level), maps,
            )
        console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Command: Rank options
# ---------------------------------------------------------------------------

def cmd_rank(session, trip, rerank: bool = False) -> None:
    from src.db.queries import get_options_for_trip, set_rating

    activity_options = get_options_for_trip(session, trip.id)
    if not activity_options:
        console.print("[yellow]No options to rank. Run research first.[/yellow]")
        return

    console.print(f"\n[bold]{'Re-rank' if rerank else 'Rank'} Options[/bold]")
    console.print("Rate 1–5 (1=low interest, 5=must-do). Press Enter to keep current.\n")

    updated = 0
    for act, options in activity_options:
        if not rerank and all(o.user_rating is not None for o in options):
            continue

        table = Table(title=f"[bold cyan]{act.query}[/bold cyan]", show_lines=True)
        table.add_column("#", width=3, style="dim")
        table.add_column("Name", style="bold", min_width=20)
        table.add_column("Location", min_width=12)
        table.add_column("Rating", width=7)
        for i, opt in enumerate(options, 1):
            rating_str = str(opt.user_rating) if opt.user_rating is not None else "—"
            table.add_row(str(i), opt.name, opt.location or "—", rating_str)
        console.print(table)

        for opt in options:
            if not rerank and opt.user_rating is not None:
                continue
            current = f"current: {opt.user_rating}" if opt.user_rating is not None else "unrated"
            rating = prompt_int(
                f"  {opt.name} ({current})",
                min_val=1,
                max_val=5,
                default=opt.user_rating,
            )
            if rating != opt.user_rating:
                set_rating(session, opt.id, rating)
                updated += 1

    console.print(f"\n[green]{updated} rating{'s' if updated != 1 else ''} updated.[/green]")


# ---------------------------------------------------------------------------
# Command: Generate itinerary
# ---------------------------------------------------------------------------

def cmd_generate(session, trip) -> None:
    from src.db.queries import get_rated_options_for_schedule, update_trip_num_days
    from src.services.trip_service import generate_and_save_schedule

    num_days = trip.num_days
    if not num_days:
        num_days = prompt_int("Number of days for the itinerary", min_val=1, max_val=90)
        if not num_days:
            console.print("[red]Number of days is required.[/red]")
            return
        update_trip_num_days(session, trip.id, num_days)
        session.refresh(trip)

    options = get_rated_options_for_schedule(session, trip.id)
    if not options:
        console.print("[yellow]No rated options found. Rate some options first.[/yellow]")
        return

    if len(options) > 60:
        console.print(
            f"[yellow]Warning: {len(options)} rated options for {num_days} days — "
            "consider raising your ratings to filter to your top picks.[/yellow]"
        )

    console.print(
        f"\n[bold]Generating itinerary:[/bold] {num_days} days, {len(options)} options"
    )
    use_llm = prompt("Use AI to refine schedule ordering? [Y/n]", default="y").lower() != "n"

    result = _run(
        generate_and_save_schedule(session, trip, num_days, use_llm_refinement=use_llm)
    )

    if result.warning:
        console.print(f"[yellow]{result.warning}[/yellow]")

    console.print("[green]Schedule saved.[/green]\n")

    header = "[bold green]AI-Refined Schedule[/bold green]" if result.source == "llm" else "[bold green]Schedule[/bold green]"
    console.print(Panel(header))
    for dp in result.day_plans:
        console.print(f"[bold]Day {dp.day_number}[/bold]")
        for item in sorted(dp.items, key=lambda i: i.start_minutes or 0):
            dur = f"{item.duration_minutes}m" if item.duration_minutes else "?"
            locked = " [yellow][locked][/yellow]" if item.is_locked else ""
            line = f"  [{_hhmm(item.start_minutes)}] {item.name}  ({dur}){locked}"
            if item.note:
                line += f"  [dim]— {item.note}[/dim]"
            console.print(line)
        console.print()


# ---------------------------------------------------------------------------
# Command: View trip
# ---------------------------------------------------------------------------

def cmd_view_trip(session, trip) -> None:
    from src.db.queries import get_options_for_trip, get_schedule

    session.refresh(trip)
    days_label = f"{trip.num_days} days" if trip.num_days else "days TBD"
    console.print(Panel(
        f"[bold]{trip.name}[/bold]\n{trip.destination}  ·  {days_label}",
        expand=False,
    ))

    # Activities + options
    activity_options = get_options_for_trip(session, trip.id)
    if not activity_options:
        console.print("[dim]No activities yet.[/dim]")
    else:
        console.print(f"\n[bold]Activities & Options[/bold]")
        for act, options in activity_options:
            researched = "[green]researched[/green]" if act.researched_at else "[yellow]unresearched[/yellow]"
            specific = " [dim](specific)[/dim]" if act.is_specific else ""
            console.print(f"\n  [cyan]{act.query}[/cyan]{specific}  {researched}")
            for opt in options:
                rating = f"★{opt.user_rating}" if opt.user_rating else "unrated"
                console.print(f"    • {opt.name}  [{opt.location or '—'}]  [dim]{rating}[/dim]")

    # Schedule
    items = get_schedule(session, trip.id)
    if items:
        console.print(f"\n[bold]Current Itinerary[/bold]")
        by_day: dict[int, list] = {}
        for si in items:
            by_day.setdefault(si.day_number or 0, []).append(si)
        for day_num in sorted(by_day):
            label = f"Day {day_num}" if day_num else "Unscheduled"
            console.print(f"\n  [bold]{label}[/bold]")
            for si in by_day[day_num]:  # already ordered by start_minutes via get_schedule
                dur = f"  ({si.duration_minutes}m)" if si.duration_minutes else ""
                locked = " [yellow][locked][/yellow]" if si.is_locked else ""
                console.print(f"    [{_hhmm(si.start_minutes)}] {si.option.name}{dur}{locked}")
    else:
        console.print("\n[dim]No itinerary generated yet.[/dim]")


# ---------------------------------------------------------------------------
# Trip selection / creation
# ---------------------------------------------------------------------------

def select_or_create_trip(session):
    from src.db.models import ACTIVITY_CATEGORIES
    from src.db.queries import (
        get_trips,
        create_trip,
        add_activity,
        get_unresearched_count,
        get_unrated_count,
        get_activities,
    )

    trips = get_trips(session)

    if trips:
        table = Table(show_header=True, header_style="bold")
        table.add_column("#", width=3, style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Destination")
        table.add_column("Days", width=5)
        table.add_column("Activities", width=10)
        table.add_column("Status")
        for i, t in enumerate(trips, 1):
            days_str = str(t.num_days) if t.num_days else "?"
            act_count = len(get_activities(session, t.id))
            unresearched = get_unresearched_count(session, t.id)
            unrated = get_unrated_count(session, t.id)
            parts = []
            if unresearched:
                parts.append(f"[yellow]{unresearched} unresearched[/yellow]")
            if unrated:
                parts.append(f"[cyan]{unrated} unrated[/cyan]")
            if not parts:
                parts.append("[green]ready[/green]")
            table.add_row(str(i), t.name, t.destination, days_str, str(act_count), ", ".join(parts))
        console.print(table)

        raw = prompt(f"\nSelect trip [1-{len(trips)}] or 'n' to create new")
        if raw.lower() != "n":
            try:
                idx = int(raw) - 1
                if 0 <= idx < len(trips):
                    return trips[idx]
            except ValueError:
                pass
            console.print("[red]Invalid selection.[/red]")
            return select_or_create_trip(session)
    else:
        console.print("[dim]No existing trips.[/dim]")

    # Create new trip
    console.print("\n[bold]Create New Trip[/bold]")
    name = prompt("Trip name")
    if not name:
        console.print("[red]Name is required.[/red]")
        sys.exit(1)
    destination = prompt("Destination")
    if not destination:
        console.print("[red]Destination is required.[/red]")
        sys.exit(1)
    num_days = prompt_int("Number of days", min_val=1, max_val=90)

    trip = create_trip(session, name, destination, num_days)
    console.print(f"[green]Created:[/green] {trip.name}")

    # Optional seed activity
    seed = prompt("\nSeed activity (or Enter to skip)")
    if seed:
        cat = prompt("Category (or Enter to skip)").lower()
        category = cat if cat in ACTIVITY_CATEGORIES else None
        add_activity(session, trip.id, seed, category, False)
        console.print(f"  [green]Added:[/green] {seed!r}")

    return trip


# ---------------------------------------------------------------------------
# Main menu loop
# ---------------------------------------------------------------------------

def main_menu(session, trip) -> None:
    from src.db.queries import (
        get_unresearched_count,
        get_unrated_count,
        get_activities,
    )

    while True:
        session.refresh(trip)
        unresearched = get_unresearched_count(session, trip.id)
        unrated = get_unrated_count(session, trip.id)
        act_count = len(get_activities(session, trip.id))
        days_label = f"{trip.num_days} days" if trip.num_days else "days TBD"

        console.rule(f"[bold]{trip.name}[/bold]  {trip.destination}  ·  {days_label}")

        research_tag = (
            f"[yellow][{unresearched} unresearched][/yellow]"
            if unresearched
            else "[green][all researched][/green]"
        )
        rank_tag = (
            f"[cyan][{unrated} unrated][/cyan]"
            if unrated
            else "[green][all rated][/green]"
        )

        console.print(f"  1. Add activities       ({act_count} total)")
        console.print(f"  2. Research options     {research_tag}")
        console.print(f"  3. Enrich w/ Maps data")
        console.print(f"  4. Rank options         {rank_tag}")
        console.print(f"  5. Re-rank options")
        console.print(f"  6. Generate itinerary")
        console.print(f"  7. Show options")
        console.print(f"  8. View trip")
        console.print(f"  9. Switch trip")
        console.print(f"  0. Exit")

        choice = prompt("\nChoice").strip()

        if choice == "1":
            cmd_add_activities(session, trip)
        elif choice == "2":
            cmd_research(session, trip)
        elif choice == "3":
            cmd_enrich(session, trip)
        elif choice == "4":
            cmd_rank(session, trip, rerank=False)
        elif choice == "5":
            cmd_rank(session, trip, rerank=True)
        elif choice == "6":
            cmd_generate(session, trip)
        elif choice == "7":
            cmd_show_options(session, trip)
        elif choice == "8":
            cmd_view_trip(session, trip)
        elif choice == "9":
            trip = select_or_create_trip(session)
        elif choice == "0":
            console.print("[dim]Goodbye.[/dim]")
            sys.exit(0)
        else:
            console.print("[red]Invalid choice.[/red]")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _print_config_summary() -> None:
    for key in ["GOOGLE_API_KEY", "GOOGLE_MAPS_API_KEY", "DATABASE_URL"]:
        val = os.getenv(key, "")
        status = "[green]set[/green]" if val else "[yellow]not set[/yellow]"
        console.print(f"  {key}: {status}")


def run_interactive(dry_run: bool = False) -> None:
    console.print(Panel("[bold cyan]Itinerary Planner[/bold cyan]", expand=False))

    if dry_run:
        _print_config_summary()
        console.print("\n[green]Dry run complete — imports and config check passed.[/green]")
        return

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        console.print("[red]DATABASE_URL not set.[/red]")
        console.print("Add to .env:  DATABASE_URL=postgresql+asyncpg://postgres:password@localhost/itinerary")
        sys.exit(1)

    from src.db.database import get_sync_session_factory

    Session = get_sync_session_factory()
    with Session() as session:
        trip = select_or_create_trip(session)
        main_menu(session, trip)
