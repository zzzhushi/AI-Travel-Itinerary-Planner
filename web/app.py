"""FastAPI web app for the itinerary planner."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Dict, Optional

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from src.db.database import get_sync_session_factory
from src.db.models import ACTIVITY_CATEGORIES, Activity, Option, ScheduledItem, Trip
from src.db.queries import (
    add_activity,
    create_trip,
    get_activities,
    get_options_for_trip,
    get_schedule,
    get_trip,
    get_trips,
    get_unrated_count,
    get_unresearched_count,
    set_option_duration,
    set_rating,
)
from src.maps.places_client import places_client_from_env
from src.services.trip_service import generate_and_save_schedule, research_and_enrich
from web.deps import get_db

app = FastAPI(title="Itinerary Planner")

_EDITABLE_TRIP_FIELDS = {"name", "destination", "num_days", "start_date", "end_date"}

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

import json as _json  # noqa: E402

def _safe_from_json(s: str) -> list:
    try:
        v = _json.loads(s)
        return v if isinstance(v, list) else []
    except Exception:
        return []

templates.env.filters["from_json"] = _safe_from_json

from src.agents.planner import category_default_duration  # noqa: E402
templates.env.globals["category_default_duration"] = category_default_duration


def _hhmm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h:02d}:{m:02d}"


templates.env.filters["hhmm"] = _hhmm
# Timeline window: 07:00–23:00 at 1 px/min = 960 px tall per day column.
templates.env.globals["TIMELINE_START"] = 420   # 07:00
templates.env.globals["TIMELINE_END"] = 1380    # 23:00
_TIMELINE_START = 420  # mirrors the Jinja global; used in route code


def _item_position(item: ScheduledItem) -> tuple[int, int]:
    """Return (item_top_px, item_height_px) for the timeline render."""
    from src.agents.planner import DEFAULT_DURATION_MINUTES, category_default_duration
    start = item.start_minutes if item.start_minutes is not None else 540
    opt = item.option
    eff_dur = (item.duration_minutes
               or (opt.default_duration_minutes if opt else None)
               or category_default_duration(opt.category if opt else None)
               or DEFAULT_DURATION_MINUTES)
    return max(start - _TIMELINE_START, 0), max(eff_dur, 20)


# ---------------------------------------------------------------------------
# In-memory job state (research + schedule generation per trip)
# ---------------------------------------------------------------------------

class _Job:
    def __init__(self):
        self.done = False
        self.error: Optional[str] = None
        self.result = None

_research_jobs: Dict[int, _Job] = {}
_schedule_jobs: Dict[int, _Job] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trip_context(
    session: Session,
    trip: Trip,
    *,
    activities=None,
    schedule=None,
) -> dict:
    """Compute status counts used across multiple templates.

    Pass pre-fetched `activities` or `schedule` to avoid duplicate DB queries
    when the caller has already fetched them.
    """
    if activities is None:
        activities = get_activities(session, trip.id)
    if schedule is None:
        schedule = get_schedule(session, trip.id)
    unresearched = get_unresearched_count(session, trip.id)
    unrated = get_unrated_count(session, trip.id)

    if not activities:
        status_label = "No activities"
    elif unresearched:
        status_label = "Ready to research"
    elif unrated:
        status_label = "Ready to rate"
    elif not schedule:
        status_label = "Ready to schedule"
    else:
        status_label = "Scheduled"

    return {
        "trip": trip,
        "activity_count": len(activities),
        "unresearched_count": unresearched,
        "unrated_count": unrated,
        "has_schedule": bool(schedule),
        "status_label": status_label,
        "categories": ACTIVITY_CATEGORIES,
    }


def _run_research_and_enrich_background(trip_id: int, job: _Job) -> None:
    """Background thread: research activities and enrich options with Places data."""
    client = places_client_from_env()
    try:
        SessionFactory = get_sync_session_factory()
        with SessionFactory() as session:
            trip = session.get(Trip, trip_id)
            if trip is None:
                return
            summaries, _stats = asyncio.run(
                research_and_enrich(session, trip, client)
            )
        job.result = summaries
    except Exception as e:
        job.error = str(e)
    finally:
        if client is not None:
            client.close()
        job.done = True


def _run_schedule_background(trip_id: int, num_days: int, use_llm: bool, job: _Job) -> None:
    """Background thread: create own session + event loop, generate schedule."""
    try:
        SessionFactory = get_sync_session_factory()
        with SessionFactory() as session:
            trip = session.get(Trip, trip_id)
            if trip is None:
                return
            day_plans, llm_days, warn = asyncio.run(
                generate_and_save_schedule(session, trip, num_days, use_llm)
            )
        job.result = {"warn": warn}
    except Exception as e:
        job.error = str(e)
    finally:
        job.done = True


# ---------------------------------------------------------------------------
# Trip list
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def trip_list(request: Request, session: Session = Depends(get_db)):
    trips = get_trips(session)
    trip_contexts = [_trip_context(session, t) for t in trips]
    return templates.TemplateResponse(request, "trips/list.html", {
        "request": request,
        "trip_contexts": trip_contexts,
    })


@app.post("/trips")
def create_trip_route(
    request: Request,
    name: str = Form(...),
    destination: str = Form(...),
    num_days: Optional[str] = Form(default=None),
    session: Session = Depends(get_db),
):
    days = int(num_days) if num_days and num_days.strip().isdigit() else None
    trip = create_trip(session, name.strip(), destination.strip(), days)
    return RedirectResponse(f"/trips/{trip.id}", status_code=303)


# ---------------------------------------------------------------------------
# Trip dashboard
# ---------------------------------------------------------------------------

@app.get("/trips/{trip_id}", response_class=HTMLResponse)
def trip_dashboard(
    request: Request,
    trip_id: int,
    tab: str = "activities",
    session: Session = Depends(get_db),
):
    trip = get_trip(session, trip_id)
    if not trip:
        return RedirectResponse("/")
    ctx = _trip_context(session, trip)
    ctx["request"] = request
    ctx["active_tab"] = tab
    ctx["tab_content"] = _render_tab(request, session, trip, tab, ctx)
    return templates.TemplateResponse(request, "trips/dashboard.html", ctx)


@app.get("/trips/{trip_id}/tabs/{tab}", response_class=HTMLResponse)
def trip_tab(
    request: Request,
    trip_id: int,
    tab: str,
    session: Session = Depends(get_db),
):
    trip = get_trip(session, trip_id)
    if not trip:
        return RedirectResponse("/")
    ctx = _trip_context(session, trip)
    return templates.TemplateResponse(request, "trips/_tabs_wrapper.html", {
        **ctx,
        "request": request,
        "active_tab": tab,
        "tab_content": _render_tab(request, session, trip, tab, ctx),
    })


def _render_tab(request: Request, session: Session, trip: Trip, tab: str, ctx: dict) -> str:
    if tab == "activities":
        activities = get_activities(session, trip.id)
        return templates.get_template("trips/_tab_activities.html").render({
            **ctx, "request": request, "activities": activities,
        })
    elif tab == "options":
        activity_options = get_options_for_trip(session, trip.id)
        return templates.get_template("trips/_tab_options.html").render({
            **ctx, "request": request, "activity_options": activity_options,
        })
    elif tab == "schedule":
        schedule = get_schedule(session, trip.id)
        # Group by day
        days: dict[int, list] = {}
        for si in schedule:
            days.setdefault(si.day_number, []).append(si)
        for items in days.values():
            items.sort(key=lambda x: x.start_minutes if x.start_minutes is not None else 9999)
        return templates.get_template("trips/_tab_schedule.html").render({
            **ctx, "request": request, "days": days,
        })
    return ""


# ---------------------------------------------------------------------------
# Inline editing: trip fields
# ---------------------------------------------------------------------------

@app.get("/trips/{trip_id}/fields/{field}", response_class=HTMLResponse)
def trip_field_edit_form(request: Request, trip_id: int, field: str, session: Session = Depends(get_db)):
    if field not in _EDITABLE_TRIP_FIELDS:
        return HTMLResponse("", status_code=404)
    trip = get_trip(session, trip_id)
    if not trip:
        return HTMLResponse("", status_code=404)
    value = getattr(trip, field, "") or ""
    return templates.TemplateResponse(request, "trips/_field_edit.html", {
        "request": request, "trip": trip, "field": field, "value": value,
    })


@app.get("/trips/{trip_id}/fields/{field}/display", response_class=HTMLResponse)
def trip_field_display(request: Request, trip_id: int, field: str, session: Session = Depends(get_db)):
    if field not in _EDITABLE_TRIP_FIELDS:
        return HTMLResponse("", status_code=404)
    trip = get_trip(session, trip_id)
    if not trip:
        return HTMLResponse("", status_code=404)
    value = getattr(trip, field, "") or ""
    return templates.TemplateResponse(request, "trips/_field_display.html", {
        "request": request, "trip": trip, "field": field, "value": value,
    })


@app.patch("/trips/{trip_id}", response_class=HTMLResponse)
async def update_trip_field(
    request: Request,
    trip_id: int,
    session: Session = Depends(get_db),
):
    form = await request.form()
    trip = get_trip(session, trip_id)
    if not trip:
        return HTMLResponse("", status_code=404)
    for field in ("name", "destination", "num_days", "start_date", "end_date"):
        if field in form:
            raw = form[field]
            if field == "num_days":
                val = int(raw) if str(raw).strip().isdigit() else None
            else:
                val = str(raw).strip() or None if field in ("start_date", "end_date") else str(raw).strip()
            setattr(trip, field, val)
    session.commit()
    session.refresh(trip)
    field = next(iter(form.keys()), "name")
    value = getattr(trip, field, "") or ""
    return templates.TemplateResponse(request, "trips/_field_display.html", {
        "request": request, "trip": trip, "field": field, "value": value,
    })


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

@app.post("/trips/{trip_id}/activities", response_class=HTMLResponse)
def add_activity_route(
    request: Request,
    trip_id: int,
    query: str = Form(...),
    category: Optional[str] = Form(default=None),
    is_specific: bool = Form(default=False),
    session: Session = Depends(get_db),
):
    trip = get_trip(session, trip_id)
    if not trip:
        return HTMLResponse("", status_code=404)
    cat = category if category and category in ACTIVITY_CATEGORIES else None
    act = add_activity(session, trip_id, query.strip(), cat, is_specific)
    return templates.TemplateResponse(request, "trips/_activity_row.html", {
        "request": request, "trip": trip, "act": act, "categories": ACTIVITY_CATEGORIES,
    })


@app.get("/trips/{trip_id}/activities/{act_id}/edit", response_class=HTMLResponse)
def edit_activity_form(request: Request, trip_id: int, act_id: int, session: Session = Depends(get_db)):
    trip = get_trip(session, trip_id)
    act = session.get(Activity, act_id)
    if not trip or not act or act.trip_id != trip_id:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "trips/_activity_edit.html", {
        "request": request, "trip": trip, "act": act, "categories": ACTIVITY_CATEGORIES,
    })


@app.get("/trips/{trip_id}/activities/{act_id}", response_class=HTMLResponse)
def get_activity_row(request: Request, trip_id: int, act_id: int, session: Session = Depends(get_db)):
    trip = get_trip(session, trip_id)
    act = session.get(Activity, act_id)
    if not trip or not act or act.trip_id != trip_id:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "trips/_activity_row.html", {
        "request": request, "trip": trip, "act": act, "categories": ACTIVITY_CATEGORIES,
    })


@app.patch("/trips/{trip_id}/activities/{act_id}", response_class=HTMLResponse)
async def update_activity(
    request: Request,
    trip_id: int,
    act_id: int,
    session: Session = Depends(get_db),
):
    form = await request.form()
    trip = get_trip(session, trip_id)
    act = session.get(Activity, act_id)
    if not act or act.trip_id != trip_id:
        return HTMLResponse("", status_code=404)
    if "query" in form:
        act.query = str(form["query"]).strip()
    if "category" in form:
        cat = str(form["category"])
        act.category = cat if cat in ACTIVITY_CATEGORIES else None
    if "is_specific" in form:
        act.is_specific = str(form["is_specific"]).lower() in ("true", "1", "on")
    session.commit()
    session.refresh(act)
    return templates.TemplateResponse(request, "trips/_activity_row.html", {
        "request": request, "trip": trip, "act": act, "categories": ACTIVITY_CATEGORIES,
    })


@app.delete("/trips/{trip_id}/activities/{act_id}", response_class=HTMLResponse)
def delete_activity(trip_id: int, act_id: int, session: Session = Depends(get_db)):
    act = session.get(Activity, act_id)
    if act and act.trip_id == trip_id:
        session.delete(act)
        session.commit()
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Research (batch, with polling)
# ---------------------------------------------------------------------------

@app.post("/trips/{trip_id}/research", response_class=HTMLResponse)
def start_research(
    request: Request,
    trip_id: int,
    session: Session = Depends(get_db),
):
    job = _Job()
    _research_jobs[trip_id] = job
    # Run in a thread so it can call asyncio.run() without conflicting with FastAPI's loop
    t = threading.Thread(target=_run_research_and_enrich_background, args=(trip_id, job), daemon=True)
    t.start()
    return templates.TemplateResponse(request, "trips/_research_status.html", {
        "request": request, "trip_id": trip_id, "message": "Researching activities…",
    })


@app.get("/trips/{trip_id}/research/status", response_class=HTMLResponse)
def research_status(request: Request, trip_id: int, session: Session = Depends(get_db)):
    job = _research_jobs.get(trip_id)
    if job is None or not job.done:
        return templates.TemplateResponse(request, "trips/_research_status.html", {
            "request": request, "trip_id": trip_id, "message": "Researching activities…",
        })
    # Done — return full tab wrapper so the tab bar stays visible
    trip = get_trip(session, trip_id)
    _research_jobs.pop(trip_id, None)
    if not trip:
        return HTMLResponse("", status_code=410)
    activities = get_activities(session, trip.id)
    ctx = _trip_context(session, trip, activities=activities)
    error = job.error
    tab_content = templates.get_template("trips/_tab_activities.html").render({
        **ctx, "request": request, "activities": activities,
        "research_done": True, "research_error": error,
    })
    return templates.TemplateResponse(request, "trips/_tabs_wrapper.html", {
        "request": request, **ctx, "active_tab": "activities", "tab_content": tab_content,
    })


# ---------------------------------------------------------------------------
# Options / Rating
# ---------------------------------------------------------------------------

@app.post("/trips/{trip_id}/options/{opt_id}/rate", response_class=HTMLResponse)
async def rate_option(
    request: Request,
    trip_id: int,
    opt_id: int,
    session: Session = Depends(get_db),
):
    form = await request.form()
    rating = int(form.get("rating", 0))
    opt = session.get(Option, opt_id)
    if not opt:
        return HTMLResponse("", status_code=404)
    set_rating(session, opt_id, rating if rating > 0 else None)
    session.refresh(opt)
    return templates.TemplateResponse(request, "trips/_star_rating.html", {
        "request": request, "trip_id": trip_id, "opt": opt,
    })


@app.patch("/trips/{trip_id}/options/{opt_id}/duration", response_class=HTMLResponse)
async def set_duration(
    request: Request,
    trip_id: int,
    opt_id: int,
    session: Session = Depends(get_db),
):
    form = await request.form()
    opt = session.get(Option, opt_id)
    if not opt or opt.activity.trip_id != trip_id:
        return HTMLResponse("", status_code=404)
    raw = str(form.get("duration_minutes", "")).strip()
    if raw.isdigit() and int(raw) >= 1:
        minutes: Optional[int] = min(int(raw), 1440)
    else:
        minutes = None  # blank or invalid → revert to category default
    set_option_duration(session, opt_id, minutes)
    session.refresh(opt)
    effective_default = category_default_duration(opt.category or opt.activity.category)
    return templates.TemplateResponse(request, "trips/_duration_edit.html", {
        "request": request, "trip_id": trip_id, "opt": opt,
        "effective_default": effective_default,
    })


# ---------------------------------------------------------------------------
# Schedule (generate + polling)
# ---------------------------------------------------------------------------

@app.post("/trips/{trip_id}/schedule", response_class=HTMLResponse)
async def generate_schedule_route(
    request: Request,
    trip_id: int,
    session: Session = Depends(get_db),
):
    form = await request.form()
    use_llm = form.get("use_llm", "true").lower() != "false"
    trip = get_trip(session, trip_id)
    if not trip:
        return HTMLResponse("", status_code=404)
    num_days = trip.num_days or 3

    job = _Job()
    _schedule_jobs[trip_id] = job
    t = threading.Thread(
        target=_run_schedule_background, args=(trip_id, num_days, use_llm, job), daemon=True
    )
    t.start()
    return templates.TemplateResponse(request, "trips/_schedule_status.html", {
        "request": request, "trip_id": trip_id, "message": "Generating schedule…",
    })


@app.get("/trips/{trip_id}/schedule/status", response_class=HTMLResponse)
def schedule_status(request: Request, trip_id: int, session: Session = Depends(get_db)):
    job = _schedule_jobs.get(trip_id)
    if job is None or not job.done:
        return templates.TemplateResponse(request, "trips/_schedule_status.html", {
            "request": request, "trip_id": trip_id, "message": "Generating schedule…",
        })
    trip = get_trip(session, trip_id)
    _schedule_jobs.pop(trip_id, None)
    if not trip:
        return HTMLResponse("", status_code=410)
    schedule = get_schedule(session, trip.id)
    days: dict[int, list] = {}
    for si in schedule:
        days.setdefault(si.day_number, []).append(si)
    for items in days.values():
        items.sort(key=lambda x: x.start_minutes if x.start_minutes is not None else 9999)
    ctx = _trip_context(session, trip, schedule=schedule)
    warn = job.result.get("warn", "") if job.result else ""
    error = job.error
    tab_content = templates.get_template("trips/_tab_schedule.html").render({
        **ctx, "request": request, "days": days,
        "schedule_done": True, "schedule_warn": warn, "schedule_error": error,
    })
    return templates.TemplateResponse(request, "trips/_tabs_wrapper.html", {
        "request": request, **ctx, "active_tab": "schedule", "tab_content": tab_content,
    })


# ---------------------------------------------------------------------------
# Schedule item edits (lock / time_slot / day_number)
# ---------------------------------------------------------------------------

@app.patch("/trips/{trip_id}/schedule/{item_id}", response_class=HTMLResponse)
async def update_schedule_item(
    request: Request,
    trip_id: int,
    item_id: int,
    session: Session = Depends(get_db),
):
    form = await request.form()
    item = session.get(ScheduledItem, item_id)
    if not item or item.trip_id != trip_id:
        return HTMLResponse("", status_code=404)
    if "is_locked" in form:
        item.is_locked = str(form["is_locked"]).lower() in ("true", "1", "on")
    if "time_slot" in form:
        item.time_slot = str(form["time_slot"])
    if not item.is_locked:
        # Movement fields are ignored for locked items — lock is enforced both
        # in the JS (no interact.js on locked elements) and here server-side.
        if "day_number" in form:
            raw = str(form["day_number"])
            if raw.isdigit():
                item.day_number = int(raw)
        if "start_minutes" in form:
            raw = str(form["start_minutes"])
            if raw.lstrip("-").isdigit():
                item.start_minutes = min(max(int(raw), 0), 1439)
        if "duration_minutes" in form:
            raw = str(form["duration_minutes"])
            if raw.isdigit() and int(raw) >= 1:
                item.duration_minutes = min(int(raw), 1440)
    session.commit()
    session.refresh(item)
    item_top, item_height = _item_position(item)
    return templates.TemplateResponse(request, "trips/_schedule_item.html", {
        "request": request, "trip_id": trip_id, "item": item,
        "item_top": item_top, "item_height": item_height,
    })
