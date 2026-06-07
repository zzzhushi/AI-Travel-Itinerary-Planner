"""The storage interface + the process-wide registry.

A `Sink` receives finished records and does whatever it wants with them (write a
row, a JSON line, ship to an exporter). The tracer never talks to a concrete
backend — it calls the module-level `emit_*` here, which applies processors and
dispatches to the currently-installed sink. Swapping backends is `set_sink(...)`.
"""

from __future__ import annotations

import abc
import threading
from typing import Callable, Optional, Union

from .records import Event, Span

Record = Union[Span, Event]
Processor = Callable[[Record], Optional[Record]]


class Sink(abc.ABC):
    """Implement this to send records anywhere. emit_* must be cheap + thread-safe."""

    @abc.abstractmethod
    def emit_span(self, span: Span) -> None: ...

    @abc.abstractmethod
    def emit_event(self, event: Event) -> None: ...

    def flush(self) -> None:
        """Block until buffered records are persisted. No-op by default."""

    def close(self) -> None:
        """Flush and release resources. No-op by default."""

    def cleanup(self, retention: Optional[str] = None) -> None:
        """Delete records older than the retention window. No-op by default."""


class NullSink(Sink):
    """The default: drops everything. Keeps obslog a safe no-op until configured."""

    def emit_span(self, span: Span) -> None:  # noqa: D102
        pass

    def emit_event(self, event: Event) -> None:  # noqa: D102
        pass


_lock = threading.Lock()
_sink: Sink = NullSink()
_processors: list[Processor] = []
_service: Optional[str] = None


def set_sink(sink: Sink) -> None:
    global _sink
    with _lock:
        _sink = sink


def get_sink() -> Sink:
    return _sink


def configure(*, service: Optional[str] = None, processors: Optional[list[Processor]] = None) -> None:
    """Global setup. `service` is stamped as a label; `processors` are `(record)->record`
    hooks applied to every record before it reaches the sink (e.g. add host/env/git sha).
    A processor that returns None drops the record."""
    global _service, _processors
    with _lock:
        if service is not None:
            _service = service
        if processors is not None:
            _processors = list(processors)


def reset() -> None:
    """Restore registry defaults. Primarily for tests."""
    global _sink, _processors, _service
    with _lock:
        _sink = NullSink()
        _processors = []
        _service = None


def _enrich(record: Record) -> Optional[Record]:
    """Apply service label + processors. A processor returning None DROPS the
    record (the structlog convention), so `_enrich` may return None."""
    if _service is not None:
        record.labels.setdefault("service", _service)
    for proc in _processors:
        out = proc(record)
        if out is None:
            return None
        record = out
    return record


def emit_span(span: Span) -> None:
    record = _enrich(span)
    if record is not None:
        get_sink().emit_span(record)


def emit_event(event: Event) -> None:
    record = _enrich(event)
    if record is not None:
        get_sink().emit_event(record)
