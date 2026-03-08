"""Add start_time to bets for reliable event time tracking

Revision ID: 003
Revises: 002
Create Date: 2026-03-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bets', sa.Column('start_time', sa.DateTime(), nullable=True))

    # Backfill from events table for bets that have event_id
    op.execute("""
        UPDATE bets
        SET start_time = (SELECT start_time FROM events WHERE events.id = bets.event_id)
        WHERE event_id IS NOT NULL
          AND start_time IS NULL
    """)


def downgrade() -> None:
    op.drop_column('bets', 'start_time')
