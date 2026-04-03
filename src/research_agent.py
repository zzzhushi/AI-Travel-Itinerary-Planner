"""Google ADK research agent: vague activities -> concrete options (JSON + Excel-ready rows)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.adk.tools import google_search
from google.genai import types


def _retry_config() -> types.HttpRetryOptions:
    return types.HttpRetryOptions(
        attempts=5,
        exp_base=7,
        initial_delay=1,
        http_status_codes=[429, 500, 503, 504],
    )


RESEARCH_INSTRUCTION = """You are a travel research assistant. The user may only provide a vague activity; city and country may be missing — infer geography from the activity text (e.g. "salt bread, seoul" implies Seoul, Korea) and use google_search to find 2-4 popular, real options per activity in the right area.

You MUST respond with a single JSON array only — no markdown, no code fences, no extra text. Each element must be an object with keys:
- "activity_query" (string): the vague activity this row answers
- "option_name" (string): place or experience name
- "address" (string): street or area address if known, else best effort
- "location" (string): neighborhood, district, or area
- "link" (string): URL if found, else ""

Search in the local language or English as needed. When city/country are unknown, infer location from the activity wording before searching."""


def _extract_json_array(text: str) -> list[Any] | None:
    if not text or not text.strip():
        return None
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        pass
    i = text.find("[")
    if i == -1:
        return None
    depth = 0
    for j, c in enumerate(text[i:], i):
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[i : j + 1])
                    return data if isinstance(data, list) else None
                except json.JSONDecodeError:
                    return None
    return None


def _normalize_rows(raw: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "activity_query": str(item.get("activity_query", "")),
                "option_name": str(item.get("option_name", "")),
                "address": str(item.get("address", "")),
                "location": str(item.get("location", "")),
                "link": str(item.get("link", "")),
                "user_rating": item.get("user_rating", ""),
            }
        )
    return out


def build_user_message(parsed: dict[str, Any]) -> str:
    country = parsed.get("country") or ""
    city = parsed.get("city") or ""
    acts = parsed.get("activities") or []
    return (
        f"Trip context — country: {country or '(unknown)'}, city: {city or '(unknown)'}. "
        f"Research these activities and return the JSON array as specified: {json.dumps(acts)}"
    )


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

    parsed_json = _extract_json_array(text)
    if not parsed_json:
        return [], "Could not parse JSON array from model response."
    rows = _normalize_rows(parsed_json)
    if not rows:
        return [], "Model returned no valid rows."
    return rows, ""
