"""PostgresSink — a non-blocking, batched, self-retaining database backend.

Design goals (see the module README):

- **Never blocks the caller.** `emit_*` only enqueues onto a `queue.Queue`; a
  daemon thread drains it and batch-inserts on its *own* engine/connection. So
  logging is safe from an asyncio event loop and from sync code alike, and a log
  write is never part of the app's transaction — a rolled-back app operation
  still leaves its logs (the whole point, for debugging the failure).
- **Storage-agnostic core stays clean.** All SQLAlchemy/Postgres specifics live
  here, imported lazily so importing this module costs nothing until a sink is
  built. The DB work is behind a tiny `_Backend` seam so the threading/batching/
  retention logic is unit-testable with a fake (no DB required).
- **Typed event tables.** Events whose `kind` matches a registered
  `TypedEventTable` are routed to a dedicated table with promoted columns (this
  is how `llm_call` events land in `llm_calls`); everything else goes to
  `log_records`. The library knows nothing about LLMs — the app supplies the policy.
- **Retention is a library feature.** The worker deletes rows older than the
  window on startup and periodically; `cleanup()` triggers it on demand.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..records import LogRecord, Span
from ..sink import Sink

# ---------------------------------------------------------------------------
# Retention parsing
# ---------------------------------------------------------------------------

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_retention(value: Optional[str | timedelta]) -> Optional[timedelta]:
    """Parse "3d" / "12h" / "30m" / "1w" (or a timedelta / None) into a window.

    None or "" means "no retention" (keep everything)."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value
    s = str(value).strip().lower()
    if not s:
        return None
    unit = s[-1]
    if unit not in _UNIT_SECONDS:
        raise ValueError(f"bad retention {value!r}: expected a suffix in s/m/h/d/w")
    return timedelta(seconds=int(s[:-1]) * _UNIT_SECONDS[unit])


# ---------------------------------------------------------------------------
# Typed event tables (the kind -> dedicated-table registry)
# ---------------------------------------------------------------------------


@dataclass
class TypedEventTable:
    """Routes events of one `kind` to a dedicated table with promoted columns.

    `columns` are `(name, sqlalchemy_type)` pulled from `event.fields`; `blob_column`
    (if set) receives `event.blob`. The app builds these — that is where SQLAlchemy
    types and LLM-specific column names live, never in the obslog core.
    """

    kind: str
    table: str
    columns: list[tuple[str, Any]] = field(default_factory=list)
    blob_column: Optional[str] = None


# ---------------------------------------------------------------------------
# Pure record -> row mappers (no DB, unit-testable)
# ---------------------------------------------------------------------------


