"""PostgreSQL 16 のテーブル（local_llm_data_model.md §3）。

試験は SQLite（aiosqlite）でも動くよう、方言に依存しない型で書く:
- jsonb / text[] → JSON（PostgreSQL では JSONB）
- timestamptz → UTCDateTime（SQLite では UTC の naive 値で保存し、読み出しで UTC を付ける）
- bigserial → BigInteger（SQLite では INTEGER PRIMARY KEY）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    TypeDecorator,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")
BigId = BigInteger().with_variant(Integer(), "sqlite")


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    profile: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("orgs.id"))
    name: Mapped[str] = mapped_column(Text)
    acl_groups: Mapped[list[str]] = mapped_column(JsonType, default=list)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user','admin')", name="role"),
        CheckConstraint("auth_provider IN ('local','oidc','ldap')", name="auth_provider"),
        CheckConstraint("net_level_cap IN ('off','search','browse')", name="net_level_cap"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("orgs.id"))
    department_id: Mapped[str | None] = mapped_column(Text, ForeignKey("departments.id"))
    login: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    auth_provider: Mapped[str] = mapped_column(Text, default="local")
    password_hash: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text, unique=True)
    extra_acl_groups: Mapped[list[str]] = mapped_column(JsonType, default=list)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=2)
    net_level_cap: Mapped[str] = mapped_column(Text, default="off")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class Session(Base):
    """リフレッシュトークン1つ = 1行。同じログインから続く系列は family_id でまとめる。"""

    __tablename__ = "sessions"
    __table_args__ = (Index("sessions_family", "family_id"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"))
    family_id: Mapped[str] = mapped_column(Text)
    refresh_hash: Mapped[str] = mapped_column(Text, unique=True)
    device_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # rotated（使って交換済み）| logout | reuse（再利用を検知）| password | admin
    revoked_reason: Mapped[str | None] = mapped_column(Text)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("state IN ('queued','running','done','failed','cancelled')", name="state"),
        CheckConstraint("purpose IN ('agent','invoice_extract','compact')", name="purpose"),
        Index(
            "jobs_queue",
            "state",
            "priority",
            "enqueued_at",
            postgresql_where="state IN ('queued','running')",
            sqlite_where="state IN ('queued','running')",
        ),
        Index("jobs_finished", "finished_at"),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    body_hash: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"))
    model: Mapped[str] = mapped_column(Text)
    lora: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, default="agent")
    priority: Mapped[int] = mapped_column(SmallInteger, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(Text)
    enqueued_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    requeue_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)  # エラーコードのみ（本文は入れない）
    result_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class JobPayload(Base):
    __tablename__ = "job_payloads"
    job_id: Mapped[str] = mapped_column(Text, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    key_id: Mapped[str] = mapped_column(Text)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    request_ct: Mapped[bytes | None] = mapped_column(LargeBinary)  # 完了・取り消しで直ちに消す（ADR-007）
    response_ct: Mapped[bytes | None] = mapped_column(LargeBinary)


class JobEvent(Base):
    __tablename__ = "job_events"
    job_id: Mapped[str] = mapped_column(Text, ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)  # queue | started | token | requeued | done | error
    data_ct: Mapped[bytes | None] = mapped_column(LargeBinary)


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    jobs: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    web_searches: Mapped[int] = mapped_column(Integer, default=0)
    web_fetches: Mapped[int] = mapped_column(Integer, default=0)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("source IN ('client','gateway','web_relay','admin')", name="source"),
        Index("audit_at", "at"),
    )
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    client_id: Mapped[str | None] = mapped_column(Text, unique=True)  # クライアントの記録 ID（重複除去）
    at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    user_id: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JsonType)


class NetPolicy(Base):
    __tablename__ = "net_policies"
    __table_args__ = (CheckConstraint("level_cap IN ('off','search','browse')", name="level_cap"),)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)  # 'org' | 'department:<id>'
    level_cap: Mapped[str] = mapped_column(Text)
    monthly_search_limit: Mapped[int | None] = mapped_column(Integer)


class NetSite(Base):
    __tablename__ = "net_sites"
    __table_args__ = (CheckConstraint("kind IN ('block','trusted')", name="kind"),)
    pattern: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)


class DlpTerm(Base):
    __tablename__ = "dlp_terms"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("orgs.id"))
    term: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    __table_args__ = (CheckConstraint("status IN ('candidate','production','retired')", name="status"),)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    base_model: Mapped[str] = mapped_column(Text)
    quant: Mapped[str] = mapped_column(Text)
    lora: Mapped[str | None] = mapped_column(Text)
    train_data_hash: Mapped[str | None] = mapped_column(Text)
    protocol_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    serve_config: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    promoted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class EvalResult(Base):
    __tablename__ = "eval_results"
    model_id: Mapped[str] = mapped_column(Text, ForeignKey("model_registry.id"), primary_key=True)
    eval_set: Mapped[str] = mapped_column(Text, primary_key=True)
    metric: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[float] = mapped_column(Numeric)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True, server_default=func.now())


class Instruction(Base):
    __tablename__ = "instructions"
    org_id: Mapped[str] = mapped_column(Text, ForeignKey("orgs.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


# ---- 接続 ----


def make_engine(url: str, *, pool_size: int = 10) -> AsyncEngine:
    if url.startswith("sqlite"):
        engine = create_async_engine(url, connect_args={"timeout": 30})

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_connect(dbapi_conn: Any, _rec: Any) -> None:
            # トランザクションの開始は下の begin で明示する（遅延ロックによる SQLITE_BUSY を避ける）
            dbapi_conn.isolation_level = None
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        @event.listens_for(engine.sync_engine, "begin")
        def _sqlite_begin(conn: Any) -> None:
            conn.exec_driver_sql("BEGIN IMMEDIATE")

        return engine
    return create_async_engine(url, pool_size=pool_size, pool_pre_ping=True)


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[AsyncSession]:
        """短いトランザクション。抜けるときにコミットする。"""
        async with self._maker() as s, s.begin():
            yield s

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
