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

    IDs are UUID4 strings (dashed format). Required fields have no default;
    optional diagnostic fields default to None and are stamped by processors or
    the caller — leave them None if not applicable.
    """

    # --- identity (required) ---
    operation_id: str           # UUID4; shared by all spans in one operation tree
    span_id: str                # UUID4; unique per span
    parent_span_id: Optional[str]   # None on the root
    name: str
    status: str                 # one of Status
    started_at: float           # wall-clock epoch seconds

    # --- timing (filled on close) ---
    ended_at: Optional[float] = None
    duration_ms: Optional[float] = None

    # --- outcome ---
    error: Optional[str] = None

    # --- correlation / filtering (indexed in Postgres) ---
    labels: dict[str, Any] = field(default_factory=dict)

    # --- span-specific detail (not indexed; read when drilling in) ---
    attributes: dict[str, Any] = field(default_factory=dict)

    # --- deployment context (set once at startup via a processor) ---
    version: Optional[str] = None           # semver tag or git SHA ("v1.4.2", "abc1234")
    environment: Optional[str] = None       # "prod" / "staging" / "dev"
    host: Optional[str] = None              # hostname, container name, or pod name
    region: Optional[str] = None            # cloud/geo region ("us-east-1", "local")

    # --- per-request identity ---
    user_id: Optional[str] = None           # who triggered this operation
    session_id: Optional[str] = None        # user's browser/CLI session
    client_platform: Optional[Any] = None   # str ("cli"/"web") or dict {"browser": "Chrome", ...}

    # --- reliability ---
    retry_count: Optional[int] = None       # attempts before this outcome
    http_status_code: Optional[int] = None  # HTTP response code for web-triggered ops

    # --- feature control ---
    feature_flags: Optional[dict[str, Any]] = None  # active flags at time of operation

    # --- cross-system correlation ---
    correlation_id: Optional[str] = None      # external system's ID (Stripe, Places, etc.)
    parent_request_id: Optional[str] = None   # upstream service's request ID (first step toward multi-service tracing)
    idempotency_key: Optional[str] = None     # key used for retryable operations; de-duplicate in the DB


@dataclass
class LogRecord:
    """A point-in-time record attached to a span (a trace breadcrumb, an LLM call…).

    `kind` discriminates record types; `fields` is the structured payload; `blob`
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
