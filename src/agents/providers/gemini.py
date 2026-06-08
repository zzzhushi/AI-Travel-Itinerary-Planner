"""Gemini provider backed by Google ADK."""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import obslog
from src.agents.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Production provider backed by Google ADK + Gemini."""

    def __init__(
        self,
        agent_name: str,
        instruction: str,
        tools: Optional[list] = None,
        retry_attempts: int = 5,
        retry_exp_base: int = 7,
    ) -> None:
        from src.agents.base import _extract_text, _retry_config

        from google.adk.agents import Agent
        from google.adk.models.google_llm import Gemini
        from google.adk.runners import InMemoryRunner

        if os.getenv("GOOGLE_API_KEY"):
            os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

        self._agent = Agent(
            name=agent_name,
            model=Gemini(
                model="gemini-2.5-flash",
                retry_options=_retry_config(retry_attempts, retry_exp_base),
            ),
            instruction=instruction,
            tools=tools or [],
        )
        self._runner = InMemoryRunner(agent=self._agent)
        self._call_count = 0
        self._extract_text = _extract_text

    async def ask(self, prompt: str) -> tuple[str, str]:
        session_id = f"session_{self._call_count}"
        self._call_count += 1
        logger.info(
            "[%s] call #%d prompt:\n%s",
            self._agent.name,
            self._call_count,
            prompt,
        )
        t0 = time.monotonic()
        text, err = "", ""
        try:
            response = await self._runner.run_debug(prompt, session_id=session_id)
            text = self._extract_text(response)
            if not text:
                err = "No text in agent response."
            return (text, err)
        except Exception as e:
            err = f"Agent error: {e}"
            return "", err
        finally:
            obslog.event(
                "llm_call",
                agent_name=self._agent.name,
                model="gemini-2.5-flash",
                status="error" if err else "ok",
                latency_ms=round((time.monotonic() - t0) * 1000, 1),
                prompt_chars=len(prompt),
                response_chars=len(text),
                error=err or None,
                blob=text or None,
            )
            try:
                await self._runner.session_service.delete_session(
                    app_name=self._runner.app_name,
                    user_id="debug_user_id",
                    session_id=session_id,
                )
            except Exception:
                pass
