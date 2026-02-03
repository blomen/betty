"""Initial schema - represents current database state

Revision ID: 001
Revises:
Create Date: 2026-02-03

This migration represents the existing schema. It's used as a baseline
for databases that were created before Alembic was introduced.

For new databases, this will create all tables.
For existing databases, run `alembic stamp 001` to mark this as applied.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create events table
    op.create_table(
        'events',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('sport', sa.String(), nullable=False),
        sa.Column('league', sa.String(), nullable=True),
        sa.Column('home_team', sa.String(), nullable=False),
        sa.Column('away_team', sa.String(), nullable=False),
        sa.Column('start_time', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create providers table
    op.create_table(
        'providers',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=True),
        sa.Column('balance', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create odds table
    op.create_table(
        'odds',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.String(), nullable=False),
        sa.Column('provider_id', sa.String(), nullable=False),
        sa.Column('market', sa.String(), nullable=False),
        sa.Column('outcome', sa.String(), nullable=False),
        sa.Column('odds', sa.Float(), nullable=False),
        sa.Column('point', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id']),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'provider_id', 'market', 'outcome', 'point', name='uq_odds_with_point')
    )
    op.create_index('ix_odds_event_provider_outcome', 'odds', ['event_id', 'provider_id', 'outcome'], unique=False)

    # Create bets table
    op.create_table(
        'bets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.String(), nullable=True),
        sa.Column('provider_id', sa.String(), nullable=False),
        sa.Column('market', sa.String(), nullable=True),
        sa.Column('outcome', sa.String(), nullable=True),
        sa.Column('odds', sa.Float(), nullable=False),
        sa.Column('stake', sa.Float(), nullable=False),
        sa.Column('is_bonus', sa.Boolean(), nullable=True),
        sa.Column('bonus_type', sa.String(), nullable=True),
        sa.Column('result', sa.String(), nullable=True),
        sa.Column('payout', sa.Float(), nullable=True),
        sa.Column('placed_at', sa.DateTime(), nullable=True),
        sa.Column('settled_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id']),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # Create profiles table
    op.create_table(
        'profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('bankroll', sa.Float(), nullable=True),
        sa.Column('currency', sa.String(), nullable=True),
        sa.Column('kelly_fraction', sa.Float(), nullable=True),
        sa.Column('min_edge_pct', sa.Float(), nullable=True),
        sa.Column('min_arb_pct', sa.Float(), nullable=True),
        sa.Column('max_stake_pct', sa.Float(), nullable=True),
        sa.Column('min_retention_pct', sa.Float(), nullable=True),
        sa.Column('preferred_counterparts', sa.String(), nullable=True),
        sa.Column('bonus_enabled', sa.Boolean(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )

    # Create opportunities table
    op.create_table(
        'opportunities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('event_id', sa.String(), nullable=True),
        sa.Column('market', sa.String(), nullable=True),
        sa.Column('provider1_id', sa.String(), nullable=True),
        sa.Column('provider2_id', sa.String(), nullable=True),
        sa.Column('odds1', sa.Float(), nullable=True),
        sa.Column('odds2', sa.Float(), nullable=True),
        sa.Column('outcome1', sa.String(), nullable=True),
        sa.Column('outcome2', sa.String(), nullable=True),
        sa.Column('outcomes', sa.JSON(), nullable=True),
        sa.Column('point', sa.Float(), nullable=True),
        sa.Column('total_stake', sa.Float(), nullable=True),
        sa.Column('profit_pct', sa.Float(), nullable=True),
        sa.Column('edge_pct', sa.Float(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('detected_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['event_id'], ['events.id']),
        sa.ForeignKeyConstraint(['provider1_id'], ['providers.id']),
        sa.ForeignKeyConstraint(['provider2_id'], ['providers.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # Create extraction_runs table
    op.create_table(
        'extraction_runs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('providers_attempted', sa.Integer(), nullable=True),
        sa.Column('providers_succeeded', sa.Integer(), nullable=True),
        sa.Column('providers_failed', sa.Integer(), nullable=True),
        sa.Column('total_events', sa.Integer(), nullable=True),
        sa.Column('total_odds', sa.Integer(), nullable=True),
        sa.Column('polymarket_events', sa.Integer(), nullable=True),
        sa.Column('trigger', sa.String(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create provider_run_metrics table
    op.create_table(
        'provider_run_metrics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(), nullable=True),
        sa.Column('provider_id', sa.String(), nullable=False),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('events_processed', sa.Integer(), nullable=True),
        sa.Column('events_new', sa.Integer(), nullable=True),
        sa.Column('odds_processed', sa.Integer(), nullable=True),
        sa.Column('odds_new', sa.Integer(), nullable=True),
        sa.Column('sports_attempted', sa.Integer(), nullable=True),
        sa.Column('sports_succeeded', sa.Integer(), nullable=True),
        sa.Column('retries', sa.Integer(), nullable=True),
        sa.Column('cache_hits', sa.Integer(), nullable=True),
        sa.Column('avg_response_time', sa.Float(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('circuit_breaker_tripped', sa.Boolean(), nullable=True),
        sa.Column('health_check_passed', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['extraction_runs.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # Create sport_run_metrics table
    op.create_table(
        'sport_run_metrics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(), nullable=True),
        sa.Column('provider_run_id', sa.Integer(), nullable=True),
        sa.Column('provider_id', sa.String(), nullable=False),
        sa.Column('sport', sa.String(), nullable=False),
        sa.Column('events_extracted', sa.Integer(), nullable=True),
        sa.Column('odds_extracted', sa.Integer(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=True),
        sa.Column('error_type', sa.String(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['provider_run_id'], ['provider_run_metrics.id']),
        sa.ForeignKeyConstraint(['run_id'], ['extraction_runs.id']),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('sport_run_metrics')
    op.drop_table('provider_run_metrics')
    op.drop_table('extraction_runs')
    op.drop_table('opportunities')
    op.drop_table('profiles')
    op.drop_table('bets')
    op.drop_index('ix_odds_event_provider_outcome', table_name='odds')
    op.drop_table('odds')
    op.drop_table('providers')
    op.drop_table('events')
