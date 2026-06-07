"""The wire format: plain dataclasses every sink consumes.

These are storage-agnostic. A sink maps them to rows / lines / spans however it
likes; nothing here knows about Postgres, JSON, or any domain (no `trip_id`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Status(str, Enum):
    """Lifecycle of a span. `running` is the opening record; the rest are terminal."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class Span:
    """One step in an operation tree.

    Emitted twice per span: once with status=running on open (so a crash leaves a
    detectable running-without-terminal row) and once with a terminal status on
    close. Both records share `span_id`.
    """

    operation_id: str
    span_id: str
    parent_span_id: Optional[str]
    name: str
    status: str  # one of Status
    started_at: float  # wall-clock epoch seconds
    ended_at: Optional[float] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    labels: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Event:
    """A point-in-time record attached to a span (a trace breadcrumb, an LLM call…).

    `kind` discriminates event types; `fields` is the structured payload; `blob`
    is the optional large payload (e.g. an LLM response) kept in its own slot so
    metadata scans never have to load it.
    """

    operation_id: Optional[str]
    span_id: Optional[str]
    ts: float  # wall-clock epoch seconds
    kind: str
    level: str = "info"
    message: Optional[str] = None
    fields: dict[str, Any] = field(default_factory=dict)
    blob: Optional[str] = None
    labels: dict[str, Any] = field(default_factory=dict)
