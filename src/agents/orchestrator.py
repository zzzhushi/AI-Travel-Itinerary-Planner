"""Orchestrator: runs Research or Itinerary agent based on step_mode and user message."""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import AgentTool
from google.genai import types

from .research_agent import create_research_agent
from .itinerary_agent import create_itinerary_agent


def get_retry_config() -> types.HttpRetryOptions:
    return types.HttpRetryOptions(
        attempts=5,
        exp_base=7,
        initial_delay=1,
        http_status_codes=[429, 500, 503, 504],
    )


ORCHESTRATOR_INSTRUCTION = """You are the itinerary workflow coordinator. You have access to two tools: ResearchAgent and ItineraryAgent.

Session state contains: trip_info (country, city, days, flight_info), activity_list (list of vague activities with preference), step_mode ("full" or "itinerary_only"), research_results (after research run), user_ratings (optional), user_feedback (optional).

When the user message is "RUN_RESEARCH":
- You MUST call ResearchAgent with the activity list and trip context. Pass a message that includes the trip_info and activity_list so the agent can research options for each activity. Do not do anything else after calling ResearchAgent.

When the user message is "RUN_ITINERARY" or "RUN_ITINERARY_ONLY":
- You MUST call ItineraryAgent. Pass the trip_info, research_results, user_ratings, and user_feedback (if any) in your message so the agent can build the hour-by-hour plan and take-it-easy alternative. Do not call ResearchAgent. Do not do anything else after calling ItineraryAgent.

Always call exactly one of the two agents based on the user message. Respond briefly to confirm which step was run."""


def create_orchestrator() -> Agent:
    research_agent = create_research_agent()
    itinerary_agent = create_itinerary_agent()
    return Agent(
        name="Orchestrator",
        model=Gemini(
            model="gemini-2.5-flash-lite",
            retry_options=get_retry_config(),
        ),
        instruction=ORCHESTRATOR_INSTRUCTION,
        tools=[AgentTool(research_agent), AgentTool(itinerary_agent)],
    )
