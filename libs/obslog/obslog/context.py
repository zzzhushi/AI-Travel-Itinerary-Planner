"""Ambient correlation state, built on `contextvars`.

Why contextvars: each asyncio Task runs in its own *copy* of the context, and
each thread has its own context. So the "current span" is automatically isolated
between concurrent operations — a `.set()` in one task/thread is invisible to
others — without threading ids through function signatures. This is the
async-native successor to thread-locals (PEP 567), and what makes a `trace()`
call deep in the stack attach to the right operation.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
from typing import Any, Callable, Iterator, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # avoid an import cycle with tracer at runtime
    from .tracer import SpanHandle

# The innermost active span. The chain of reset tokens is the implicit stack:
# open -> set() (saving a token); close -> reset(token) restores the parent.
_current_span: contextvars.ContextVar[Optional["SpanHandle"]] = contextvars.ContextVar(
    "obslog_current_span", default=None
)

# Labels attached to a scope via bind_labels(); merged into spans opened within.
# Default is None (not a shared mutable {}) so no caller can accidentally mutate
# a single dict shared across every context; get_ambient_labels() normalizes it.
_ambient_labels: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    "obslog_ambient_labels", default=None
)


def current_span() -> Optional["SpanHandle"]:
    return _current_span.get()


def set_current(handle: "SpanHandle") -> contextvars.Token:
    return _current_span.set(handle)


def reset_current(token: contextvars.Token) -> None:
    _current_span.reset(token)


def get_ambient_labels() -> dict[str, Any]:
    return _ambient_labels.get() or {}


@contextlib.contextmanager
def bind_labels(**labels: Any) -> Iterator[None]:
    """Attach labels to the ambient context for the duration of the block.

    Every span opened inside inherits them, so a web request / CLI run can set
    `request_id` (or `trip_id`) once instead of passing it to every call. Usable
    in async code too — it's synchronous and holds across `await`.
    """
    merged = {**(_ambient_labels.get() or {}), **labels}
    token = _ambient_labels.set(merged)
    try:
        yield
    finally:
        _ambient_labels.reset(token)


def bind_context(fn: Callable) -> Callable:
    """Capture the current correlation context so `fn` runs inside it elsewhere.

    `loop.run_in_executor` and raw threads do NOT propagate contextvars, so a
    `trace()` inside such a worker would be an (uncorrelated) no-op. Wrap the
    worker callable with this to carry the current operation/span into it:

        await loop.run_in_executor(None, bind_context(do_work), arg)
    """
    ctx = contextvars.copy_context()

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return ctx.run(fn, *args, **kwargs)

    return wrapper
