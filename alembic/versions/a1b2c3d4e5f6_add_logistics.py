"""add logistics table (travel + lodging)

Revision ID: a1b2c3d4e5f6
Revises: f7a8b9c0d1e2
Create Date: 2026-06-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "logistics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("day_number", sa.Integer(), nullable=True),
        sa.Column("time_minutes", sa.Integer(), nullable=True),
        sa.Column("transit_minutes", sa.Integer(), nullable=True),
        sa.Column("check_in_day", sa.Integer(), nullable=True),
        sa.Column("check_out_day", sa.Integer(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("place_id", sa.String(length=255), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("maps_link", sa.Text(), nullable=True),
        sa.Column("place_refreshed_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_logistics_trip_id", "logistics", ["trip_id"])


def downgrade() -> None:
    op.drop_index("ix_logistics_trip_id", table_name="logistics")
    op.drop_table("logistics")
