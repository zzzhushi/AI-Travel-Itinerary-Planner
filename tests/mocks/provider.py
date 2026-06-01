"""MockProvider for unit tests — returns preset responses without any network calls."""

from src.agents.provider import LLMProvider


class MockProvider(LLMProvider):
    """Returns responses from a pre-loaded queue in FIFO order.

    Initialize with a list of response strings. Each call to ask() pops
    the first response. When the queue is empty, returns an error tuple.
    """

    def __init__(self, responses: list[str]) -> None:
        self._queue = list(responses)

    async def ask(self, prompt: str) -> tuple[str, str]:
        if not self._queue:
            return "", "MockProvider: no more responses"
        return self._queue.pop(0), ""
