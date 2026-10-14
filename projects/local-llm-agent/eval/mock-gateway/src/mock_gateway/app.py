"""モック Gateway の HTTP API（local_llm_api.md と同じ形）。"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .admin import AdminState, admin_router
from .engine import HANG, Engine, GatewayError, Job
from .scenario import Scenario

# /mock/feedback は試験用の確認口（モックだけ）
PUBLIC_PATHS = {
    "/healthz",
    "/readyz",
    "/v1/auth/login",
    "/v1/auth/refresh",
    "/docs",
    "/openapi.json",
    "/mock/feedback",
}


def is_public(path: str) -> bool:
    # 管理画面の静的ファイルは公開（API は /admin/v1 で認証する）
    return path in PUBLIC_PATHS or (path.startswith("/admin") and not path.startswith("/admin/v1"))


class Recorder:
    """受け取ったリクエストを JSON Lines で記録する（試験での照合用）。本文は記録しない。"""

    def __init__(self, path: Path | None):
        self.path = path
        self.lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _schema_tools(schema: Any) -> list[str]:
    """Action のスキーマ（oneOf）で tool に const が付いた変種のツール名。"""
    if not isinstance(schema, dict):
        return []
    names = []
    for v in schema.get("oneOf") or []:
        tool = ((v or {}).get("properties") or {}).get("tool") or {}
        if isinstance(tool, dict) and isinstance(tool.get("const"), str):
            names.append(tool["const"])
    return sorted(names)


def create_app(
    scenario: Scenario,
    *,
    record_path: Path | None = None,
    samples_path: Path | None = None,
    ping_interval_s: float = 15.0,
    result_ttl_s: float = 600.0,
) -> FastAPI:
    engine = Engine(scenario, result_ttl_s=result_ttl_s)
    recorder = Recorder(record_path)
    # 学習データの合成（ml/datagen、T-52）: 推論の入力（Runtime が組み立てた messages）と、返した出力の組を残す
    samples = Recorder(samples_path)
    tokens: dict[str, str] = {}  # access_token → login
    refresh: dict[str, str] = {}  # refresh_token → login（使ったら失効）
    counter = itertools.count(1)
    users = {u.login: u for u in scenario.users}

    app = FastAPI(title="local-llm-agent mock gateway", version="0.1.0")
    app.state.engine = engine
    admin_state = AdminState(scenario)
    app.state.admin = admin_state

    @app.exception_handler(GatewayError)
    async def _gateway_error(_req: Request, exc: GatewayError) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.middleware("http")
    async def _auth_and_record(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or f"mock-{next(counter)}"
        record: dict[str, Any] = {
            "ts": time.time(),
            "method": request.method,
            "path": request.url.path,
            "idempotency_key": request.headers.get("idempotency-key"),
            "last_event_id": request.headers.get("last-event-id"),
            "net_level": request.headers.get("x-net-level"),
            "authorized": False,
        }
        if not is_public(request.url.path):
            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            if token not in tokens:
                record["status"] = 401
                recorder.write(record)
                return JSONResponse(
                    {
                        "error": {
                            "code": "UNAUTHENTICATED",
                            "message": "ログインしてください",
                            "retryable": False,
                        }
                    },
                    status_code=401,
                    headers={"X-Request-Id": rid},
                )
            request.state.user = tokens[token]
            record["authorized"] = True
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        record["status"] = response.status_code
        recorder.write(record)
        return response

    def _issue(login: str) -> dict[str, Any]:
        n = next(counter)
        at, rt = f"mock-at-{n}", f"mock-rt-{n}"
        tokens[at] = login
        refresh[rt] = login
        u = users[login]
        return {
            "access_token": at,
            "refresh_token": rt,
            "expires_in": 900,
            "user": {
                "id": f"u-{login}",
                "display_name": u.display_name,
                "role": u.role,
                "department": u.department,
            },
        }

    app.include_router(
        admin_router(admin_state, lambda login: users.get(login) is not None and users[login].role == "admin")
    )

    # 管理画面（apps/admin-web のビルド結果）があれば /admin で配る（本物の Gateway と同じ配置）
    admin_dist = Path(__file__).resolve().parents[4] / "apps" / "admin-web" / "dist"
    if admin_dist.exists():
        from fastapi.staticfiles import StaticFiles  # noqa: PLC0415

        app.mount("/admin", StaticFiles(directory=admin_dist, html=True), name="admin")

    # ---- 認証 ----
    @app.post("/v1/auth/login")
    async def login(body: dict[str, Any]) -> dict[str, Any]:
        u = users.get(str(body.get("login", "")))
        if u is None or u.password != body.get("password"):
            raise GatewayError(401, "UNAUTHENTICATED", "ログインIDまたはパスワードが違います")
        return _issue(u.login)

    @app.post("/v1/auth/refresh")
    async def do_refresh(body: dict[str, Any]) -> dict[str, Any]:
        login_name = refresh.pop(str(body.get("refresh_token", "")), None)
        if login_name is None:
            raise GatewayError(401, "UNAUTHENTICATED", "リフレッシュトークンが無効です")
        return _issue(login_name)

    @app.post("/v1/auth/logout", status_code=204)
    async def logout(body: dict[str, Any]) -> Response:
        refresh.pop(str(body.get("refresh_token", "")), None)
        return Response(status_code=204)

    # ---- 設定・死活 ----
    @app.get("/v1/config")
    async def config() -> dict[str, Any]:
        return engine.config()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        avail = {m["id"]: ("ok" if m["available"] else "unavailable") for m in engine.config()["models"]}
        return {"engine": avail, "db": "ok", "scenario": scenario.name}

    # ---- 推論ジョブ ----
    @app.post("/v1/steps", status_code=202)
    async def submit_step(request: Request) -> JSONResponse:
        key = request.headers.get("idempotency-key")
        if not key:
            raise GatewayError(400, "INVALID_REQUEST", "Idempotency-Key ヘッダーが必要です")
        try:
            body = await request.json()
        except ValueError:
            raise GatewayError(400, "INVALID_REQUEST", "JSON ではありません") from None
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            raise GatewayError(400, "INVALID_REQUEST", "messages（配列）が必要です")
        job, created = engine.submit(key=key, user=request.state.user, body=body)
        if created:
            samples.write(
                {
                    "purpose": job.purpose,
                    "index": job.index,
                    "model": job.model,
                    "messages": body.get("messages"),
                    "constrained": bool((body.get("response_format") or {}).get("schema")),
                    # 文法で選べるツール（Runtime が繰り返し失敗したツールを外したかの確認用）
                    "schema_tools": _schema_tools((body.get("response_format") or {}).get("schema")),
                    "output": job.output,
                }
            )
        first = job.events[0]
        position = first.data.get("position", 0) if first.kind == "queue" else 0
        return JSONResponse(
            {
                "job_id": job.id,
                "state": job.state(time.monotonic()),
                "position": position,
                "eta_seconds": position * 2,
            },
            status_code=202 if created else 200,
        )

    @app.get("/v1/steps/{job_id}")
    async def get_step(job_id: str) -> dict[str, Any]:
        job = engine.get(job_id)
        state = job.state(time.monotonic())
        out: dict[str, Any] = {"job_id": job.id, "state": state}
        if state == "done":
            out.update(output=job.output, usage=job.usage)
        return out

    @app.delete("/v1/steps/{job_id}", status_code=204)
    async def cancel_step(job_id: str) -> Response:
        engine.cancel(job_id)
        return Response(status_code=204)

    @app.get("/v1/steps/{job_id}/events")
    async def step_events(job_id: str, request: Request) -> StreamingResponse:
        job = engine.get(job_id)
        try:
            last = int(request.headers.get("last-event-id") or 0)
        except ValueError:
            last = 0
        job.connections += 1
        drop_after = job.drop_after.pop(0) if job.drop_after else None
        return StreamingResponse(
            _sse(job, last, drop_after, ping_interval_s, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- 社内文書検索・Web・OCR・監査（固定応答） ----
    @app.post("/v1/rag/search")
    async def rag_search(body: dict[str, Any]) -> Any:
        return scenario.fixtures.get("rag", {"hits": []})

    @app.post("/v1/web/search")
    async def web_search(request: Request, body: dict[str, Any]) -> Any:
        _net_check(engine, request, "search")
        return scenario.fixtures.get("web_search", {"results": [], "remaining_quota": 300})

    @app.post("/v1/web/fetch")
    async def web_fetch(request: Request, body: dict[str, Any]) -> Any:
        _net_check(engine, request, "browse")
        default = {
            "url": body.get("url"),
            "final_url": body.get("url"),
            "title": "",
            "content_type": "text/html",
            "text": "",
            "chars": 0,
            "truncated": False,
        }
        # URL ごとの応答（FI-20）。{"error": {status, code, message}} なら Web Relay の失敗（502）を返す
        by_url = scenario.fixtures.get("web_fetch_by_url") or {}
        entry = by_url.get(str(body.get("url")))
        if isinstance(entry, dict) and isinstance(entry.get("error"), dict):
            err = entry["error"]
            raise GatewayError(int(err.get("status", 502)), str(err["code"]), str(err.get("message", "")))
        if isinstance(entry, dict):
            return {**default, **entry}
        return scenario.fixtures.get("web_fetch", default)

    @app.post("/v1/vision/ocr")
    async def ocr() -> Any:
        return scenario.fixtures.get(
            "ocr", {"text": "", "lines": [], "engine_used": "ocr", "confidence": 1.0}
        )

    @app.post("/v1/audit", status_code=204)
    async def audit() -> Response:
        return Response(status_code=204)

    # ---- アプリの自動更新（ADM-05。本物は server/gateway の updates.py）----
    # シナリオの fixtures.app_update: { version, notes, content }（content の文字列をインストーラーの中身にする）
    def _update() -> dict[str, Any] | None:
        u = scenario.fixtures.get("app_update")
        if not isinstance(u, dict):
            return None
        body = str(u.get("content", "fake installer")).encode()
        return {
            "version": str(u["version"]),
            "file": f"LocalLLMAgent-Setup-{u['version']}.exe",
            "sha512": hashlib.sha512(body).hexdigest(),
            "size": len(body),
            "notes": str(u.get("notes", "")),
            "published_at": "2026-10-12T00:00:00+00:00",
            "_body": body,
        }

    @app.get("/v1/app/latest")
    async def app_latest() -> Any:
        u = _update()
        return (
            {"available": False}
            if u is None
            else {"available": True, **{k: v for k, v in u.items() if k != "_body"}}
        )

    @app.get("/v1/app/download/{name}")
    async def app_download(name: str) -> Any:
        u = _update()
        if u is None or name != u["file"]:
            raise GatewayError(404, "NOT_FOUND", "配布しているファイルではありません")
        return Response(u["_body"], media_type="application/octet-stream")

    # ---- 評価ボタン（ADM-06。本物は server/gateway の feedback.py）----
    @app.post("/v1/feedback")
    async def feedback(request: Request, body: dict[str, Any]) -> Any:
        return admin_state.add_feedback(str(request.state.user), body)

    @app.get("/mock/feedback")
    async def mock_feedback() -> Any:
        """試験用: 受け取った評価（中身の件数だけ）。認証なし（モックだけの口）。"""
        return [
            {k: v for k, v in f.items() if k != "transcript"} | {"messages": len(f["transcript"])}
            for f in admin_state.feedback.values()
        ]

    return app


LEVELS = {"off": 0, "search": 1, "browse": 2}


def _net_check(engine: Engine, request: Request, needed: str) -> None:
    # 判定の順序は本物と同じ: 利用者の設定と上限 → （禁止サイト）→ DLP（local_llm_api.md §7）
    cap = str(engine.config()["net"]["level_cap"])
    client = request.headers.get("x-net-level", "off")
    effective = min(LEVELS.get(cap, 0), LEVELS.get(client, 0))
    if effective < LEVELS[needed]:
        raise GatewayError(403, "NET_DISABLED", "ネットアクセスは許可されていません")
    for kind, code in (("net_disabled", "NET_DISABLED"), ("dlp_block", "DLP_BLOCKED")):
        if engine.take_fault(kind, "agent", None) is not None:
            raise GatewayError(403, code, "（モック）注入したポリシーエラー")


def _format(seq: int, kind: str, data: dict[str, Any]) -> bytes:
    return f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _sse(
    job: Job, last: int, drop_after: int | None, ping_s: float, request: Request
) -> AsyncIterator[bytes]:
    sent = 0
    for ev in job.events:
        if ev.seq <= last:
            continue
        # 予定時刻まで待つ（待っている間は ping を送る。hang の場合はずっと ping）
        while True:
            if job.cancelled:
                yield _format(ev.seq, "error", {"code": "CANCELLED", "retryable": False})
                return
            if await request.is_disconnected():
                return
            wait = job.created + ev.at - time.monotonic()
            if wait <= 0:
                break
            if ev.at == HANG or wait > ping_s:
                await asyncio.sleep(ping_s)
                yield b": ping\n\n"
            else:
                await asyncio.sleep(wait)
        if drop_after is not None and sent >= drop_after:
            return  # 注入した切断。done を送らずに閉じる
        yield _format(ev.seq, ev.kind, ev.data)
        sent += 1
        if ev.kind in ("done", "error"):
            return
