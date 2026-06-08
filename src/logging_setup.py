"""App-side policy for obslog (the obslog core is domain-agnostic).

This is where *this* app decides what to log to Postgres: it indexes `trip_id`
and registers a typed `llm_calls` table for `kind="llm_call"` events. The obslog
library knows none of that — it only provides the mechanisms (labels,
typed-event-tables, sinks).

Activation is opt-in via the `OBSLOG_SINK` env var so unit tests and
`--dry-run` stay on the default NullSink (no DB needed):

    OBSLOG_SINK=postgres   → install the PostgresSink at startup
    (anything else/unset)  → no-op, default NullSink

Call `install_obslog_sink()` once at process startup and `shutdown_obslog()` on
exit so the background writer flushes.
"""

from __future__ import annotations

import os
from typing import Optional

from obslog import configure, set_sink

# llm_calls schema (promoted columns pulled from the llm_call event's fields).
# Kept here, not in the library, because these column names are app policy.
_LLM_CALL_KIND = "llm_call"
_LLM_CALL_TABLE = "llm_calls"

_sink = None  # the installed PostgresSink, for shutdown()


def _llm_calls_typed_table():
    from sqlalchemy import Float, Text

    from obslog.sinks import TypedEventTable

    return TypedEventTable(
        kind=_LLM_CALL_KIND,
        table=_LLM_CALL_TABLE,
        columns=[
            ("agent_name", Text),
            ("model", Text),
            ("prompt_version", Text),
            ("call_type", Text),
            ("status", Text),
            ("latency_ms", Float),
            ("error", Text),
        ],
        blob_column="response",  # full (scrubbed) LLM output
    )


def indexed_labels() -> list[str]:
    """Labels this app filters logs by — the sink builds expression indexes for these."""
    return ["trip_id"]


def install_obslog_sink() -> Optional[object]:
    """Install the PostgresSink if OBSLOG_SINK=postgres and DATABASE_URL is set.

    Returns the sink (or None if not installed). Idempotent-ish: a second call
    replaces the sink. Schema is owned by Alembic, so create_tables stays False.
    """
    global _sink
    if os.getenv("OBSLOG_SINK", "").strip().lower() != "postgres":
        return None
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        return None

    from obslog.sinks import PostgresSink

    configure(service="itinerary")
    _sink = PostgresSink(
        dsn,
        retention=os.getenv("OBSLOG_RETENTION", "3d"),
        indexed_labels=indexed_labels(),
        typed_tables=[_llm_calls_typed_table()],
    )
    set_sink(_sink)
    return _sink


def shutdown_obslog() -> None:
    """Flush + close the installed sink (so the background writer drains)."""
    global _sink
    if _sink is not None:
        _sink.close()
        _sink = None
