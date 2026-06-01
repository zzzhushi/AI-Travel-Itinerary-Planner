"""Shared infrastructure for all LLM agents."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol, runtime_checkable

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.genai import types


@runtime_checkable
class AgentBackend(Protocol):
    """Future provider abstraction — implement to swap Gemini for OpenAI/Anthropic."""

    async def ask(self, system_prompt: str, user_prompt: str) -> tuple[str, str]: ...


class LlmAgent:
    """Holds one Agent+Runner; each call gets an isolated session to avoid history bleed."""

    def __init__(
        self,
        name: str,
        instruction: str,
        tools: list = [],
        retry_attempts: int = 5,
        retry_exp_base: int = 7,
    ) -> None:
        if os.getenv("GOOGLE_API_KEY"):
            os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
        self._agent = Agent(
            name=name,
            model=Gemini(
                model="gemini-2.5-flash",
                retry_options=_retry_config(retry_attempts, retry_exp_base),
            ),
            instruction=instruction,
            tools=tools,
        )
        self._runner = InMemoryRunner(agent=self._agent)
        self._call_count = 0

    async def ask(self, prompt: str) -> tuple[str, str]:
        """Send a prompt; returns (text, error). Each call uses a fresh session.

        A unique session_id per call prevents conversation history from bleeding
        across unrelated queries when the agent is reused as a singleton.
        """
        session_id = f"session_{self._call_count}"
        self._call_count += 1
        try:
            response = await self._runner.run_debug(prompt, session_id=session_id)
            text = _extract_text(response)
            return (text, "") if text else ("", "No text in agent response.")
        except Exception as e:
            return "", f"Agent error: {e}"
        finally:
            # Singletons live for the process lifetime; delete each one-shot session
            # immediately so InMemoryRunner doesn't accumulate stale session objects.
            try:
                await self._runner.session_service.delete_session(
                    app_name=self._runner.app_name,
                    user_id="debug_user_id",
                    session_id=session_id,
                )
            except Exception:
                pass


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
