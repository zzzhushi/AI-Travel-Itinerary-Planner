"""add_places_fields_to_option

Revision ID: 550e04efc00a
Revises: 002
Create Date: 2026-06-02 01:53:36.666187

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '550e04efc00a'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('options', sa.Column('maps_search', sa.Text(), nullable=True))
    op.add_column('options', sa.Column('why', sa.Text(), nullable=True))
    op.add_column('options', sa.Column('place_id', sa.String(length=255), nullable=True))
    op.add_column('options', sa.Column('google_rating', sa.Float(), nullable=True))
    op.add_column('options', sa.Column('price_level', sa.Integer(), nullable=True))
    op.add_column('options', sa.Column('phone_number', sa.String(length=30), nullable=True))
    op.add_column('options', sa.Column('website', sa.Text(), nullable=True))
    op.add_column('options', sa.Column('opening_hours', sa.Text(), nullable=True))
    op.add_column('options', sa.Column('place_refreshed_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('options', 'place_refreshed_at')
    op.drop_column('options', 'opening_hours')
    op.drop_column('options', 'website')
    op.drop_column('options', 'phone_number')
    op.drop_column('options', 'price_level')
    op.drop_column('options', 'google_rating')
    op.drop_column('options', 'place_id')
    op.drop_column('options', 'why')
    op.drop_column('options', 'maps_search')
