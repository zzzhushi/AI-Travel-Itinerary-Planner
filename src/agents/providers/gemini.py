"""Gemini provider backed by Google ADK."""

from __future__ import annotations

import logging
import os
from typing import Optional

from src.agents.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

# Single source of truth for the model id: used both to build the ADK agent and
# to label the llm_call log events, so they can never drift apart.
_MODEL = "gemini-2.5-flash"


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

        self._model = _MODEL
        self._agent = Agent(
            name=agent_name,
            model=Gemini(
                model=_MODEL,
                retry_options=_retry_config(retry_attempts, retry_exp_base),
            ),
            instruction=instruction,
            tools=tools or [],
        )
        self._runner = InMemoryRunner(agent=self._agent)
        self._call_count = 0
        self._extract_text = _extract_text

    async def ask(self, prompt: str) -> tuple[str, str]:
        from src.agents.base import instrument_llm_call

        session_id = f"session_{self._call_count}"
        self._call_count += 1
        logger.info(
            "[%s] call #%d prompt:\n%s",
            self._agent.name,
            self._call_count,
            prompt,
        )
        # instrument_llm_call times the call and emits the llm_call event on exit;
        # we only record the outcome via call.ok()/call.failed().
        with instrument_llm_call(self._agent.name, self._model, prompt) as call:
            try:
                response = await self._runner.run_debug(prompt, session_id=session_id)
                text = self._extract_text(response)
                if not text:
                    return call.failed("No text in agent response.")
                return call.ok(text)
            except Exception as e:
                return call.failed(f"Agent error: {e}")
            finally:
                try:
                    await self._runner.session_service.delete_session(
                        app_name=self._runner.app_name,
                        user_id="debug_user_id",
                        session_id=session_id,
                    )
                except Exception:
                    pass
