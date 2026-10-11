"""FastAPI アプリ（内部 API: Gateway からのみ呼ばれる）。

- `POST /search`, `POST /fetch`: ヘッダー `X-Relay-Secret` に共有シークレットが必要（無い・違うと 401）。
- `GET /healthz`: 認証なし（死活監視用。中身は返さない）。
- OpenAPI・ドキュメントの画面は公開しない。
- 入力の検証エラーは入力値を返さない（検索語・URL が応答やログに残らないように）。
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__, errors
from .audit import AuditSink, JsonlAuditSink, MemoryAuditSink
from .config import Settings
from .fetcher import Dialer, Fetcher
from .models import FetchRequest, FetchResponse, SearchRequest, SearchResponse
from .netguard import Resolver, SystemResolver, UrlGuard
from .policy import PolicyStore
from .policy_sync import PolicySync
from .search import SearchProvider, build_provider
from .service import Clock, RelayService, _utcnow
from .usage import MemoryUsageStore, SqliteUsageStore, UsageStore

SECRET_HEADER = "X-Relay-Secret"  # noqa: S105 - ヘッダー名であり秘密の値ではない
_PUBLIC_PATHS = frozenset({"/healthz"})


# 外部ライブラリのログは URL（検索語を含むクエリ）や取得先のホスト名を出すので、WARNING 以上に絞る
_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "trafilatura",
    "htmldate",
    "courlan",
    "justext",
    "pdfminer",
    "pdfplumber",
)


def quiet_third_party_loggers() -> None:
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


@dataclass
class Components:
    service: RelayService
    policy: PolicyStore
    audit: AuditSink
    usage: UsageStore
    provider: SearchProvider
    sync: PolicySync | None = None


def build_components(
    settings: Settings,
    *,
    resolver: Resolver | None = None,
    dialer: Dialer | None = None,
    provider: SearchProvider | None = None,
    audit: AuditSink | None = None,
    usage: UsageStore | None = None,
    clock: Clock = _utcnow,
) -> Components:
    policy = PolicyStore(settings.policy)
    guard = UrlGuard(
        allowed_ports=settings.fetch.allowed_ports,
        extra_blocked_networks=settings.policy.blocked_networks,
        resolver=resolver or SystemResolver(settings.fetch.connect_timeout_s),
    )
    fetcher = Fetcher(settings.fetch, guard, dialer=dialer)
    if audit is None:
        audit = MemoryAuditSink() if settings.audit.kind == "memory" else JsonlAuditSink(settings.audit.path)
    if usage is None:
        usage = (
            MemoryUsageStore() if settings.usage.kind == "memory" else SqliteUsageStore(settings.usage.path)
        )
    if provider is None:
        provider = build_provider(settings.search)
    service = RelayService(
        settings,
        policy=policy,
        guard=guard,
        fetcher=fetcher,
        provider=provider,
        usage=usage,
        audit=audit,
        clock=clock,
    )
    return Components(service=service, policy=policy, audit=audit, usage=usage, provider=provider)


def create_app(settings: Settings, secret: str, components: Components | None = None) -> FastAPI:
    if len(secret) < 16:
        raise ValueError("shared secret must be at least 16 characters")
    quiet_third_party_loggers()
    comps = components or build_components(settings)
    if comps.sync is None and settings.policy_sync.gateway_url:
        comps.sync = PolicySync(settings.policy, settings.policy_sync, comps.policy, secret)
    sync = comps.sync
    secret_b = secret.encode("utf-8")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if sync is None:
            yield
            return
        stop = asyncio.Event()
        task = asyncio.create_task(sync.run(stop))
        try:
            yield
        finally:
            stop.set()
            await task
            await sync.aclose()

    app = FastAPI(
        title="web-relay",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.components = comps

    @app.middleware("http")
    async def require_secret(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path not in _PUBLIC_PATHS:
            given = request.headers.get(SECRET_HEADER, "").encode("utf-8")
            if not hmac.compare_digest(given, secret_b):
                e = errors.unauthenticated()
                return JSONResponse(e.to_body(), status_code=e.status)
        response = await call_next(request)
        rid = request.headers.get("X-Request-Id")
        if rid and len(rid) <= 128:
            response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(errors.RelayError)
    async def relay_error(_request: Request, exc: errors.RelayError) -> JSONResponse:
        return JSONResponse(exc.to_body(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in err.get("loc", ())[1:]) for err in exc.errors()})
        e = errors.invalid_request("リクエストの形式が正しくありません: " + ", ".join(f for f in fields if f))
        return JSONResponse(e.to_body(), status_code=e.status)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    def require_policy() -> None:
        if sync is not None and not sync.ready():
            raise errors.policy_unavailable()

    @app.post("/search", response_model=SearchResponse)
    async def search(body: SearchRequest) -> SearchResponse:
        require_policy()
        return await comps.service.search(body)

    @app.post("/fetch", response_model=FetchResponse)
    async def fetch(body: FetchRequest) -> FetchResponse:
        require_policy()
        return await comps.service.fetch(body)

    return app
