"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trips",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("num_days", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "trip_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("interests", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("is_specific", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "options",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("activity_id", sa.Integer(), sa.ForeignKey("activities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("maps_link", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("user_rating", sa.Integer(), nullable=True),
        sa.Column("research_hash", sa.String(32), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "scheduled_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trip_id", sa.Integer(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("option_id", sa.Integer(), sa.ForeignKey("options.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=True),
        sa.Column("time_slot", sa.String(20), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("scheduled_items")
    op.drop_table("options")
    op.drop_table("activities")
    op.drop_table("trip_preferences")
    op.drop_table("trips")
