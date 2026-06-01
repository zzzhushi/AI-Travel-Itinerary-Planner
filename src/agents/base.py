"""Shared infrastructure for all LLM agents."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from google.genai import types

from src.agents.providers import LLMProvider

logger = logging.getLogger(__name__)


class LlmAgent:
    """Thin base that holds an injected LLMProvider and delegates ask() to it."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def ask(self, prompt: str) -> tuple[str, str]:
        return await self._provider.ask(prompt)


def _retry_config(attempts: int = 5, exp_base: int = 7) -> types.HttpRetryOptions:
    return types.HttpRetryOptions(
        attempts=attempts,
        exp_base=exp_base,
        initial_delay=1,
        http_status_codes=[429, 500, 503, 504],
    )


def _extract_text(response: Any) -> str:
    """Extract text from an ADK run_debug response (list[Event] or fallback shapes)."""
    if isinstance(response, list):
        for event in reversed(response):
            if hasattr(event, "is_final_response") and event.is_final_response():
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            return part.text
        for event in reversed(response):
            if hasattr(event, "content") and event.content and hasattr(event.content, "parts"):
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        return part.text
    if isinstance(response, str):
        return response
    if hasattr(response, "content") and response.content:
        c = response.content
        if isinstance(c, str):
            return c
        if hasattr(c, "parts") and c.parts:
            for part in c.parts:
                if hasattr(part, "text") and part.text:
                    return part.text
    return ""


def _extract_json(text: str) -> list[dict] | None:
    """Extract a JSON array from agent response text, handling code fences."""
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


def _extract_json_dict(text: str) -> dict | None:
    """Extract a JSON object from agent response text, handling code fences."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    i = text.find("{")
    if i == -1:
        return None
    depth = 0
    for j, c in enumerate(text[i:], i):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[i : j + 1])
                    return data if isinstance(data, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
