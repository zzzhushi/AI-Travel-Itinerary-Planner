"""add researched_at to activities

Revision ID: 002
Revises: 001
Create Date: 2026-06-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("researched_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "researched_at")
