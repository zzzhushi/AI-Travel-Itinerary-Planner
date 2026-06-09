"""add pace/budget/day-window fields to trip_preferences

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-06-05

Re-chained after the obslog tables migration (e6f7a8b9c0d1): this branch
originally also used revision e6f7a8b9c0d1, which collided when the obslog
library merged to main first. New id + down_revision keeps the history linear.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trip_preferences", sa.Column("pace", sa.String(length=20), nullable=True))
    op.add_column("trip_preferences", sa.Column("budget", sa.String(length=20), nullable=True))
    op.add_column("trip_preferences", sa.Column("day_start_minutes", sa.Integer(), nullable=True))
    op.add_column("trip_preferences", sa.Column("day_end_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("trip_preferences", "day_end_minutes")
    op.drop_column("trip_preferences", "day_start_minutes")
    op.drop_column("trip_preferences", "budget")
    op.drop_column("trip_preferences", "pace")
