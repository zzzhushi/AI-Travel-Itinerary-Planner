# obslog

Structured **span + event** logging with **ambient correlation** and **pluggable sinks**.

- **Spans** form an operation tree (a root `operation()` with nested `span()`s).
- **Events** (`trace()`, `event()`) attach to the active span.
- **Correlation is ambient** via `contextvars` — nested code never threads ids through
  signatures, concurrent operations are isolated automatically, and everything in one
  operation shares an `operation_id`.
- **No-op-safe** — calling `trace()` with no active span (or before a sink is set) does nothing.
- **Storage-agnostic** — pick a `Sink` (`StdoutSink`, `JsonlFileSink`, your own); the core is stdlib-only.

```python
from obslog import operation, span, trace, set_sink
from obslog.sinks import StdoutSink

set_sink(StdoutSink())

async with operation("generate_schedule", labels={"trip_id": 42}) as op:
    trace("options_loaded", count=12)
    async with span("llm_plan") as s:
        ...
        s.fail("retries exhausted")      # this child failed; the operation is unaffected
    op.set(source="deterministic")        # the operation still succeeded
```

## Extensibility (core = mechanism, project = policy)

| Need | Mechanism |
|---|---|
| Filterable correlation dims | `labels={...}` (arbitrary), or `bind_labels(**labels)` for a whole scope |
| Cross-cutting fields on every record | `configure(processors=[fn, ...])` |
| A new backend | implement `Sink` |
| Carry correlation into a thread/executor | `bind_context(fn)` |

## Postgres backend

`PostgresSink` persists records for query/diagnostics. It never blocks the
caller — `emit_*` enqueues, and a daemon thread batch-inserts on its **own**
connection (so logs land outside the app's transaction; a rolled-back operation
still leaves its logs). Retention deletes rows older than the window.

```python
from sqlalchemy import Float, Text
from obslog import set_sink
from obslog.sinks import PostgresSink, TypedEventTable

# Route kind="llm_call" events to a dedicated table with promoted columns.
llm_calls = TypedEventTable(
    kind="llm_call", table="llm_calls",
    columns=[("model", Text), ("latency_ms", Float), ("status", Text)],
    blob_column="response",                       # event.blob -> response column
)

set_sink(PostgresSink(
    dsn,                                          # async/bare URLs are coerced to psycopg2
    retention="3d",                               # s/m/h/d/w; None disables
    indexed_labels=["trip_id"],                   # expression index on (labels ->> 'trip_id')
    typed_tables=[llm_calls],
))
```

- **Schema** is defined once in `obslog.sinks.postgres.build_metadata` (tables
  `spans`, `events`, plus any typed tables). Manage it with your own migration
  tool (reuse `build_metadata`), or pass `create_tables=True` for standalone use.
- Needs the extra: `pip install obslog[postgres]` (SQLAlchemy + psycopg2).
- `flush()` blocks until the queue drains; `close()` flushes, stops the worker,
  and disposes the engine; `cleanup()` triggers retention on demand.

## Install

No PyPI needed:

```bash
pip install -e libs/obslog                 # local editable
pip install -e "libs/obslog[postgres]"     # with the Postgres backend
pip install "git+<repo>#subdirectory=libs/obslog"   # from a git repo
```
