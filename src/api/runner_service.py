"""Run orchestrator agent and parse research/itinerary from response."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .session_store import (
    get_session,
    _extract_json_from_text,
    RESEARCH_RESULTS,
    ITINERARY_PLAN,
    ITINERARY_ALTERNATIVES,
    set_research_results,
    set_itinerary,
    set_excel,
)
from ..tools.excel_export import research_results_to_excel


def _ensure_env() -> None:
    if os.getenv("GOOGLE_API_KEY"):
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")


async def run_research(session_id: str) -> tuple[list[dict[str, Any]] | None, str]:
    """
    Run research agent for the given session. Session must have trip_info and activity_list.
    Returns (research_results, error_message). If error_message is non-empty, results may be None.
    """
    _ensure_env()
    session = get_session(session_id)
    if not session:
        return None, "Session not found."
    trip_info = session.get("trip_info")
    activity_list = session.get("activity_list")
    if not trip_info or not activity_list:
        return None, "Upload trip and activities first."

    try:
        from google.adk.agents import Agent
        from google.adk.models.google_llm import Gemini
        from google.adk.runners import InMemoryRunner
        from google.adk.tools import AgentTool, google_search
        from google.genai import types
        from ..agents.research_agent import create_research_agent
    except ImportError as e:
        return None, f"Agent dependencies not available: {e}"

    research_agent = create_research_agent()
    runner = InMemoryRunner(agent=research_agent)

    context = {
        "trip_info": trip_info,
        "activity_list": activity_list,
    }
    message = (
        f"Research 2-5 concrete options for each of these activities in {trip_info.get('city', '')}, {trip_info.get('country', '')}. "
        f"Activities: {json.dumps(activity_list)}. "
        "Return only a valid JSON array of objects with keys: activity_query, option_name, address, location, link."
    )

    try:
        response = await runner.run_debug(message)
    except Exception as e:
        return None, f"Agent run failed: {e}"

    # response may be RunResponse with .content or .events; try to get final text
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

    parsed = _extract_json_from_text(text)
    if parsed is None:
        return None, "Could not parse research results from agent response."
    if not isinstance(parsed, list):
        return None, "Research results must be a JSON array."

    # Normalize to list of dicts with expected keys
    results = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        results.append({
            "activity_query": item.get("activity_query", ""),
            "option_name": item.get("option_name", ""),
            "address": item.get("address", ""),
            "location": item.get("location", ""),
            "link": item.get("link", ""),
            "user_rating": item.get("user_rating", ""),
        })
    set_research_results(session_id, results)
    try:
        excel_bytes = research_results_to_excel(results)
        set_excel(session_id, excel_bytes)
    except Exception:
        pass
    return results, ""


async def run_itinerary(
    session_id: str,
    step_mode: str = "full",
    feedback: str = "",
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None, str]:
    """
    Run itinerary agent. Returns (plan, alternatives, error_message).
    Session must have trip_info and research_results (and optionally user_ratings).
    """
    _ensure_env()
    session = get_session(session_id)
    if not session:
        return None, None, "Session not found."
    trip_info = session.get("trip_info")
    research_results = session.get("research_results") or []
    user_ratings = session.get("user_ratings") or {}

    if not trip_info:
        return None, None, "Upload trip info first."
    if step_mode == "full" and not research_results:
        return None, None, "Run research first to get activity options."

    try:
        from google.adk.runners import InMemoryRunner
        from ..agents.itinerary_agent import create_itinerary_agent
    except ImportError as e:
        return None, None, f"Agent dependencies not available: {e}"

    itinerary_agent = create_itinerary_agent()
    runner = InMemoryRunner(agent=itinerary_agent)

    message = (
        f"Build an hour-by-hour itinerary and a 'take it easy' alternative. "
        f"Trip: {json.dumps(trip_info)}. "
        f"Activities with options: {json.dumps(research_results)}. "
        f"User ratings (row/option -> 1-5): {json.dumps(user_ratings)}. "
    )
    if feedback:
        message += f"User feedback: {feedback}. "
    message += (
        "Output only a JSON object with keys 'plan' and 'alternatives'. "
        "plan: array of days, each with day, date_label, slots (start, end, activity, location, travel_buffer_mins, is_meal, is_rest). "
        "alternatives: same structure but fewer activities and more rest."
    )

    try:
        response = await runner.run_debug(message)
    except Exception as e:
        return None, None, f"Agent run failed: {e}"

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

    parsed = _extract_json_from_text(text)
    if parsed is None or not isinstance(parsed, dict):
        return None, None, "Could not parse itinerary from agent response."

    plan = parsed.get("plan")
    alternatives = parsed.get("alternatives")
    if not isinstance(plan, list):
        plan = []
    if not isinstance(alternatives, list):
        alternatives = []
    set_itinerary(session_id, plan, alternatives)
    return plan, alternatives, ""
