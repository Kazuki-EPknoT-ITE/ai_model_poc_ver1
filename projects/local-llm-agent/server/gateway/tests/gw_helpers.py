"""試験用: Gateway を実際の HTTP サーバー（uvicorn）として同じイベントループで動かす。

SSE を途中で切る・再接続する試験のため、ASGITransport ではなく本物のソケットを使う。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import uvicorn
from sqlalchemy import text

from llm_gateway.app import create_app
from llm_gateway.config import (
    Argon2Settings,
    AuthSettings,
    DatabaseSettings,
    EngineSettings,
    JobSettings,
    ModelSettings,
    Profile,
    SchedulerSettings,
    Settings,
    UpstreamSettings,
)
from llm_gateway.crypto import PayloadCipher, generate_ed25519_pem, parse_ed25519_pem
from llm_gateway.db import Database, make_engine
from llm_gateway.engines import FakeEngine, InferenceEngine
from llm_gateway.gateway import Gateway
from llm_gateway.users import UserCreate
from llm_gateway.util import Clock, FakeClock

SIGNING_KEY = parse_ed25519_pem(generate_ed25519_pem())
PAYLOAD_KEYS = {"k1": os.urandom(32)}
PASSWORD = "correct-horse-1"  # noqa: S105 - 試験用


def settings_for(db_url: str, **over: Any) -> Settings:
    sched = SchedulerSettings(
        max_concurrent=over.pop("max_concurrent", 2),
        tick_s=0.05,
        engine_retry_s=0.05,
        max_requeues=over.pop("max_requeues", 10),
        max_queued_per_user=over.pop("max_queued_per_user", 50),
    )
    jobs = JobSettings(
        ping_interval_s=over.pop("ping_interval_s", 0.3),
        token_flush_ms=0,
        cleanup_interval_s=3600,
        result_ttl_s=600,
    )
    auth = AuthSettings(argon2=Argon2Settings(memory_kib=1024, time_cost=1, parallelism=1))
    models = over.pop(
        "models",
        [
            ModelSettings(
                id="local-12b", label="高速", context_budget=4000, engine=EngineSettings(kind="fake")
            ),
        ],
    )
    s = Settings(
        database=DatabaseSettings(url=db_url),
        models=models,
        scheduler=sched,
        jobs=jobs,
        auth=auth,
        upstreams=over.pop("upstreams", UpstreamSettings()),
    )
    assert not over, f"unknown overrides: {over}"
    return s


async def reset_pg(url: str) -> None:
    if not url.startswith("postgresql"):
        return
    eng = make_engine(url)
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await eng.dispose()


@dataclass
class Rig:
    gw: Gateway
    url: str
    clock: FakeClock
    engine: FakeEngine
    http: httpx.AsyncClient
    tokens: dict[str, str] = field(default_factory=dict)  # login → access_token
    user_ids: dict[str, str] = field(default_factory=dict)

    async def add_user(self, login: str, *, role: str = "user", **kw: Any) -> str:
        async with self.gw.db.tx() as s:
            u = await self.gw.users.create(
                s,
                UserCreate(login=login, display_name=login, password=PASSWORD, role=role, **kw),  # type: ignore[arg-type]
            )
            self.user_ids[login] = u.id
            return u.id

    async def login(self, login: str, password: str = PASSWORD) -> dict[str, Any]:
        r = await self.http.post(
            "/v1/auth/login", json={"login": login, "password": password, "device_name": "pytest"}
        )
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.json()
        self.tokens[login] = body["access_token"]
        return body

    async def user(self, login: str, **kw: Any) -> str:
        await self.add_user(login, **kw)
        await self.login(login)
        return login

    def h(self, login: str, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[login]}", **extra}

    async def submit(
        self, login: str, key: str, *, content: str = "こんにちは", **body: Any
    ) -> httpx.Response:
        payload = {
            "model": "local-12b",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 64,
            "stream": True,
            **body,
        }
        return await self.http.post(
            "/v1/steps", json=payload, headers=self.h(login, **{"Idempotency-Key": key})
        )

    async def events(
        self,
        login: str,
        job_id: str,
        *,
        last: int | None = None,
        stop_after: int | None = None,
        timeout: float = 10,
    ) -> list[dict[str, Any]]:
        """SSE を読む。done/error で終わるか、stop_after 件で切断する。ping は {"event": "ping"}。"""
        out: list[dict[str, Any]] = []
        headers = self.h(login)
        if last is not None:
            headers["Last-Event-ID"] = str(last)

        async def run() -> None:
            async with self.http.stream("GET", f"/v1/steps/{job_id}/events", headers=headers) as res:
                assert res.status_code == 200, await res.aread()
                cur: dict[str, Any] = {}
                async for line in res.aiter_lines():
                    if line == "":
                        if cur:
                            out.append(cur)
                            cur = {}
                            ev = out[-1]
                            if ev["event"] in ("done", "error"):
                                return
                            if (
                                stop_after is not None
                                and len([e for e in out if e["event"] != "ping"]) >= stop_after
                            ):
                                return
                        continue
                    if line.startswith(":"):
                        cur = {"event": "ping"}
                        continue
                    k, _, v = line.partition(": ")
                    if k == "id":
                        cur["id"] = int(v)
                    elif k == "event":
                        cur["event"] = v
                    elif k == "data":
                        cur["data"] = json.loads(v)

        await asyncio.wait_for(run(), timeout)
        return out


def real_events(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in evs if e["event"] != "ping"]


def tokens_text(evs: list[dict[str, Any]]) -> str:
    """requeued が来たら逐次テキストを捨てる（クライアントと同じ扱い）。"""
    text_ = ""
    for e in evs:
        if e["event"] == "requeued":
            text_ = ""
        elif e["event"] == "token":
            text_ += e["data"]["delta"]
    return text_


def free_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    return sock


@asynccontextmanager
async def serve(gw: Gateway) -> AsyncIterator[str]:
    app = create_app(gw)
    sock = free_socket()
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, lifespan="on", log_level="warning", timeout_graceful_shutdown=1)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    for _ in range(500):
        if server.started:
            break
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


def make_gateway(
    db_url: str,
    *,
    clock: Clock,
    engine: InferenceEngine | None = None,
    profile: Profile | None = None,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    **over: Any,
) -> Gateway:
    settings = settings_for(db_url, **over)
    engines = {m.id: (engine or FakeEngine()) for m in settings.models}
    return Gateway(
        settings,
        profile=profile or Profile(),
        signing_key=SIGNING_KEY,
        cipher=PayloadCipher(PAYLOAD_KEYS, "k1"),
        db=Database(make_engine(db_url)),
        engines=engines,
        clock=clock,
        upstream_transport=upstream_transport,
        init_db="migrate",
    )


@asynccontextmanager
async def rig(
    db_url: str,
    *,
    engine: FakeEngine | None = None,
    clock: FakeClock | None = None,
    fresh: bool = True,
    **over: Any,
) -> AsyncIterator[Rig]:
    if fresh:
        await reset_pg(db_url)
    clock = clock or FakeClock()
    engine = engine or FakeEngine()
    gw = make_gateway(db_url, clock=clock, engine=engine, **over)
    try:
        async with serve(gw) as url, httpx.AsyncClient(base_url=url, timeout=10) as http:
            yield Rig(gw=gw, url=url, clock=clock, engine=engine, http=http)
    finally:
        await gw.db.dispose()


async def wait_until(pred: Any, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        v = pred()
        if asyncio.iscoroutine(v):
            v = await v
        if v:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)
