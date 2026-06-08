"""obslog — structured span/event logging with ambient correlation.

Quick start:

    from obslog import operation, span, trace, set_sink
    from obslog.sinks import StdoutSink

    set_sink(StdoutSink())
    async with operation("do_thing", labels={"user_id": 7}) as op:
        trace("started", n=3)
        async with span("substep") as s:
            ...
            s.fail("nope")        # child failed; the operation is unaffected
        op.set(result="ok")

Correlation is ambient (contextvars), so nested code never threads ids through
signatures, and every emitter is no-op-safe when no span is active.
"""

from .context import bind_context, bind_labels, current_span
from .records import LogRecord, Span, Status
from .sink import NullSink, Sink, configure, get_sink, require_labels, set_sink
from .tracer import event, new_operation_id, operation, span, trace

__all__ = [
    # tracing API
    "operation",
    "span",
    "trace",
    "event",
    "bind_labels",
    "bind_context",
    "current_span",
    "new_operation_id",
    # configuration
    "configure",
    "set_sink",
    "get_sink",
    "require_labels",
    # sinks / records (concrete sinks live in obslog.sinks; InMemorySink in obslog.testing)
    "Sink",
    "NullSink",
    "Span",
    "LogRecord",
    "Status",
]