def _epoch_to_dt(epoch: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(epoch, tz=timezone.utc) if epoch is not None else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _promote(labels: dict[str, Any], keys: Optional[list[str]]) -> dict[str, Any]:
    """Pop promoted label keys out of `labels` (mutating it) into top-level columns.

    A promoted label becomes a real, indexed column, so it is *removed* from the
    JSONB `labels` blob to avoid storing it twice (the app asked for it as a
    column precisely because it filters on it often). Keys that are absent still
    get a column, set to None — the column is always present in the row."""
    if not keys:
        return {}
    return {k: labels.pop(k, None) for k in keys}


def span_row(span: Span, promoted_labels: Optional[list[str]] = None) -> dict[str, Any]:
    labels = dict(span.labels)
    promoted = _promote(labels, promoted_labels)
    return {
        "created_at": _now(),
        "operation_id": span.operation_id,
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "status": span.status,
        "started_at": _epoch_to_dt(span.started_at),
        "ended_at": _epoch_to_dt(span.ended_at),
        "duration_ms": span.duration_ms,
        "error": span.error,
        "labels": labels,
        "attributes": dict(span.attributes),
        # diagnostic fields (None when not set)
        "version": span.version,
        "environment": span.environment,
        "host": span.host,
        "region": span.region,
        "user_id": span.user_id,
        "session_id": span.session_id,
        "client_platform": span.client_platform,
        "retry_count": span.retry_count,
        "http_status_code": span.http_status_code,
        "feature_flags": span.feature_flags,
        "correlation_id": span.correlation_id,
        "parent_request_id": span.parent_request_id,
        "idempotency_key": span.idempotency_key,
        **promoted,
    }


def log_record_row(record: LogRecord, promoted_labels: Optional[list[str]] = None) -> dict[str, Any]:
    labels = dict(record.labels)
    promoted = _promote(labels, promoted_labels)
    return {
        "created_at": _now(),
        "ts": _epoch_to_dt(record.ts),
        "operation_id": record.operation_id,
        "span_id": record.span_id,
        "kind": record.kind,
        "level": record.level,
        "message": record.message,
        "fields": dict(record.fields),
        "blob": record.blob,
        "labels": labels,
        **promoted,
    }


def typed_log_record_row(
    record: LogRecord, typed: TypedEventTable, promoted_labels: Optional[list[str]] = None
) -> dict[str, Any]:
    labels = dict(record.labels)
    promoted = _promote(labels, promoted_labels)
    row: dict[str, Any] = {
        "created_at": _now(),
        "operation_id": record.operation_id,
        "span_id": record.span_id,
        "labels": labels,
        **promoted,
    }
    for name, _type in typed.columns:
        row[name] = record.fields.get(name)
    if typed.blob_column is not None:
        row[typed.blob_column] = record.blob
    return row


def route_log_record(
    record: LogRecord,
    typed_tables: dict[str, TypedEventTable],
    promoted_labels: Optional[list[str]] = None,
) -> tuple[str, dict[str, Any]]:
    """Return (table_name, row) for a LogRecord — typed table if registered, else `log_records`."""
    typed = typed_tables.get(record.kind)
    if typed is not None:
        return typed.table, typed_log_record_row(record, typed, promoted_labels)
    return "log_records", log_record_row(record, promoted_labels)


# ---------------------------------------------------------------------------
# Schema (built lazily; SQLAlchemy imported only here)
# ---------------------------------------------------------------------------


def build_metadata(
    *,
    indexed_labels: Optional[list[str]] = None,
    label_columns: Optional[list[tuple[str, Any]]] = None,
    typed_tables: Optional[list[TypedEventTable]] = None,
    schema: Optional[str] = None,
):
    """Build the SQLAlchemy MetaData + {name: Table} for the log tables.

    Single source of truth for the schema: both `SqlAlchemyBackend.create_tables`
    and the app's Alembic migration use this, so the DDL never drifts.

    Two ways to make a label filterable, depending on how often you query it:

    - `indexed_labels`: the label stays in the JSONB `labels` blob and gets an
      *expression* index on `(labels ->> 'k')`. Good for occasional filters; no
      schema change to add one.
    - `label_columns`: `(name, sqlalchemy_type)` pairs promoted to *real columns*
      on every table, each with a plain B-tree index, and stripped out of the
      JSONB blob at write time (see `_promote`). Good for a stable, hot filter
      key (e.g. `trip_id`) you want to query as `WHERE trip_id = 5`.

    A key listed in both is treated as a column (the JSONB expression index is
    skipped, since the key no longer lives in `labels`).
    """
    from sqlalchemy import (
        BigInteger,
        Boolean,
        Column,
        DateTime,
        Float,
        Index,
        Integer,
        MetaData,
        Table,
        Text,
    )
    from sqlalchemy.dialects.postgresql import JSONB, UUID

    indexed_labels = indexed_labels or []
    label_columns = label_columns or []
    promoted_names = [name for name, _type in label_columns]
    # A promoted label is a real column now, so don't also build a (dead) JSONB
    # expression index for it.
    expr_labels = [l for l in indexed_labels if l not in promoted_names]
    md = MetaData(schema=schema)

    def _promoted_cols() -> list:
        # Fresh Column objects per call — a Column can't be shared across tables.
        return [Column(name, col_type, nullable=True) for name, col_type in label_columns]

    def _label_indexes(table_name: str, columns) -> None:
        # Core indexes every table gets, plus an expression index per indexed
        # label and a B-tree index per promoted label column. The expression
        # index is built from `labels[label].astext` (-> the `(labels ->> 'k')`
        # expression) rather than a raw text() so SQLAlchemy binds it to the
        # table and create_all actually emits it.
        Index(f"ix_{table_name}_operation_id", columns["operation_id"])
        Index(f"ix_{table_name}_created_at", columns["created_at"])
        for label in expr_labels:
            Index(f"ix_{table_name}_{label}", columns["labels"][label].astext)
        for name in promoted_names:
            Index(f"ix_{table_name}_{name}", columns[name])

    # IDs use the native UUID type (16 bytes, natively indexed); as_uuid=False
    # means SQLAlchemy accepts/returns plain strings — no uuid.UUID objects needed.
    _UUID = UUID(as_uuid=False)

    spans = Table(
        "spans",
        md,
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("operation_id", _UUID, nullable=False),
        Column("span_id", _UUID, nullable=False),
        Column("parent_span_id", _UUID, nullable=True),
        Column("name", Text, nullable=False),
        Column("status", Text, nullable=False),
        Column("started_at", DateTime(timezone=True), nullable=True),
        Column("ended_at", DateTime(timezone=True), nullable=True),
        Column("duration_ms", Float, nullable=True),
        Column("error", Text, nullable=True),
        Column("labels", JSONB, nullable=False),
        Column("attributes", JSONB, nullable=False),
        *_promoted_cols(),
        # deployment context
        Column("version", Text, nullable=True),
        Column("environment", Text, nullable=True),
        Column("host", Text, nullable=True),
        Column("region", Text, nullable=True),
        # per-request identity
        Column("user_id", Text, nullable=True),
        Column("session_id", Text, nullable=True),
        Column("client_platform", JSONB, nullable=True),
        # reliability
        Column("retry_count", Integer, nullable=True),
        Column("http_status_code", Integer, nullable=True),
        # feature control
        Column("feature_flags", JSONB, nullable=True),
        # cross-system correlation
        Column("correlation_id", Text, nullable=True),
        Column("parent_request_id", Text, nullable=True),
        Column("idempotency_key", Text, nullable=True),
    )
    Index("ix_spans_parent_span_id", spans.c.parent_span_id)
    _label_indexes("spans", spans.c)

    log_records = Table(
        "log_records",
        md,
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("ts", DateTime(timezone=True), nullable=True),
        Column("operation_id", _UUID, nullable=True),
        Column("span_id", _UUID, nullable=True),
        Column("kind", Text, nullable=False),
        Column("level", Text, nullable=False),
        Column("message", Text, nullable=True),
        Column("fields", JSONB, nullable=False),
        Column("blob", Text, nullable=True),
        Column("labels", JSONB, nullable=False),
        *_promoted_cols(),
    )
    _label_indexes("log_records", log_records.c)

    tables = {"spans": spans, "log_records": log_records}

    for typed in typed_tables or []:
        cols = [
            Column("id", BigInteger, primary_key=True, autoincrement=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("operation_id", _UUID, nullable=True),
            Column("span_id", _UUID, nullable=True),
            Column("labels", JSONB, nullable=False),
            *_promoted_cols(),
        ]
        for name, col_type in typed.columns:
            cols.append(Column(name, col_type, nullable=True))
        if typed.blob_column is not None:
            cols.append(Column(typed.blob_column, Text, nullable=True))
        t = Table(typed.table, md, *cols)
        _label_indexes(typed.table, t.c)
        tables[typed.table] = t

    return md, tables


def _sync_dsn(dsn: str) -> str:
    """Coerce an async/bare Postgres URL to the sync psycopg2 driver the worker uses."""
    return dsn.replace("postgresql+asyncpg://", "postgresql+psycopg2://").replace(
        "postgresql://", "postgresql+psycopg2://"
    )


# ---------------------------------------------------------------------------
# Backend seam (real DB vs. test fake)
# ---------------------------------------------------------------------------


class SqlAlchemyBackend:
    """The real backend: a sync SQLAlchemy engine on its own connection pool."""

    def __init__(self, dsn: str, *, metadata, tables: dict, create_tables: bool = False) -> None:
        from sqlalchemy import create_engine

        self._engine = create_engine(_sync_dsn(dsn), pool_pre_ping=True, future=True)
        self._metadata = metadata
        self._tables = tables
        if create_tables:
            metadata.create_all(self._engine)

    def insert(self, table_name: str, rows: list[dict]) -> None:
        table = self._tables[table_name]
        with self._engine.begin() as conn:
            conn.execute(table.insert(), rows)

    def delete_older_than(self, cutoff: datetime) -> int:
        total = 0
        with self._engine.begin() as conn:
            for table in self._tables.values():
                res = conn.execute(table.delete().where(table.c.created_at < cutoff))
                total += res.rowcount or 0
        return total

    def close(self) -> None:
        self._engine.dispose()


def _warn(msg: str) -> None:
    print(f"obslog.PostgresSink: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# The sink
# ---------------------------------------------------------------------------


class PostgresSink(Sink):
    """Batched, background-thread Postgres sink. See module docstring.

    `backend` is injectable for tests; production builds a `SqlAlchemyBackend`
    from `dsn`. Set `create_tables=True` only for standalone/test use — the app
    owns its schema via Alembic (which reuses `build_metadata`).
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        *,
        retention: Optional[str | timedelta] = "3d",
        indexed_labels: Optional[list[str]] = None,
        label_columns: Optional[list[tuple[str, Any]]] = None,
        typed_tables: Optional[list[TypedEventTable]] = None,
        batch_size: int = 100,
        flush_interval: float = 0.5,
        retention_interval: float = 3600.0,
        schema: Optional[str] = None,
        create_tables: bool = False,
        backend: Optional[Any] = None,
    ) -> None:
        self._typed = {t.kind: t for t in (typed_tables or [])}
        # Names of labels promoted to real columns — stripped from JSONB and set
        # as top-level columns on every row (see span_row / route_log_record).
        self._promoted = [name for name, _type in (label_columns or [])]
        self._retention = parse_retention(retention)
        self._retention_interval = retention_interval
        self._batch_size = batch_size
        self._flush_interval = flush_interval

        if backend is None:
            if dsn is None:
                raise ValueError("PostgresSink requires either a dsn or an injected backend")
            metadata, tables = build_metadata(
                indexed_labels=indexed_labels,
                label_columns=label_columns,
                typed_tables=typed_tables,
                schema=schema,
            )
            backend = SqlAlchemyBackend(
                dsn, metadata=metadata, tables=tables, create_tables=create_tables
            )
        self._backend = backend

        self._queue: "queue.Queue[tuple[str, dict]]" = queue.Queue()
        self._stop = threading.Event()
        self._last_sweep = 0.0
        self._thread = threading.Thread(target=self._run, name="obslog-postgres", daemon=True)
        self._thread.start()

    # --- Sink API (caller thread; must be cheap + never raise) ---

    def emit_span(self, span: Span) -> None:
        self._queue.put(("spans", span_row(span, self._promoted)))

    def emit_log_record(self, record: LogRecord) -> None:
        self._queue.put(route_log_record(record, self._typed, self._promoted))

    def flush(self) -> None:
        """Block until everything enqueued so far has been written."""
        self._queue.join()

    def close(self) -> None:
        self.flush()
        self._stop.set()
        self._thread.join(timeout=5.0)
        self._backend.close()

    def cleanup(self, retention: Optional[str] = None) -> None:
        window = parse_retention(retention) if retention is not None else self._retention
        if window is None:
            return
        cutoff = _now() - window
        try:
            self._backend.delete_older_than(cutoff)
        except Exception as exc:  # retention must never crash the app
            _warn(f"retention delete failed: {exc}")

    # --- worker thread ---

    def _run(self) -> None:
        self._sweep_retention()
        while not self._stop.is_set():
            self._write_and_ack(self._drain(self._flush_interval))
            self._sweep_retention()
        # Drain anything left after a stop request so close() loses nothing.
        while True:
            batch = self._drain(0.0)
            if not batch:
                break
            self._write_and_ack(batch)

    def _drain(self, timeout: float) -> list[tuple[str, dict]]:
        items: list[tuple[str, dict]] = []
        try:
            items.append(self._queue.get(timeout=timeout) if timeout else self._queue.get_nowait())
        except queue.Empty:
            return items
        while len(items) < self._batch_size:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items

    def _write_and_ack(self, batch: list[tuple[str, dict]]) -> None:
        if not batch:
            return
        grouped: dict[str, list[dict]] = defaultdict(list)
        for table_name, row in batch:
            grouped[table_name].append(row)
        for table_name, rows in grouped.items():
            try:
                self._backend.insert(table_name, rows)
            except Exception as exc:  # a bad write must not kill the worker
                _warn(f"failed to write {len(rows)} row(s) to {table_name}: {exc}")
        for _ in batch:  # ack even on failure so flush()/close() can't hang
            self._queue.task_done()

    def _sweep_retention(self) -> None:
        if self._retention is None:
            return
        now = time.monotonic()
        if self._last_sweep and now - self._last_sweep < self._retention_interval:
            return
        self._last_sweep = now
        cutoff = _now() - self._retention
        try:
            self._backend.delete_older_than(cutoff)
        except Exception as exc:
            _warn(f"retention sweep failed: {exc}")
