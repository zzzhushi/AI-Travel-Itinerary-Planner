"""Request/response schemas for the itinerary API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TripUploadResponse(BaseModel):
    trip_id: str
    country: str
    city: str
    days: int


class ActivitiesUploadResponse(BaseModel):
    activity_count: int
    activities: list[dict[str, Any]]


class RunResearchRequest(BaseModel):
    session_id: str


class RunResearchResponse(BaseModel):
    session_id: str
    research_results: list[dict[str, Any]] | None = None
    message: str = "Research complete."


class SubmitRatingsRequest(BaseModel):
    session_id: str
    ratings: dict[str, int | float] = Field(default_factory=dict)


class RunItineraryRequest(BaseModel):
    session_id: str
    step_mode: str = Field("full", pattern="^(full|itinerary_only)$")
    feedback: str = ""


class RunItineraryResponse(BaseModel):
    session_id: str
    itinerary: list[dict[str, Any]] | None = None
    alternatives: list[dict[str, Any]] | None = None
    message: str = "Itinerary complete."


class GetItineraryResponse(BaseModel):
    itinerary: list[dict[str, Any]] | None = None
    alternatives: list[dict[str, Any]] | None = None
