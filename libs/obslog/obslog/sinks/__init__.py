"""Built-in sink implementations. Heavier backends (Postgres) are added here."""

from .stdout import JsonlFileSink, StdoutSink
from .postgres import PostgresSink, TypedEventTable

__all__ = ["StdoutSink", "JsonlFileSink", "PostgresSink", "TypedEventTable"]
