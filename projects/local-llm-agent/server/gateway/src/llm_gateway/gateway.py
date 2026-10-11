"""Gateway の部品をまとめる（設定・DB・認証・ジョブ・中継）。"""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text

from .audit import AuditLog
from .auth import AuthService, LocalAuthProvider, TokenService
from .config import Profile, Settings
from .crypto import PayloadCipher
from .db import Database, make_engine
from .engines import InferenceEngine, build_engine
from .jobs import JobService
from .metrics import Metrics
from .migrate import upgrade
from .usage import UsageRecorder
from .users import UserAdmin, ensure_org
from .util import Clock

log = logging.getLogger("llm_gateway")

InitDb = Literal["migrate", "create_all", "none"]


class Gateway:
    def __init__(
        self,
        settings: Settings,
        *,
        profile: Profile,
        signing_key: Ed25519PrivateKey,
        cipher: PayloadCipher,
        db: Database | None = None,
        engines: dict[str, InferenceEngine] | None = None,
        clock: Clock | None = None,
        upstream_transport: httpx.AsyncBaseTransport | None = None,
        init_db: InitDb | None = None,
        background: bool = True,
    ) -> None:
        self.settings = settings
        self.profile = profile
        self.clock = clock or Clock()
        self.db = db or Database(make_engine(settings.database_url(), pool_size=settings.database.pool_size))
        self._owns_db = db is None
        self.cipher = cipher
        self.metrics = Metrics()
        self.engines: dict[str, InferenceEngine] = engines or {
            m.id: build_engine(m.engine) for m in settings.models
        }
        missing = [m.id for m in settings.models if m.id not in self.engines]
        if missing:
            raise ValueError(f"エンジンがないモデルがあります: {missing}")
        self.provider = LocalAuthProvider(settings.auth, self.clock)
        self.tokens = TokenService(signing_key, settings.auth, self.clock)
        self.auth = AuthService(self.db, self.provider, self.tokens, settings.auth, self.clock)
        self.usage = UsageRecorder(self.db)
        self.audit = AuditLog(self.db, self.clock)
        self.users = UserAdmin(self.provider, settings.auth, profile, self.clock)
        self.jobs = JobService(settings, self.db, cipher, self.engines, self.clock, self.metrics, self.usage)
        self.http = httpx.AsyncClient(timeout=settings.upstreams.timeout_s, transport=upstream_transport)
        self.init_db: InitDb = init_db or ("migrate" if settings.database.auto_migrate else "none")
        self.background = background
        self.metrics.gauge(
            "llmgw_queue_depth",
            "待っているジョブ数（モデル別）",
            lambda: {(("model", m),): float(self.jobs.queue_depth.get(m, 0)) for m in self.jobs.models},
        )
        self.metrics.gauge(
            "llmgw_running_jobs", "実行中のジョブ数", lambda: {(): float(self.jobs.running_count)}
        )
        self.metrics.gauge(
            "llmgw_engine_up",
            "推論エンジンが使えるか（1/0）",
            lambda: {
                (("model", m),): 1.0 if st == "ok" else 0.0 for m, st in self.jobs.engine_status().items()
            },
        )

    async def start(self) -> None:
        if self.init_db == "migrate":
            await upgrade(self.db.engine)
        elif self.init_db == "create_all":
            await self.db.create_all()
        async with self.db.tx() as s:
            await ensure_org(s, self.profile)
        await self.jobs.start(background=self.background)
        from .registry import apply_production  # noqa: PLC0415 - 循環 import を避ける

        await apply_production(self)
        log.info("gateway started models=%s", [m.id for m in self.settings.models])

    async def stop(self) -> None:
        await self.jobs.stop()
        for eng in self.engines.values():
            await eng.aclose()
        await self.http.aclose()
        if self._owns_db:
            await self.db.dispose()

    async def db_ok(self) -> bool:
        try:
            async with self.db.tx() as s:
                await s.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
