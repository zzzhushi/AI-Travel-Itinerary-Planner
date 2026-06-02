"""remove_research_hash_from_option

Revision ID: ea048876dec1
Revises: 550e04efc00a
Create Date: 2026-06-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'ea048876dec1'
down_revision: Union[str, None] = '550e04efc00a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_options_research_hash', table_name='options')
    op.drop_column('options', 'research_hash')


def downgrade() -> None:
    op.add_column('options', sa.Column('research_hash', sa.String(length=32), nullable=True))
    op.create_index('ix_options_research_hash', 'options', ['research_hash'])
