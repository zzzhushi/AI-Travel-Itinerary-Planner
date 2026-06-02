"""add category to option

Revision ID: 3f9c1e7a2b48
Revises: ea048876dec1
Create Date: 2026-06-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "3f9c1e7a2b48"
down_revision: Union[str, None] = "ea048876dec1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("options", sa.Column("category", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("options", "category")
