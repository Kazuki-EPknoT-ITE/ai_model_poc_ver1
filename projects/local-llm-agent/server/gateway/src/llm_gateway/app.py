"""HTTP API（local_llm_api.md）。"""

from __future__ import annotations

import ipaddress
import logging
import statistics
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .admin_api import register_admin, register_internal
from .audit import AuditBatch
from .auth import Principal
from .db import Instruction, Job, User
from .feedback import register_feedback
from .gateway import Gateway
from .gpu import gpu_status
from .jobs import StepRequest
from .netpolicy import admin_cap, decide
from .registry import register_registry
from .relay import forward, require_upstream
from .updates import register_updates
from .users import UserCreate, UserPatch, user_view
from .util import NET_LEVELS, ApiError, new_ulid

log = logging.getLogger("llm_gateway.http")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class RequestIdMiddleware:
    """すべての応答に X-Request-Id を付ける（送られてきたら引き継ぐ）。SSE を妨げない純 ASGI 実装。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = None
        for k, v in scope.get("headers", []):
            if k == b"x-request-id":
                rid = v.decode("latin-1")[:128]
        rid = rid or new_ulid()
        scope.setdefault("state", {})["request_id"] = rid

        async def _send(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                headers = list(msg.get("headers", []))
                headers.append((b"x-request-id", rid.encode("latin-1")))
                msg["headers"] = headers
            await send(msg)

        await self.app(scope, receive, _send)


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    login: str = Field(max_length=256)
    password: str = Field(max_length=1024)
    device_name: str | None = Field(default=None, max_length=256)


class RefreshBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    refresh_token: str = Field(max_length=512)


class PasswordBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


def _err(e: ApiError) -> JSONResponse:
    return JSONResponse(e.body(), status_code=e.status)


def _gw(request: Request) -> Gateway:
    gw: Gateway = request.app.state.gw
    return gw


async def current_user(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise ApiError(401, "UNAUTHENTICATED", "ログインしてください")
    return await _gw(request).auth.principal(auth[7:].strip())


async def admin_user(p: Annotated[Principal, Depends(current_user)]) -> Principal:
    if not p.is_admin:
        raise ApiError(403, "FORBIDDEN", "管理者の権限が必要です")
    return p


UserDep = Annotated[Principal, Depends(current_user)]
AdminDep = Annotated[Principal, Depends(admin_user)]


def create_app(gw: Gateway) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await gw.start()
        try:
            yield
        finally:
            await gw.stop()

    app = FastAPI(title="local-llm-agent gateway", version=__version__, lifespan=lifespan)
    app.state.gw = gw
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:
        if exc.status == 401:
            gw.metrics.inc("llmgw_auth_failures_total", "認証の失敗数", code=exc.code)
        return _err(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        # 入力値（本文）を返さないよう、場所だけを返す
        locs = sorted({".".join(str(x) for x in e.get("loc", ())) for e in exc.errors()})
        return _err(
            ApiError(400, "INVALID_REQUEST", f"リクエストの形式が正しくありません: {', '.join(locs)}")
        )

    @app.exception_handler(Exception)
    async def _unexpected(_req: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error type=%s", type(exc).__name__)  # 本文を含みうるので型だけ
        return _err(ApiError(500, "INTERNAL", "サーバー内部のエラーです", retryable=True))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return _err(ApiError(exc.status_code, code, str(exc.detail)))

    # ------------------------------------------------------------------ 認証

    @app.post("/v1/auth/login")
    async def login(body: LoginBody) -> dict[str, Any]:
        try:
            out, uid = await gw.auth.login(body.login, body.password, body.device_name)
        except ApiError:
            await gw.audit.record(
                source="gateway", action="login", result="error:UNAUTHENTICATED", target=body.login
            )
            raise
        await gw.audit.record(source="gateway", action="login", result="ok", user_id=uid)
        return out

    @app.post("/v1/auth/refresh")
    async def refresh(body: RefreshBody) -> dict[str, Any]:
        return await gw.auth.refresh(body.refresh_token)

    @app.post("/v1/auth/logout", status_code=204)
    async def logout(body: RefreshBody) -> Response:
        await gw.auth.logout(body.refresh_token)
        return Response(status_code=204)

    @app.post("/v1/auth/password", status_code=204)
    async def change_password(body: PasswordBody, p: UserDep) -> Response:
        await gw.auth.change_password(p, body.current, body.new)
        await gw.audit.record(source="gateway", action="password.change", result="ok", user_id=p.user_id)
        return Response(status_code=204)

    # ------------------------------------------------------------------ 設定

    @app.get("/v1/config")
    async def config(p: UserDep) -> dict[str, Any]:
        prof = gw.profile
        up = gw.settings.upstreams
        engine_status = gw.jobs.engine_status()
        async with gw.db.tx() as s:
            cap, limit = await admin_cap(s, p, prof)
            ins = (
                (
                    await s.execute(
                        select(Instruction)
                        .where(Instruction.org_id == p.org_id)
                        .order_by(Instruction.version.desc())
                    )
                )
                .scalars()
                .first()
            )
        used = await gw.usage.searches_this_month(p.user_id, gw.clock.now().date())
        instructions = (
            {"version": ins.version, "body": ins.body}
            if ins is not None
            else {"version": prof.instructions.version, "body": prof.instructions_body}
        )
        return {
            "protocol_version": "1.0",
            "server_version": __version__,
            "org": {"id": prof.org.id, "name": prof.org.name},
            "models": [
                {
                    "id": m.id,
                    "label": m.label,
                    "available": engine_status.get(m.id) == "ok",
                    "context_budget": m.context_budget,
                }
                for m in gw.settings.models
            ],
            "escalation_target": prof.escalation_target,
            "net": {"level_cap": cap, "monthly_search_limit": limit, "used_this_month": used},
            "instructions": instructions,
            "invoice_schemas": prof.invoice_schemas,
            "limits": {"max_concurrent": p.max_concurrent, "max_tool_result_tokens": 6000},
            "features": {
                "rag": bool(up.rag_url) and prof.features.rag,
                "ocr": bool(up.ocr_url) and prof.features.ocr,
                "web": bool(up.web_relay_url) and prof.features.web,
            },
            "macros": [m.model_dump() for m in prof.macros],
        }

    # ------------------------------------------------------------------ 推論ジョブ

    @app.post("/v1/steps", status_code=202)
    async def submit_step(
        request: Request,
        p: UserDep,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> JSONResponse:
        if not idempotency_key:
            raise ApiError(400, "INVALID_REQUEST", "Idempotency-Key ヘッダーが必要です")
        try:
            raw = await request.json()
        except ValueError:
            raise ApiError(400, "INVALID_REQUEST", "JSON ではありません") from None
        if not isinstance(raw, dict):
            raise ApiError(400, "INVALID_REQUEST", "JSON オブジェクトが必要です")
        try:
            req = StepRequest.model_validate(raw)
        except ValidationError as e:
            locs = sorted({".".join(str(x) for x in err.get("loc", ())) for err in e.errors()})
            raise ApiError(
                400, "INVALID_REQUEST", f"リクエストの形式が正しくありません: {', '.join(locs)}"
            ) from None
        out, created = await gw.jobs.submit(p, idempotency_key, raw, req)
        return JSONResponse(out, status_code=202 if created else 200)

    @app.get("/v1/steps/{job_id}")
    async def get_step(job_id: str, p: UserDep) -> dict[str, Any]:
        return await gw.jobs.get(p, job_id)

    @app.delete("/v1/steps/{job_id}", status_code=204)
    async def cancel_step(job_id: str, p: UserDep) -> Response:
        await gw.jobs.cancel(p, job_id)
        return Response(status_code=204)

    @app.get("/v1/steps/{job_id}/events")
    async def step_events(
        job_id: str,
        p: UserDep,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        await gw.jobs.open_events(p, job_id)
        try:
            last = int(last_event_id or 0)
        except ValueError:
            last = 0
        return StreamingResponse(
            gw.jobs.events(job_id, last),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------ 監査

    @app.post("/v1/audit", status_code=204)
    async def audit(body: AuditBatch, p: UserDep) -> Response:
        await gw.audit.ingest(p.user_id, body.records)
        return Response(status_code=204)

    # ------------------------------------------------------------------ 中継（RAG・Web・OCR）

    def _relay_headers(request: Request, p: Principal) -> dict[str, str]:
        h = {"X-LLMGW-User": p.user_id, "X-LLMGW-Role": p.role}
        if p.department_id:
            h["X-LLMGW-Department"] = quote(p.department_id, safe="")  # 日本語の部門名はURLエンコード
        rid = request.scope.get("state", {}).get("request_id")
        if rid:
            h["X-Request-Id"] = rid
        return h

    async def _json_body(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise ApiError(400, "INVALID_REQUEST", "JSON ではありません") from None
        if not isinstance(body, dict):
            raise ApiError(400, "INVALID_REQUEST", "JSON オブジェクトが必要です")
        return body

    @app.post("/v1/rag/search")
    async def rag_search(request: Request, p: UserDep) -> Response:
        base = require_upstream(gw.settings.upstreams.rag_url, gw.profile.features.rag, "rag")
        body = await _json_body(request)
        async with gw.db.tx() as s:
            user = await s.get(User, p.user_id)
            assert user is not None
            groups = await gw.provider.resolve_groups(s, user)
        body["acl_groups"] = groups  # クライアントの値は使わず、サーバー側で必ず付ける（SEC-07）
        token = gw.settings.upstreams.secret("rag", gw.settings.base_dir) or ""
        res, _ = await forward(
            gw.http,
            f"{base}/v1/rag/search",
            json_body=body,
            headers=_relay_headers(request, p) | {"X-Internal-Token": token},
        )
        return res

    async def _web(request: Request, p: Principal, kind: str, needed: str) -> Response:
        base = require_upstream(gw.settings.upstreams.web_relay_url, gw.profile.features.web, "web")
        body = await _json_body(request)
        today = gw.clock.now().date()
        async with gw.db.tx() as s:
            d = await decide(s, p, gw.profile, request.headers.get("x-net-level"))
        if NET_LEVELS[d.effective] < NET_LEVELS[needed]:
            await gw.audit.record(
                source="gateway", action=f"web.{kind}", result="blocked:NET_DISABLED", user_id=p.user_id
            )
            raise ApiError(403, "NET_DISABLED", "ネットアクセスは許可されていません")
        used = 0
        if kind == "search" and d.monthly_search_limit is not None:
            used = await gw.usage.searches_this_month(p.user_id, today)
            if used >= d.monthly_search_limit:
                await gw.audit.record(
                    source="gateway", action="web.search", result="blocked:QUOTA_EXCEEDED", user_id=p.user_id
                )
                raise ApiError(
                    429, "QUOTA_EXCEEDED", "今月の検索回数の上限に達しました。ネットなしで続けてください"
                )
        secret = gw.settings.upstreams.secret("web_relay", gw.settings.base_dir)
        if not secret:
            raise ApiError(503, "FEATURE_UNAVAILABLE", "Web 中継の内部トークンが設定されていません")
        headers = _relay_headers(request, p) | {"X-Relay-Secret": secret}
        # 認証・ネットの上限・回数は Gateway が判定済み。Relay には実効の level を渡し、回数は二重に数えない
        body = {
            **body,
            "user_id": p.user_id,
            "org_id": gw.profile.org.id,
            "level": d.effective,
            "monthly_search_limit": None,
        }
        res, parsed = await forward(gw.http, f"{base}/{kind}", json_body=body, headers=headers)
        if 200 <= res.status_code < 300:
            if kind == "search":
                await gw.usage.add(p.user_id, today, web_searches=1)
                if d.monthly_search_limit is not None and isinstance(parsed, dict):
                    parsed["remaining_quota"] = max(0, d.monthly_search_limit - used - 1)
                    res = JSONResponse(parsed, status_code=res.status_code)
            else:
                await gw.usage.add(p.user_id, today, web_fetches=1)
        gw.metrics.inc(
            "llmgw_relay_total", "中継したリクエスト数", kind=f"web.{kind}", status=str(res.status_code)
        )
        return res

    @app.post("/v1/web/search")
    async def web_search(request: Request, p: UserDep) -> Response:
        return await _web(request, p, "search", "search")

    @app.post("/v1/web/fetch")
    async def web_fetch(request: Request, p: UserDep) -> Response:
        return await _web(request, p, "fetch", "browse")

    @app.post("/v1/vision/ocr")
    async def vision_ocr(request: Request, p: UserDep) -> Response:
        base = require_upstream(gw.settings.upstreams.ocr_url, gw.profile.features.ocr, "ocr")
        size = int(request.headers.get("content-length") or 0)
        if size > 21 * 1024 * 1024:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "画像が大きすぎます（20MB まで）")
        content = await request.body()  # メモリ上だけで扱い、ディスクには書かない
        if len(content) > 21 * 1024 * 1024:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "画像が大きすぎます（20MB まで）")
        res, _ = await forward(
            gw.http,
            f"{base}/v1/vision/ocr",
            content=content,
            content_type=request.headers.get("content-type"),
            headers=_relay_headers(request, p)
            | {"X-Internal-Token": gw.settings.upstreams.secret("ocr", gw.settings.base_dir) or ""},
        )
        return res

    # ------------------------------------------------------------------ 死活・メトリクス

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        db_ok = await gw.db_ok()
        engines = {}
        for mid, eng in gw.engines.items():
            try:
                engines[mid] = "ok" if await eng.health() else "unavailable"
            except Exception:
                engines[mid] = "unavailable"
        up = gw.settings.upstreams
        body = {
            "engine": engines,
            "db": "ok" if db_ok else "unavailable",
            "services": {
                "rag": "configured" if up.rag_url else "disabled",
                "web": "configured" if up.web_relay_url else "disabled",
                "ocr": "configured" if up.ocr_url else "disabled",
            },
        }
        return JSONResponse(body, status_code=200 if db_ok else 503)

    nets = [ipaddress.ip_network(c) for c in gw.settings.metrics.allow_cidrs]

    @app.get("/metrics")
    async def metrics(request: Request) -> PlainTextResponse:
        host = request.client.host if request.client else ""
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            addr = None
        if addr is None or not any(addr in n for n in nets):
            raise ApiError(403, "FORBIDDEN", "内部ネットワークからのみ参照できます")
        return PlainTextResponse(gw.metrics.render(), media_type="text/plain; version=0.0.4")

    # ------------------------------------------------------------------ 管理 API（最小）

    @app.get("/admin/v1/users")
    async def admin_users(_p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            rows = (await s.execute(select(User).order_by(User.login))).scalars().all()
            return {"users": [user_view(u) for u in rows]}

    @app.post("/admin/v1/users", status_code=201)
    async def admin_create_user(body: UserCreate, p: AdminDep) -> JSONResponse:
        async with gw.db.tx() as s:
            user = await gw.users.create(s, body)
            view = user_view(user)
        await gw.audit.record(
            source="admin", action="admin.user.create", result="ok", user_id=p.user_id, target=view["id"]
        )
        return JSONResponse(view, status_code=201)

    @app.patch("/admin/v1/users/{user_id}")
    async def admin_patch_user(user_id: str, body: UserPatch, p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            user = await gw.users.patch(s, gw.auth, user_id, body)
            view = user_view(user)
        fields = sorted(k for k, v in body.model_dump().items() if v is not None)
        await gw.audit.record(
            source="admin",
            action="admin.user.update",
            result="ok",
            user_id=p.user_id,
            target=user_id,
            detail={"fields": fields},
        )
        return view

    @app.get("/admin/v1/dashboard")
    async def admin_dashboard(_p: AdminDep, hours: int = 24) -> dict[str, Any]:
        now = gw.clock.now()
        since = now - timedelta(hours=max(1, min(hours, 24 * 31)))
        async with gw.db.tx() as s:
            q_window = select(Job.state, func.count()).where(Job.enqueued_at >= since).group_by(Job.state)
            by_state = {st: int(n) for st, n in (await s.execute(q_window)).all()}
            q_live = (
                select(Job.state, func.count())
                .where(Job.state.in_(("queued", "running")))
                .group_by(Job.state)
            )
            live = {st: int(n) for st, n in (await s.execute(q_live)).all()}
            rows = (
                await s.execute(
                    select(Job.enqueued_at, Job.started_at, Job.finished_at)
                    .where(Job.state == "done", Job.finished_at >= since, Job.started_at.is_not(None))
                    .order_by(Job.finished_at.desc())
                    .limit(10000)
                )
            ).all()
            users_total = (await s.execute(select(func.count()).select_from(User))).scalar_one()
            active = (
                await s.execute(
                    select(func.count(func.distinct(Job.user_id))).where(Job.enqueued_at >= since)
                )
            ).scalar_one()
        spans = [(enq, st, fin) for enq, st, fin in rows if st is not None and fin is not None]
        wait = [(st - enq).total_seconds() for enq, st, _ in spans]
        proc = [(fin - st).total_seconds() for _, st, fin in spans]
        total = [(fin - enq).total_seconds() for enq, _, fin in spans]
        finished = sum(int(by_state.get(k, 0)) for k in ("done", "failed", "cancelled"))
        total_q = _quantiles(total)
        return {
            # API 仕様 §9.1（管理画面が使う項目）
            "jobs_today": sum(int(v) for v in by_state.values()),
            "jobs_running": int(live.get("running", 0)),
            "queue_depth": int(live.get("queued", 0)),
            "errors_today": int(by_state.get("failed", 0)),
            "users_active_today": int(active),
            "latency_ms": {
                "p50": round(total_q["p50"] * 1000) if total_q["p50"] is not None else None,
                "p95": round(total_q["p95"] * 1000) if total_q["p95"] is not None else None,
            },
            # 詳しい項目
            "window_hours": int((now - since).total_seconds() // 3600),
            "jobs": {
                k: int(by_state.get(k, 0)) for k in ("queued", "running", "done", "failed", "cancelled")
            },
            "queue": {
                "queued": int(live.get("queued", 0)),
                "running": int(live.get("running", 0)),
                "by_model": dict(gw.jobs.queue_depth),
            },
            "latency_seconds": {
                "queue_wait": _quantiles(wait),
                "processing": _quantiles(proc),
                "total": _quantiles(total),
            },
            "error_rate": (int(by_state.get("failed", 0)) / finished) if finished else 0.0,
            "users": {"total": int(users_total), "active": int(active)},
            "engines": gw.jobs.engine_status(),
            "gpu": await gpu_status(gw),  # dcgm-exporter から（metrics.dcgm_url。無ければ空）
        }

    register_admin(app, gw, Depends(admin_user))
    register_registry(app, gw, Depends(admin_user))
    register_feedback(app, gw, Depends(current_user), Depends(admin_user))
    register_updates(app, gw, Depends(current_user))
    register_internal(app, gw)

    if gw.settings.admin_web_dir:
        admin_dir = gw.settings.resolve(gw.settings.admin_web_dir)
        if admin_dir.is_dir():
            from fastapi.staticfiles import StaticFiles  # noqa: PLC0415

            app.mount("/admin", StaticFiles(directory=admin_dir, html=True), name="admin-web")

    return app


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "count": 0}
    if len(values) == 1:
        v = round(values[0], 3)
        return {"p50": v, "p95": v, "p99": v, "count": 1}
    q = statistics.quantiles(values, n=100, method="inclusive")
    return {"p50": round(q[49], 3), "p95": round(q[94], 3), "p99": round(q[98], 3), "count": len(values)}
