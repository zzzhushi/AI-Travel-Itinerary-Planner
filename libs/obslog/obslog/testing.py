"""Test helper: a sink that captures records in memory for assertions."""

from __future__ import annotations

from .records import Event, Span
from .sink import Sink


class InMemorySink(Sink):
    """Collect emitted records so tests can assert on them. Inject via set_sink()."""

    def __init__(self) -> None:
        self.spans: list[Span] = []
        self.events: list[Event] = []

    def emit_span(self, span: Span) -> None:
        self.spans.append(span)

    def emit_event(self, event: Event) -> None:
        self.events.append(event)

    def clear(self) -> None:
        self.spans.clear()
        self.events.clear()

    # --- convenience queries ---
    def spans_named(self, name: str) -> list[Span]:
        return [s for s in self.spans if s.name == name]

    def terminal_spans(self) -> list[Span]:
        """Spans in a terminal state (the closing record of each span)."""
        return [s for s in self.spans if s.status != "running"]

    def running_without_terminal(self) -> list[Span]:
        """Span ids opened (running) but never closed — i.e. crashed mid-flight."""
        terminal_ids = {s.span_id for s in self.terminal_spans()}
        return [s for s in self.spans if s.status == "running" and s.span_id not in terminal_ids]
