"""Research agent: uses search to find 2-5 concrete options per vague activity."""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import google_search
from google.genai import types


def get_retry_config() -> types.HttpRetryOptions:
    return types.HttpRetryOptions(
        attempts=5,
        exp_base=7,
        initial_delay=1,
        http_status_codes=[429, 500, 503, 504],
    )


RESEARCH_INSTRUCTION = """You are a travel research agent. Given a trip context (country, city, number of days) and a list of vague activity descriptions from the user, your job is to use the google_search tool to find 2-5 concrete options for EACH activity (e.g. specific places, venues, or experiences).

For each option found, you must collect: option_name, address (or area), location (neighborhood or district), and an optional link (URL).

You MUST output your final response as a single valid JSON array of objects only, with no other text before or after. Each object must have these exact keys:
- "activity_query" (string): the original vague activity from the list)
- "option_name" (string): name of the place/experience
- "address" (string): full address or area
- "location" (string): neighborhood, district, or area name
- "link" (string): URL if available, or empty string

Example format:
[{"activity_query": "night market Seoul", "option_name": "Gwangjang Market", "address": "88 Changgyeonggung-ro", "location": "Jongno-gu", "link": "https://..."}, ...]

Use the trip context (city, country) to disambiguate and target search. Your entire response must be only the JSON array, with no other text, no markdown, and no code block wrapper."""


def create_research_agent() -> Agent:
    return Agent(
        name="ResearchAgent",
        model=Gemini(
            model="gemini-2.5-flash-lite",
            retry_options=get_retry_config(),
        ),
        instruction=RESEARCH_INSTRUCTION,
        tools=[google_search],
        output_key="research_results",
    )
