"""初期スキーマ（local_llm_data_model.md §3.2）

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "orgs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("profile", JSONB, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_orgs"),
    )
    op.create_table(
        "departments",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("acl_groups", JSONB, nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_departments_org_id_orgs"),
        sa.PrimaryKeyConstraint("id", name="pk_departments"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("department_id", sa.Text(), nullable=True),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("auth_provider", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("extra_acl_groups", JSONB, nullable=False),
        sa.Column("max_concurrent", sa.Integer(), nullable=False),
        sa.Column("net_level_cap", sa.Text(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("failed_logins", sa.Integer(), nullable=False),
        sa.Column("locked_until", TS, nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('user','admin')", name="ck_users_role"),
        sa.CheckConstraint("auth_provider IN ('local','oidc','ldap')", name="ck_users_auth_provider"),
        sa.CheckConstraint("net_level_cap IN ('off','search','browse')", name="ck_users_net_level_cap"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_users_org_id_orgs"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_users_department_id_departments"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("login", name="uq_users_login"),
        sa.UniqueConstraint("external_id", name="uq_users_external_id"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("family_id", sa.Text(), nullable=False),
        sa.Column("refresh_hash", sa.Text(), nullable=False),
        sa.Column("device_name", sa.Text(), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("refresh_hash", name="uq_sessions_refresh_hash"),
    )
    op.create_index("sessions_family", "sessions", ["family_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("body_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("lora", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("enqueued_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", TS, nullable=True),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("requeue_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_expires_at", TS, nullable=True),
        sa.CheckConstraint("state IN ('queued','running','done','failed','cancelled')", name="ck_jobs_state"),
        sa.CheckConstraint("purpose IN ('agent','invoice_extract','compact')", name="ck_jobs_purpose"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_jobs_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
    )
    op.create_index(
        "jobs_queue",
        "jobs",
        ["state", "priority", "enqueued_at"],
        postgresql_where=sa.text("state IN ('queued','running')"),
        sqlite_where=sa.text("state IN ('queued','running')"),
    )
    op.create_index("jobs_finished", "jobs", ["finished_at"])
    op.create_table(
        "job_payloads",
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("request_ct", sa.LargeBinary(), nullable=True),
        sa.Column("response_ct", sa.LargeBinary(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_job_payloads_job_id_jobs", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("job_id", name="pk_job_payloads"),
    )
    op.create_table(
        "job_events",
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("data_ct", sa.LargeBinary(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_job_events_job_id_jobs", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("job_id", "seq", name="pk_job_events"),
    )
    op.create_table(
        "usage_daily",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("jobs", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("web_searches", sa.Integer(), nullable=False),
        sa.Column("web_fetches", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_usage_daily_user_id_users"),
        sa.PrimaryKeyConstraint("user_id", "day", name="pk_usage_daily"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", BIGID, autoincrement=True, nullable=False),
        sa.Column("client_id", sa.Text(), nullable=True),
        sa.Column("at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("detail", JSONB, nullable=True),
        sa.CheckConstraint(
            "source IN ('client','gateway','web_relay','admin')", name="ck_audit_events_source"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.UniqueConstraint("client_id", name="uq_audit_events_client_id"),
    )
    op.create_index("audit_at", "audit_events", ["at"])
    op.create_table(
        "net_policies",
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("level_cap", sa.Text(), nullable=False),
        sa.Column("monthly_search_limit", sa.Integer(), nullable=True),
        sa.CheckConstraint("level_cap IN ('off','search','browse')", name="ck_net_policies_level_cap"),
        sa.PrimaryKeyConstraint("scope", name="pk_net_policies"),
    )
    op.create_table(
        "net_sites",
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('block','trusted')", name="ck_net_sites_kind"),
        sa.PrimaryKeyConstraint("pattern", name="pk_net_sites"),
    )
    op.create_table(
        "dlp_terms",
        sa.Column("id", BIGID, autoincrement=True, nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_dlp_terms_org_id_orgs"),
        sa.PrimaryKeyConstraint("id", name="pk_dlp_terms"),
    )
    op.create_table(
        "model_registry",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("base_model", sa.Text(), nullable=False),
        sa.Column("quant", sa.Text(), nullable=False),
        sa.Column("lora", sa.Text(), nullable=True),
        sa.Column("train_data_hash", sa.Text(), nullable=True),
        sa.Column("protocol_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("serve_config", JSONB, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("promoted_at", TS, nullable=True),
        sa.CheckConstraint("status IN ('candidate','production','retired')", name="ck_model_registry_status"),
        sa.PrimaryKeyConstraint("id", name="pk_model_registry"),
    )
    op.create_table(
        "eval_results",
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("eval_set", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("run_at", TS, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_id"], ["model_registry.id"], name="fk_eval_results_model_id_model_registry"
        ),
        sa.PrimaryKeyConstraint("model_id", "eval_set", "metric", "run_at", name="pk_eval_results"),
    )
    op.create_table(
        "instructions",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("published_at", TS, server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], name="fk_instructions_org_id_orgs"),
        sa.PrimaryKeyConstraint("org_id", "version", name="pk_instructions"),
    )


def downgrade() -> None:
    for name in (
        "instructions",
        "eval_results",
        "model_registry",
        "dlp_terms",
        "net_sites",
        "net_policies",
        "audit_events",
        "usage_daily",
        "job_events",
        "job_payloads",
        "jobs",
        "sessions",
        "users",
        "departments",
        "orgs",
    ):
        op.drop_table(name)
