"""Phase 1 checkpoint tests for the obslog core (no DB, via InMemorySink).

Async cases run through asyncio.run() so no pytest-asyncio plugin is needed.
"""

import asyncio
import threading
import time

import pytest

import obslog
from obslog import sink as sink_registry
from obslog.records import Span
from obslog.testing import InMemorySink


@pytest.fixture
def mem():
    s = InMemorySink()
    obslog.set_sink(s)
    yield s
    sink_registry.reset()


# --- span lifecycle -------------------------------------------------------

def test_span_emits_running_then_terminal_success(mem):
    with obslog.operation("op"):
        pass
    assert [s.status for s in mem.spans] == ["running", "success"]
    running, terminal = mem.spans
    assert running.span_id == terminal.span_id
    assert terminal.parent_span_id is None
    assert terminal.duration_ms is not None and terminal.duration_ms >= 0


def test_exception_marks_failed_and_propagates(mem):
    with pytest.raises(ValueError):
        with obslog.operation("op"):
            raise ValueError("boom")
    terminal = mem.terminal_spans()[0]
    assert terminal.status == "failed"
    assert "ValueError" in terminal.error and "boom" in terminal.error


def test_fail_marks_failed_without_raising(mem):
    with obslog.operation("op") as op:
        op.fail("nope")
    terminal = mem.terminal_spans()[0]
    assert terminal.status == "failed"
    assert terminal.error == "nope"


# --- nesting & correlation ------------------------------------------------

def test_nesting_parent_child_and_shared_operation_id(mem):
    with obslog.operation("root") as root:
        with obslog.span("child") as child:
            assert child.parent_span_id == root.span_id
            assert child.operation_id == root.operation_id
    root_term = next(s for s in mem.terminal_spans() if s.name == "root")
    child_term = next(s for s in mem.terminal_spans() if s.name == "child")
    assert child_term.parent_span_id == root_term.span_id
    assert child_term.operation_id == root_term.operation_id


def test_trace_attaches_to_current_span(mem):
    with obslog.operation("root") as root:
        obslog.trace("hello", n=1)
        with obslog.span("child") as child:
            obslog.trace("inside", n=2)
    e_root = next(e for e in mem.events if e.message == "hello")
    e_child = next(e for e in mem.events if e.message == "inside")
    assert e_root.span_id == root.span_id
    assert e_child.span_id == child.span_id
    assert e_root.operation_id == e_child.operation_id
    assert e_child.kind == "trace" and e_child.fields == {"n": 2}


def test_deep_trace_without_plumbing(mem):
    def helper():  # receives no ids, still correlates via the contextvar
        obslog.trace("from_helper", deep=True)

    with obslog.operation("root") as root:
        helper()
    e = mem.events[0]
    assert e.operation_id == root.operation_id and e.span_id == root.span_id


# --- no-op safety ---------------------------------------------------------

def test_trace_is_noop_without_active_span(mem):
    obslog.trace("orphan", x=1)
    obslog.event("custom", message="orphan2")
    assert mem.events == []


def test_noop_with_null_sink_does_not_raise():
    sink_registry.reset()  # NullSink default
    with obslog.operation("op") as op:
        obslog.trace("t")
        op.set(a=1)
    # success = no exception raised


# --- concurrency isolation ------------------------------------------------

def test_concurrency_isolation():
    s = InMemorySink()
    obslog.set_sink(s)
    try:
        async def worker(tag):
            async with obslog.operation("op", labels={"tag": tag}) as op:
                await asyncio.sleep(0)  # force the two tasks to interleave
                obslog.trace("tick", tag=tag)
                await asyncio.sleep(0)
                return op.operation_id

        async def main():
            return await asyncio.gather(worker("a"), worker("b"))

        op_a, op_b = asyncio.run(main())
        assert op_a != op_b
        by_op = {op_a: "a", op_b: "b"}
        for e in s.events:  # each event's tag matches the op it belongs to
            assert e.fields["tag"] == by_op[e.operation_id]
        roots = [sp for sp in s.terminal_spans() if sp.parent_span_id is None]
        assert {r.operation_id for r in roots} == {op_a, op_b}
    finally:
        sink_registry.reset()


# --- labels & context propagation -----------------------------------------

def test_bind_labels_inherited(mem):
    with obslog.bind_labels(request_id="r1"):
        with obslog.operation("op", labels={"trip_id": 5}):
            obslog.trace("t")
    term = mem.terminal_spans()[0]
    assert term.labels["request_id"] == "r1" and term.labels["trip_id"] == 5
    assert mem.events[0].labels["request_id"] == "r1"


def test_child_inherits_parent_labels(mem):
    with obslog.operation("root", labels={"trip_id": 9}):
        with obslog.span("child") as child:
            assert child.labels["trip_id"] == 9


def test_bind_context_carries_correlation_into_thread(mem):
    captured = {}

    def work():
        obslog.trace("in_thread")
        cur = obslog.current_span()
        captured["op"] = cur.operation_id if cur else None

    with obslog.operation("root") as root:
        t = threading.Thread(target=obslog.bind_context(work))
        t.start()
        t.join()
    assert captured["op"] == root.operation_id
    assert any(e.message == "in_thread" and e.operation_id == root.operation_id for e in mem.events)


def test_without_bind_context_thread_is_noop(mem):
    def work():
        obslog.trace("in_thread")

    with obslog.operation("root"):
        t = threading.Thread(target=work)  # fresh empty context → no active span
        t.start()
        t.join()
    assert all(e.message != "in_thread" for e in mem.events)


# --- crash detection, processors, dual sync/async -------------------------

def test_running_without_terminal_detects_crash(mem):
    # Simulate a crash: a running record with no terminal (process died mid-span).
    mem.emit_span(Span(operation_id="o", span_id="s1", parent_span_id=None,
                       name="stuck", status="running", started_at=time.time()))
    assert [s.span_id for s in mem.running_without_terminal()] == ["s1"]


def test_processors_and_service_label():
    s = InMemorySink()
    obslog.set_sink(s)

    def add_env(record):
        record.labels["env"] = "test"
        return record

    obslog.configure(service="svc", processors=[add_env])
    try:
        with obslog.operation("op"):
            obslog.trace("t")
        for rec in s.spans + s.events:
            assert rec.labels["service"] == "svc" and rec.labels["env"] == "test"
    finally:
        sink_registry.reset()


def test_async_and_sync_both_work(mem):
    with obslog.operation("sync_op"):
        pass

    async def go():
        async with obslog.operation("async_op"):
            pass

    asyncio.run(go())
    names = {s.name for s in mem.terminal_spans()}
    assert {"sync_op", "async_op"} <= names
