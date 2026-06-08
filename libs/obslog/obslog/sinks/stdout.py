"""Zero-dependency sinks: JSON lines to stdout or a file. Good for local dev."""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
from typing import IO, Any

from ..records import LogRecord, Span
from ..sink import Sink


def _to_line(kind: str, record: Any) -> str:
    payload = {"type": kind, **dataclasses.asdict(record)}
    return json.dumps(payload, default=str)


class StdoutSink(Sink):
    """Write each record as a JSON line to a stream (stdout by default)."""

    def __init__(self, stream: IO[str] | None = None) -> None:
        # Keep `None` and resolve sys.stdout lazily at write time, so later
        # redirection (pytest capsys, contextlib.redirect_stdout) is honored.
        self._stream = stream
        self._lock = threading.Lock()

    def emit_span(self, span: Span) -> None:
        self._write(_to_line("span", span))

    def emit_log_record(self, record: LogRecord) -> None:
        self._write(_to_line("log_record", record))

    def _write(self, line: str) -> None:
        stream = self._stream if self._stream is not None else sys.stdout
        with self._lock:
            stream.write(line + "\n")
            stream.flush()


class JsonlFileSink(Sink):
    """Append each record as a JSON line to a file. Thread-safe.

    Holds one append-mode handle open for the sink's lifetime instead of
    reopening per record; call close() to release it.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8")

    def emit_span(self, span: Span) -> None:
        self._append(_to_line("span", span))

    def emit_log_record(self, record: LogRecord) -> None:
        self._append(_to_line("log_record", record))

    def _append(self, line: str) -> None:
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()
