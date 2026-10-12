"""ジョブの用途に image_describe を加える（DOC-07 の画像の説明）

Revision ID: 0004_image_describe
Revises: 0003_feedback
Create Date: 2026-10-12
"""

from __future__ import annotations

from alembic import op

revision = "0004_image_describe"
down_revision = "0003_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as b:
        b.drop_constraint("ck_jobs_purpose", type_="check")
        b.create_check_constraint(
            "ck_jobs_purpose", "purpose IN ('agent','invoice_extract','compact','image_describe')"
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as b:
        b.drop_constraint("ck_jobs_purpose", type_="check")
        b.create_check_constraint("ck_jobs_purpose", "purpose IN ('agent','invoice_extract','compact')")
