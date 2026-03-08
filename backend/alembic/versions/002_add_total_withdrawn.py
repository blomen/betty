"""Add total_withdrawn to profiles

Revision ID: 002
Revises: 001
Create Date: 2026-03-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('profiles', sa.Column('total_withdrawn', sa.Float(), server_default='0.0'))


def downgrade() -> None:
    op.drop_column('profiles', 'total_withdrawn')
