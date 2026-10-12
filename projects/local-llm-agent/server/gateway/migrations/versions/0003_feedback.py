"""評価ボタン（ADM-06）: feedback テーブル

Revision ID: 0003_feedback
Revises: 0002_dlp_readings
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_feedback"
down_revision = "0002_dlp_readings"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload_ct", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", TS, nullable=True),
        sa.CheckConstraint("rating IN ('good','bad')", name="ck_feedback_rating"),
        sa.CheckConstraint("status IN ('none','pending','approved','rejected')", name="ck_feedback_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_feedback_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
        sa.UniqueConstraint("client_id", name="uq_feedback_client_id"),
    )
    op.create_index("feedback_status", "feedback", ["status"])


def downgrade() -> None:
    op.drop_index("feedback_status", table_name="feedback")
    op.drop_table("feedback")
