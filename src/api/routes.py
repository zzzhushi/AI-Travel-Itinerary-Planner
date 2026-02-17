"""FastAPI routes for trip upload, research, ratings, itinerary."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from .runner_service import run_research, run_itinerary
from .schemas import (
    TripUploadResponse,
    ActivitiesUploadResponse,
    RunResearchRequest,
    RunResearchResponse,
    SubmitRatingsRequest,
    RunItineraryRequest,
    RunItineraryResponse,
    GetItineraryResponse,
)
from .session_store import (
    get_or_create_session,
    get_session,
    set_trip,
    set_activities,
    set_user_ratings,
    set_feedback,
    set_step_mode,
    TRIP_INFO,
    RESEARCH_RESULTS,
    ITINERARY_PLAN,
    ITINERARY_ALTERNATIVES,
    EXCEL_BYTES,
)
from ..tools.trip_parser import parse_trip_file, TripParseError
from ..tools.csv_parser import parse_activity_csv, CsvParseError

router = APIRouter(prefix="/api", tags=["itinerary"])


@router.post("/session", response_model=dict)
def create_session(session_id: str | None = None) -> dict[str, str]:
    """Create or get a session. Returns session_id."""
    sid = get_or_create_session(session_id)
    return {"session_id": sid}


@router.post("/trip", response_model=TripUploadResponse)
async def upload_trip(
    file: UploadFile = File(...),
    session_id: str | None = None,
) -> TripUploadResponse:
    """Upload trip info file (JSON or YAML). Required: country, city, days."""
    content = await file.read()
    try:
        trip = parse_trip_file(content, file.filename or "")
    except TripParseError as e:
        raise HTTPException(status_code=400, detail=e.message)
    sid = get_or_create_session(session_id)
    set_trip(sid, trip.to_dict())
    return TripUploadResponse(
        trip_id=sid,
        country=trip.country,
        city=trip.city,
        days=trip.days,
    )


@router.post("/activities", response_model=ActivitiesUploadResponse)
async def upload_activities(
    file: UploadFile = File(...),
    session_id: str | None = None,
) -> ActivitiesUploadResponse:
    """Upload activity CSV. Returns activity count and list."""
    content = await file.read()
    try:
        rows = parse_activity_csv(content)
    except CsvParseError as e:
        raise HTTPException(status_code=400, detail=e.message)
    sid = get_or_create_session(session_id)
    activity_list = [r.to_dict() for r in rows]
    set_activities(sid, activity_list)
    return ActivitiesUploadResponse(
        activity_count=len(activity_list),
        activities=activity_list,
    )


@router.post("/research/run", response_model=RunResearchResponse)
async def run_research_endpoint(body: RunResearchRequest) -> RunResearchResponse:
    """Run research agent. Session must have trip and activities."""
    results, err = await run_research(body.session_id)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return RunResearchResponse(
        session_id=body.session_id,
        research_results=results,
        message="Research complete.",
    )


@router.get("/research/results")
async def get_research_results(session_id: str) -> dict[str, Any]:
    """Get research results for session."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    results = session.get(RESEARCH_RESULTS)
    return {"research_results": results or [], "session_id": session_id}


@router.get("/research/excel")
async def get_research_excel(session_id: str) -> Response:
    """Download research results as Excel."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    excel_bytes = session.get(EXCEL_BYTES)
    if not excel_bytes:
        raise HTTPException(status_code=404, detail="No Excel generated yet. Run research first.")
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=research_options.xlsx"},
    )


@router.post("/ratings")
async def submit_ratings(body: SubmitRatingsRequest) -> dict:
    """Submit user ratings for research rows."""
    sid = get_or_create_session(body.session_id)
    set_user_ratings(sid, body.ratings)
    return {"session_id": sid, "message": "Ratings saved."}


@router.post("/itinerary/run", response_model=RunItineraryResponse)
async def run_itinerary_endpoint(body: RunItineraryRequest) -> RunItineraryResponse:
    """Run itinerary agent. step_mode: full | itinerary_only. Optional feedback for itinerary_only."""
    if body.step_mode == "itinerary_only":
        sid = get_or_create_session(body.session_id)
        set_step_mode(sid, "itinerary_only")
        set_feedback(sid, body.feedback or "")
    plan, alternatives, err = await run_itinerary(
        body.session_id,
        step_mode=body.step_mode,
        feedback=body.feedback,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return RunItineraryResponse(
        session_id=body.session_id,
        itinerary=plan,
        alternatives=alternatives,
        message="Itinerary complete.",
    )


@router.get("/itinerary", response_model=GetItineraryResponse)
async def get_itinerary(session_id: str) -> GetItineraryResponse:
    """Get itinerary and alternatives for session. Returns nulls if session missing or no plan yet."""
    session = get_session(session_id)
    if not session:
        return GetItineraryResponse(itinerary=None, alternatives=None)
    return GetItineraryResponse(
        itinerary=session.get(ITINERARY_PLAN),
        alternatives=session.get(ITINERARY_ALTERNATIVES),
    )
