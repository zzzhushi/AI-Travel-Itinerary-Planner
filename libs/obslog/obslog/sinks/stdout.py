"""Zero-dependency sinks: JSON lines to stdout or a file. Good for local dev."""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
from typing import IO, Any

from ..records import Event, Span
from ..sink import Sink


def _to_line(kind: str, record: Any) -> str:
    payload = {"type": kind, **dataclasses.asdict(record)}
    return json.dumps(payload, default=str)


class StdoutSink(Sink):
    """Write each record as a JSON line to a stream (stdout by default)."""

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._lock = threading.Lock()

    def emit_span(self, span: Span) -> None:
        self._write(_to_line("span", span))

    def emit_event(self, event: Event) -> None:
        self._write(_to_line("event", event))

    def _write(self, line: str) -> None:
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()


class JsonlFileSink(Sink):
    """Append each record as a JSON line to a file. Thread-safe."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()

    def emit_span(self, span: Span) -> None:
        self._append(_to_line("span", span))

    def emit_event(self, event: Event) -> None:
        self._append(_to_line("event", event))

    def _append(self, line: str) -> None:
        with self._lock, open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
