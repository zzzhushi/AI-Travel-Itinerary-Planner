"""Built-in sink implementations. Heavier backends (Postgres) are added here.

`postgres` imports SQLAlchemy lazily (inside functions), so importing this
package is cheap and dependency-free; only *building* a PostgresSink needs the
`obslog[postgres]` extra.
"""

from .postgres import PostgresSink, TypedEventTable
from .stdout import JsonlFileSink, StdoutSink

__all__ = ["StdoutSink", "JsonlFileSink", "PostgresSink", "TypedEventTable"]
