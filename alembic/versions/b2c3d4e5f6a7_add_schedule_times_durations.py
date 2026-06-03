"""add schedule times and durations

Revision ID: b2c3d4e5f6a7
Revises: 3f9c1e7a2b48
Create Date: 2026-06-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "3f9c1e7a2b48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("options", sa.Column("default_duration_minutes", sa.Integer(), nullable=True))
    op.add_column("scheduled_items", sa.Column("start_minutes", sa.Integer(), nullable=True))
    op.add_column("scheduled_items", sa.Column("duration_minutes", sa.Integer(), nullable=True))
    # Backfill existing rows from the legacy time_slot so the future timeline (P3)
    # has start positions to render. morning=09:00, afternoon=13:00, evening=18:00.
    op.execute(
        """
        UPDATE scheduled_items SET start_minutes = CASE time_slot
            WHEN 'morning' THEN 540
            WHEN 'afternoon' THEN 780
            WHEN 'evening' THEN 1080
            ELSE 540
        END
        WHERE start_minutes IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("scheduled_items", "duration_minutes")
    op.drop_column("scheduled_items", "start_minutes")
    op.drop_column("options", "default_duration_minutes")
