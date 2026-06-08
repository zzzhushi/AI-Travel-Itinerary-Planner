"""Built-in sink implementations. Heavier backends (Postgres) are added here."""

from .stdout import JsonlFileSink, StdoutSink

__all__ = ["StdoutSink", "JsonlFileSink"]
