"""機密語の読み（dlp_terms.readings）。Web Relay の表記ゆれの検出に使う（T-61 の提案）

Revision ID: 0002_dlp_readings
Revises: 0001_initial
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_dlp_readings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("dlp_terms") as b:
        b.add_column(sa.Column("readings", JSONB, server_default=sa.text("'[]'"), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("dlp_terms") as b:
        b.drop_column("readings")
