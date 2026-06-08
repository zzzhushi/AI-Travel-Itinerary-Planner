"""add obslog logging tables (spans, events, llm_calls)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-06

Schema is defined once in obslog.sinks.postgres.build_metadata and reused here,
with the same app policy (indexed_labels + the llm_calls typed table) as
src/logging_setup.py, so the migration and the runtime sink never drift.
"""
from typing import Sequence, Union

from alembic import op

from obslog.sinks.postgres import build_metadata
from src.logging_setup import indexed_labels, _llm_calls_typed_table

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _metadata():
    return build_metadata(
        indexed_labels=indexed_labels(),
        typed_tables=[_llm_calls_typed_table()],
    )[0]


def upgrade() -> None:
    _metadata().create_all(op.get_bind())


def downgrade() -> None:
    _metadata().drop_all(op.get_bind())
