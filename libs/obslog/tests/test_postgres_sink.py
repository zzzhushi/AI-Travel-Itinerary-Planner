"""Phase 2 tests for PostgresSink.

The threading/batching/routing/retention logic is exercised against a FakeBackend
so these run with no database. The real SqlAlchemyBackend is covered by an
integration test gated on OBSLOG_TEST_DATABASE_URL (skipped by default).
"""

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import obslog
from obslog import sink as sink_registry
from obslog.records import Event, Span
from obslog.sinks.postgres import (
    PostgresSink,
    TypedEventTable,
    event_row,
    parse_retention,
    route_event,
    span_row,
    typed_event_row,
)


# --- pure mappers ---------------------------------------------------------

def test_parse_retention_units():
    assert parse_retention("3d") == timedelta(days=3)
    assert parse_retention("12h") == timedelta(hours=12)
    assert parse_retention("30m") == timedelta(minutes=30)
    assert parse_retention("1w") == timedelta(weeks=1)
    assert parse_retention(None) is None
    assert parse_retention("") is None
    assert parse_retention(timedelta(days=2)) == timedelta(days=2)


def test_parse_retention_rejects_bad_unit():
    with pytest.raises(ValueError):
        parse_retention("3y")


def test_span_row_converts_epoch_to_utc_datetime():
    span = Span(
        operation_id="op", span_id="s1", parent_span_id=None, name="root",
        status="success", started_at=1_700_000_000.0, ended_at=1_700_000_001.5,
        duration_ms=1500.0, labels={"trip_id": 5}, attributes={"source": "llm"},
    )
    row = span_row(span)
    assert row["operation_id"] == "op" and row["span_id"] == "s1"
    assert row["started_at"] == datetime.fromtimestamp(1_700_000_000.0, tz=timezone.utc)
    assert row["ended_at"].tzinfo is not None
    assert row["labels"] == {"trip_id": 5} and row["attributes"] == {"source": "llm"}


def test_event_row_keeps_blob_and_fields():
    ev = Event(operation_id="op", span_id="s1", ts=1_700_000_000.0, kind="trace",
               level="debug", message="hi", fields={"n": 3}, blob="big")
    row = event_row(ev)
    assert row["kind"] == "trace" and row["message"] == "hi"
    assert row["fields"] == {"n": 3} and row["blob"] == "big"


def test_route_event_to_typed_table_promotes_columns():
    llm = TypedEventTable(
        kind="llm_call", table="llm_calls",
        columns=[("model", str), ("latency_ms", float), ("status", str)],
        blob_column="response",
    )
    ev = Event(operation_id="op", span_id="s1", ts=1.0, kind="llm_call",
               fields={"model": "gemini", "latency_ms": 42.0, "status": "ok"},
               blob="full response")
    table, row = route_event(ev, {"llm_call": llm})
    assert table == "llm_calls"
    assert row["model"] == "gemini" and row["latency_ms"] == 42.0 and row["status"] == "ok"
    assert row["response"] == "full response"
    # promoted fields not present default to None, not KeyError
    sparse = Event(operation_id="op", span_id="s", ts=1.0, kind="llm_call", fields={})
    _, srow = route_event(sparse, {"llm_call": llm})
    assert srow["model"] is None and srow["latency_ms"] is None


def test_route_event_unregistered_kind_goes_to_events():
    ev = Event(operation_id="op", span_id="s", ts=1.0, kind="trace", fields={})
    table, _ = route_event(ev, {})
    assert table == "events"


def test_typed_event_row_without_blob_column_omits_blob():
    tt = TypedEventTable(kind="k", table="t", columns=[("a", str)])
    ev = Event(operation_id="op", span_id="s", ts=1.0, kind="k", fields={"a": "x"}, blob="ignored")
    row = typed_event_row(ev, tt)
    assert "response" not in row and "blob" not in row and row["a"] == "x"


# --- batching / threading / retention via a fake backend ------------------

class FakeBackend:
    def __init__(self):
        self.inserts = []  # list of (table_name, rows)
        self.deletes = []  # list of cutoffs
        self.closed = False
        self._lock = threading.Lock()

    def insert(self, table_name, rows):
        with self._lock:
            self.inserts.append((table_name, list(rows)))

    def delete_older_than(self, cutoff):
        with self._lock:
            self.deletes.append(cutoff)
        return 0

    def close(self):
        self.closed = True

    def all_rows(self, table_name):
        with self._lock:
            return [r for name, rows in self.inserts if name == table_name for r in rows]


@pytest.fixture
def fake():
    return FakeBackend()


def _drain_sink(sink):
    sink.flush()


def test_emit_writes_rows_through_worker(fake):
    sink = PostgresSink(backend=fake, retention=None, flush_interval=0.01)
    obslog.set_sink(sink)
    try:
        with obslog.operation("op", labels={"trip_id": 7}):
            obslog.trace("hello", n=1)
        sink.flush()
    finally:
        sink.close()
        sink_registry.reset()
    spans = fake.all_rows("spans")
    events = fake.all_rows("events")
    assert [s["status"] for s in spans] == ["running", "success"]
    assert spans[0]["labels"]["trip_id"] == 7
    assert len(events) == 1 and events[0]["message"] == "hello"


