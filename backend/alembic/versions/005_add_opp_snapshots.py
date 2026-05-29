"""Add opp_snapshots table for CLV tracking on all detected opportunities.

Revision ID: 005
Revises: 004
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opp_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.String, sa.ForeignKey("events.id"), nullable=False),
        sa.Column("type", sa.String, nullable=False),
        sa.Column("market", sa.String, nullable=False),
        sa.Column("outcome1", sa.String, nullable=False),
        sa.Column("point", sa.Float, nullable=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="ft"),
        sa.Column("provider1_id", sa.String, sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("odds1_at_detection", sa.Float, nullable=False),
        sa.Column("fair_odds1_at_detection", sa.Float, nullable=True),
        sa.Column("edge_pct_at_detection", sa.Float, nullable=True),
        sa.Column("provider2_id", sa.String, sa.ForeignKey("providers.id"), nullable=True),
        sa.Column("outcome2", sa.String, nullable=True),
        sa.Column("odds2_at_detection", sa.Float, nullable=True),
        sa.Column("first_detected_at", sa.DateTime, nullable=False),
        sa.Column("last_detected_at", sa.DateTime, nullable=False),
        sa.Column("detection_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("time_to_start_minutes_at_detection", sa.Float, nullable=True),
        sa.Column("provider1_closing_odds", sa.Float, nullable=True),
        sa.Column("provider1_closing_age_minutes", sa.Float, nullable=True),
        sa.Column("provider2_closing_odds", sa.Float, nullable=True),
        sa.Column("provider2_closing_age_minutes", sa.Float, nullable=True),
        sa.Column("pinnacle_closing_fair", sa.Float, nullable=True),
        sa.Column("pinnacle_closing_age_minutes", sa.Float, nullable=True),
        sa.Column("provider_clv_pct", sa.Float, nullable=True),
        sa.Column("pinnacle_clv_pct", sa.Float, nullable=True),
        sa.Column("closing_prob_sum", sa.Float, nullable=True),
        sa.Column("was_arb_at_close", sa.Boolean, nullable=True),
        sa.Column("clv_computed_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint(
            "event_id",
            "market",
            "outcome1",
            "provider1_id",
            "type",
            "scope",
            name="uq_opp_snapshot",
        ),
    )
    op.create_index(
        "ix_opp_snap_provider_type_first",
        "opp_snapshots",
        ["provider1_id", "type", "first_detected_at"],
    )
    op.create_index(
        "ix_opp_snap_first_detected_at",
        "opp_snapshots",
        ["first_detected_at"],
    )
    op.create_index(
        "ix_opp_snap_clv_pending",
        "opp_snapshots",
        ["event_id"],
        postgresql_where=sa.text("clv_computed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_opp_snap_clv_pending", table_name="opp_snapshots")
    op.drop_index("ix_opp_snap_first_detected_at", table_name="opp_snapshots")
    op.drop_index("ix_opp_snap_provider_type_first", table_name="opp_snapshots")
    op.drop_table("opp_snapshots")
