"""Rename dutch -> arb: extraction_features column + opportunities/bets type values

Revision ID: 004
Revises: 003
Create Date: 2026-04-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'extraction_features',
        'dutch_opportunities_found',
        new_column_name='arb_opportunities_found',
    )

    op.execute("UPDATE opportunities SET type = 'arb' WHERE type = 'dutch'")
    op.execute("UPDATE bets SET bet_type = 'arb' WHERE bet_type = 'dutch'")


def downgrade() -> None:
    op.alter_column(
        'extraction_features',
        'arb_opportunities_found',
        new_column_name='dutch_opportunities_found',
    )

    op.execute("UPDATE opportunities SET type = 'dutch' WHERE type = 'arb'")
    op.execute("UPDATE bets SET bet_type = 'dutch' WHERE bet_type = 'arb'")
