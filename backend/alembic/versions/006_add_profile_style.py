"""Add style column to profiles (Stats per-profile account styles).

Revision ID: 006
Revises: 005
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("style", sa.String(), nullable=False, server_default="personal"))


def downgrade() -> None:
    op.drop_column("profiles", "style")