def test_typed_event_routed_to_llm_calls(fake):
    llm = TypedEventTable(kind="llm_call", table="llm_calls",
                          columns=[("model", str)], blob_column="response")
    sink = PostgresSink(backend=fake, retention=None, typed_tables=[llm], flush_interval=0.01)
    obslog.set_sink(sink)
    try:
        with obslog.operation("op"):
            obslog.event("llm_call", model="gemini", blob="resp")
        sink.flush()
    finally:
        sink.close()
        sink_registry.reset()
    llm_rows = fake.all_rows("llm_calls")
    assert len(llm_rows) == 1 and llm_rows[0]["model"] == "gemini"
    assert llm_rows[0]["response"] == "resp"
    assert fake.all_rows("events") == []  # routed away from the generic table


def test_batches_multiple_rows_into_few_inserts(fake):
    sink = PostgresSink(backend=fake, retention=None, batch_size=100, flush_interval=0.05)
    obslog.set_sink(sink)
    try:
        with obslog.operation("op"):
            for i in range(50):
                obslog.trace("tick", i=i)
        sink.flush()
    finally:
        sink.close()
        sink_registry.reset()
    # 50 trace events should be far fewer than 50 insert calls (batched).
    event_inserts = [rows for name, rows in fake.inserts if name == "events"]
    assert sum(len(r) for r in event_inserts) == 50
    assert len(event_inserts) < 50


def test_retention_sweep_runs_on_startup(fake):
    sink = PostgresSink(backend=fake, retention="3d", flush_interval=0.01)
    try:
        # the worker sweeps once on startup; wait briefly for the thread
        for _ in range(100):
            if fake.deletes:
                break
            time.sleep(0.01)
        assert fake.deletes, "expected a startup retention sweep"
        cutoff = fake.deletes[0]
        expected = datetime.now(timezone.utc) - timedelta(days=3)
        assert abs((cutoff - expected).total_seconds()) < 5
    finally:
        sink.close()


def test_cleanup_triggers_delete(fake):
    sink = PostgresSink(backend=fake, retention="3d", flush_interval=0.01)
    try:
        before = len(fake.deletes)
        sink.cleanup()
        assert len(fake.deletes) == before + 1
    finally:
        sink.close()


def test_no_retention_means_no_delete(fake):
    sink = PostgresSink(backend=fake, retention=None, flush_interval=0.01)
    try:
        sink.cleanup()
        time.sleep(0.05)
        assert fake.deletes == []
    finally:
        sink.close()


def test_close_flushes_and_closes_backend(fake):
    sink = PostgresSink(backend=fake, retention=None, flush_interval=0.01)
    obslog.set_sink(sink)
    with obslog.operation("op"):
        obslog.trace("t")
    sink.close()
    sink_registry.reset()
    assert fake.closed is True
    assert fake.all_rows("spans")  # flushed before close


def test_backend_insert_failure_does_not_hang_flush():
    class BoomBackend(FakeBackend):
        def insert(self, table_name, rows):
            raise RuntimeError("db down")

    boom = BoomBackend()
    sink = PostgresSink(backend=boom, retention=None, flush_interval=0.01)
    obslog.set_sink(sink)
    try:
        with obslog.operation("op"):
            obslog.trace("t")
        sink.flush()  # must return even though every insert raised
    finally:
        sink.close()
        sink_registry.reset()


def test_requires_dsn_or_backend():
    with pytest.raises(ValueError):
        PostgresSink()


# --- integration: real Postgres (opt-in) ----------------------------------

@pytest.mark.skipif(
    not os.getenv("OBSLOG_TEST_DATABASE_URL"),
    reason="set OBSLOG_TEST_DATABASE_URL to run the real-Postgres integration test",
)
def test_end_to_end_postgres_and_retention():
    from sqlalchemy import Float, Text, create_engine, text

    from obslog.sinks.postgres import _sync_dsn, build_metadata

    dsn = os.environ["OBSLOG_TEST_DATABASE_URL"]
    llm = TypedEventTable(
        kind="llm_call", table="llm_calls",
        columns=[("model", Text), ("latency_ms", Float), ("status", Text)],
        blob_column="response",
    )
    md, tables = build_metadata(indexed_labels=["trip_id"], typed_tables=[llm])
    engine = create_engine(_sync_dsn(dsn), future=True)
    md.drop_all(engine)
    sink = PostgresSink(dsn, retention="3d", indexed_labels=["trip_id"],
                        typed_tables=[llm], create_tables=True, flush_interval=0.01)
    obslog.set_sink(sink)
    try:
        with obslog.operation("smoke", labels={"trip_id": 1}):
            obslog.trace("breadcrumb", n=1)
            obslog.event("llm_call", model="gemini", latency_ms=10.0, status="ok", blob="resp")
        sink.flush()
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM spans")).scalar() == 2
            assert conn.execute(text("SELECT count(*) FROM events")).scalar() == 1
            assert conn.execute(text("SELECT count(*) FROM llm_calls")).scalar() == 1
            # backdate a span beyond the window, then prove retention deletes it
            conn.execute(text(
                "UPDATE spans SET created_at = now() - interval '4 days' WHERE name='smoke'"
            ))
        sink.cleanup()
        with engine.begin() as conn:
            remaining = conn.execute(
                text("SELECT count(*) FROM spans WHERE name='smoke'")
            ).scalar()
        assert remaining == 0
    finally:
        sink.close()
        sink_registry.reset()
        md.drop_all(engine)
        engine.dispose()
