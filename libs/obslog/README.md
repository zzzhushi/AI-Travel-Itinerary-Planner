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

## Install

No PyPI needed:

```bash
pip install -e libs/obslog                 # local editable
pip install "git+<repo>#subdirectory=libs/obslog"   # from a git repo
```
