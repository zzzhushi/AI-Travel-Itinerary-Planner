"""Abstract base class for LLM backends."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Abstract base class for LLM backends used by agents."""

    @abstractmethod
    async def ask(self, prompt: str) -> tuple[str, str]:
        """Send a prompt and return (response_text, error_message). error is '' on success."""
        ...
