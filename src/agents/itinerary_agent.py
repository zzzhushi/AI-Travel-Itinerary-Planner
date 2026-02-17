"""Itinerary agent: builds hour-by-hour plan and take-it-easy alternative."""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import FunctionTool
from google.genai import types

from ..tools.travel_time import estimate_travel_time


def get_retry_config() -> types.HttpRetryOptions:
    return types.HttpRetryOptions(
        attempts=5,
        exp_base=7,
        initial_delay=1,
        http_status_codes=[429, 500, 503, 504],
    )


def _travel_time_tool(from_address: str, to_address: str, mode: str = "transit") -> int:
    """Estimate travel time in minutes between two locations. Mode: walking, driving, bus, train, transit."""
    return estimate_travel_time(from_address, to_address, mode=mode or "transit")


ITINERARY_INSTRUCTION = """You are an itinerary planner. You are given:
1. Trip info: country, city, number of days, optional flight info.
2. Research results: a list of activities with option_name, address, location, link, and optional user_rating (1-5).
3. Optional user feedback (when re-running itinerary only).

Your job is to produce TWO plans in your final response:
A) Full hour-by-hour plan: for each day, list time slots with activity name, recommended start/end time, travel buffer (use the travel_time tool when moving between locations), and mark meals/rest where appropriate.
B) "Take it easy" alternative: fewer activities per day, longer rest and travel buffers, same structure.

You MUST output your final response as a single valid JSON object only, with no other text before or after. Use these exact keys:
- "plan": array of days; each day is an object with "day": number, "date_label": optional string, "slots": array of {"start": "HH:MM", "end": "HH:MM", "activity": string, "location": string, "travel_buffer_mins": number, "is_meal": boolean, "is_rest": boolean}
- "alternatives": same structure as "plan", but with fewer activities and more rest/buffers

Prioritize activities with higher user_rating when present. Respect travel time between locations. Your entire response must be only the JSON object, with no other text, no markdown, and no code block wrapper."""


def create_itinerary_agent() -> Agent:
    travel_tool = FunctionTool(_travel_time_tool)
    return Agent(
        name="ItineraryAgent",
        model=Gemini(
            model="gemini-2.5-flash-lite",
            retry_options=get_retry_config(),
        ),
        instruction=ITINERARY_INSTRUCTION,
        tools=[travel_tool],
        output_key="itinerary_output",
    )
