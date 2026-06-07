"""Spans, the context managers that open them, and the trace/event emitters.

`operation()` opens a root span (new operation_id); `span()` opens a child of
whatever span is currently active. Both work as `with` and `async with`. All
emitters are no-op-safe: calling `trace()` with no active span does nothing.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from . import context as _ctx
from . import sink as _sink
from .records import Event, Span, Status


def new_operation_id() -> str:
    return uuid.uuid4().hex


def _new_span_id() -> str:
    return uuid.uuid4().hex


class SpanHandle:
    """The live state of an open span (also what lives in the contextvar).

    Holds only what propagation and the terminal record need; finished records
    are emitted to the sink, not retained.
    """

    def __init__(
        self,
        name: str,
        *,
        operation_id: str,
        span_id: str,
        parent_span_id: Optional[str],
        labels: dict[str, Any],
        attributes: dict[str, Any],
    ) -> None:
        self.name = name
        self.operation_id = operation_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.labels = labels
        self.attributes = attributes
        self.status: str = Status.RUNNING.value
        self.error: Optional[str] = None
        self._started_at: Optional[float] = None
        self._start_monotonic: Optional[float] = None
        self._token = None
        self._finished = False

    # --- mutation API (chainable) ---
    def set(self, **attrs: Any) -> "SpanHandle":
        """Attach attributes to this span (e.g. counts, source)."""
        self.attributes.update(attrs)
        return self

    def fail(self, error: Any = None) -> "SpanHandle":
        """Mark this span failed without raising — the parent is unaffected."""
        self.status = Status.FAILED.value
        if error is not None:
            self.error = str(error)
        return self

    def trace(self, message: str, **fields: Any) -> None:
        """A debug breadcrumb tied to *this* span (regardless of what's current)."""
        self.event("trace", level="debug", message=message, **fields)

    def event(
        self,
        kind: str,
        *,
        level: str = "info",
        message: Optional[str] = None,
        blob: Optional[str] = None,
        **fields: Any,
    ) -> None:
        """Emit an arbitrary typed event tied to this span."""
        _sink.emit_event(
            Event(
                operation_id=self.operation_id,
                span_id=self.span_id,
                ts=time.time(),
                kind=kind,
                level=level,
                message=message,
                fields=dict(fields),
                blob=blob,
                labels=dict(self.labels),
            )
        )

    # --- lifecycle (called by the context manager) ---
    def _start(self) -> None:
        self._started_at = time.time()
        self._start_monotonic = time.monotonic()
        self._token = _ctx.set_current(self)
        _sink.emit_span(self._record())  # status=running

    def _finish(self, exc: Optional[BaseException]) -> None:
        if self._finished:
            return
        self._finished = True
        if exc is not None:
            self.status = Status.FAILED.value
            if self.error is None:
                self.error = f"{type(exc).__name__}: {exc}"
        elif self.status == Status.RUNNING.value:
            self.status = Status.SUCCESS.value
        ended_at = time.time()
        duration_ms = (time.monotonic() - (self._start_monotonic or 0.0)) * 1000.0
        _sink.emit_span(self._record(ended_at=ended_at, duration_ms=duration_ms))
        if self._token is not None:
            _ctx.reset_current(self._token)

    def _record(self, *, ended_at: Optional[float] = None, duration_ms: Optional[float] = None) -> Span:
        return Span(
            operation_id=self.operation_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self.name,
            status=self.status,
            started_at=self._started_at or time.time(),
            ended_at=ended_at,
            duration_ms=duration_ms,
            error=self.error,
            labels=dict(self.labels),
            attributes=dict(self.attributes),
        )


class _SpanCM:
    """A span usable as either `with` or `async with` — nothing here awaits."""

    def __init__(self, handle: SpanHandle) -> None:
        self._handle = handle

    def __enter__(self) -> SpanHandle:
        self._handle._start()
        return self._handle

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._handle._finish(exc)
        return False  # never suppress

    async def __aenter__(self) -> SpanHandle:
        self._handle._start()
        return self._handle

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._handle._finish(exc)
        return False


def operation(name: str, *, labels: Optional[dict[str, Any]] = None, **attrs: Any) -> _SpanCM:
    """Open a ROOT span (new operation_id). Inherits ambient labels (bind_labels)."""
    handle = SpanHandle(
        name,
        operation_id=new_operation_id(),
        span_id=_new_span_id(),
        parent_span_id=None,
        labels={**_ctx.get_ambient_labels(), **(labels or {})},
        attributes=dict(attrs),
    )
    return _SpanCM(handle)


def span(name: str, *, labels: Optional[dict[str, Any]] = None, **attrs: Any) -> _SpanCM:
    """Open a CHILD of the current span (or a root if none active).

    A child inherits the parent's operation_id and labels (so trip_id propagates).
    """
    parent = _ctx.current_span()
    if parent is not None:
        operation_id = parent.operation_id
        parent_span_id: Optional[str] = parent.span_id
        base_labels = dict(parent.labels)
    else:
        operation_id = new_operation_id()
        parent_span_id = None
        base_labels = dict(_ctx.get_ambient_labels())
    handle = SpanHandle(
        name,
        operation_id=operation_id,
        span_id=_new_span_id(),
        parent_span_id=parent_span_id,
        labels={**base_labels, **(labels or {})},
        attributes=dict(attrs),
    )
    return _SpanCM(handle)


def event(
    kind: str,
    *,
    level: str = "info",
    message: Optional[str] = None,
    blob: Optional[str] = None,
    **fields: Any,
) -> None:
    """Emit a typed event on the current span. No-op if no span is active."""
    cur = _ctx.current_span()
    if cur is None:
        return
    cur.event(kind, level=level, message=message, blob=blob, **fields)


def trace(message: str, **fields: Any) -> None:
    """Emit a `kind="trace"` debug breadcrumb on the current span. No-op if none active."""
    event("trace", level="debug", message=message, **fields)
