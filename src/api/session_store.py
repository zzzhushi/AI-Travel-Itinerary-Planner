"""In-memory session store for trip, activities, research, ratings, itinerary."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

# Session data keys
TRIP_INFO = "trip_info"
ACTIVITY_LIST = "activity_list"
RESEARCH_RESULTS = "research_results"
USER_RATINGS = "user_ratings"
ITINERARY_PLAN = "itinerary_plan"
ITINERARY_ALTERNATIVES = "itinerary_alternatives"
EXCEL_BYTES = "excel_bytes"
STEP_MODE = "step_mode"
USER_FEEDBACK = "user_feedback"

# In-memory store: session_id -> dict
_sessions: dict[str, dict[str, Any]] = {}


def create_session_id() -> str:
    return str(uuid.uuid4())


def get_session(session_id: str) -> dict[str, Any] | None:
    return _sessions.get(session_id)


def get_or_create_session(session_id: str | None) -> str:
    if session_id and session_id in _sessions:
        return session_id
    sid = session_id or create_session_id()
    if sid not in _sessions:
        _sessions[sid] = {}
    return sid


def set_trip(session_id: str, trip_info: dict[str, Any]) -> None:
    _sessions.setdefault(session_id, {})[TRIP_INFO] = trip_info


def set_activities(session_id: str, activity_list: list[dict[str, Any]]) -> None:
    _sessions.setdefault(session_id, {})[ACTIVITY_LIST] = activity_list


def set_research_results(session_id: str, results: list[dict[str, Any]]) -> None:
    _sessions.setdefault(session_id, {})[RESEARCH_RESULTS] = results


def set_user_ratings(session_id: str, ratings: dict[str, int | float]) -> None:
    _sessions.setdefault(session_id, {})[USER_RATINGS] = ratings


def set_itinerary(
    session_id: str,
    plan: list[dict[str, Any]],
    alternatives: list[dict[str, Any]] | None = None,
) -> None:
    s = _sessions.setdefault(session_id, {})
    s[ITINERARY_PLAN] = plan
    s[ITINERARY_ALTERNATIVES] = alternatives or []


def set_excel(session_id: str, excel_bytes: bytes) -> None:
    _sessions.setdefault(session_id, {})[EXCEL_BYTES] = excel_bytes


def set_feedback(session_id: str, feedback: str) -> None:
    _sessions.setdefault(session_id, {})[USER_FEEDBACK] = feedback


def set_step_mode(session_id: str, mode: str) -> None:
    _sessions.setdefault(session_id, {})[STEP_MODE] = mode


def _extract_json_from_text(text: str) -> Any | None:
    """Try to extract a JSON object or array from agent response (may be wrapped in markdown)."""
    if not text or not text.strip():
        return None
    text = text.strip()
    # Strip markdown code block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    # Try parse as-is
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first [ or { and last ] or }
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        i = text.find(start_char)
        if i == -1:
            continue
        depth = 0
        j = i
        for k, c in enumerate(text[i:], i):
            if c == start_char:
                depth += 1
            elif c == end_char:
                depth -= 1
                if depth == 0:
                    j = k
                    break
        if depth == 0:
            try:
                return json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                pass
    return None
