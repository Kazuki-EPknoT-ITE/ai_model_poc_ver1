"""永続化: 再起動での requeue（RUN-09）・本文を平文で残さない・10分後の削除（SEC-06, ADR-007）・マイグレーション。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select

from llm_gateway.db import Base, Database, Job, JobEvent, JobPayload, make_engine
from llm_gateway.engines import FakeEngine, FakeScript
from llm_gateway.migrate import upgrade
from llm_gateway.util import FakeClock

from .gw_helpers import real_events, reset_pg, rig, tokens_text

pytestmark = pytest.mark.anyio

IN_MARKER = "IN-MARKER-9c1b"
OUT_MARKER = "OUT-MARKER-7f3a"


async def test_restart_requeues_running_jobs_in_order(db_url: str) -> None:
    hang = FakeEngine()
    hang.queue(FakeScript(hang=True))
    clock = FakeClock()
    ids: list[str] = []
    async with rig(db_url, engine=hang, clock=clock, max_concurrent=1) as r1:
        await r1.user("alice")
        for i in range(3):
            ids.append((await r1.submit("alice", f"rs:{i}:0")).json()["job_id"])
        first = real_events(await r1.events("alice", ids[0], stop_after=2))
        assert [e["event"] for e in first] == ["queue", "started"]
    # プロセスが落ちた状態: running が1件、queued が2件残っている
    db = Database(make_engine(db_url))
    async with db.tx() as s:
        states = {j: (await s.get(Job, j)).state for j in ids}  # type: ignore[union-attr]
    await db.dispose()
    assert states == {ids[0]: "running", ids[1]: "queued", ids[2]: "queued"}

    engine = FakeEngine(
        output='{"type":"final","summary":"再起動後","outputs":[],"failures":[],"needs_review":[]}'
    )
    async with rig(db_url, engine=engine, clock=clock, fresh=False, max_concurrent=1) as r2:
        await r2.login("alice")
        # 途中まで受け取っていたクライアントは Last-Event-ID で続きから受け取る
        rest = real_events(await r2.events("alice", ids[0], last=2))
        kinds = [e["event"] for e in rest]
        assert kinds[0] == "requeued" and rest[0]["data"]["reason"] == "gateway_restart"
        assert kinds[-1] == "done"
        assert tokens_text(rest) == rest[-1]["data"]["output"]
        for jid in ids[1:]:
            assert real_events(await r2.events("alice", jid))[-1]["event"] == "done"
        assert engine.calls == ids  # 同じ順序で投入し直す


def _scan(value: Any, markers: list[str]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
    elif isinstance(value, str):
        raw = value.encode()
    else:
        raw = json.dumps(value, ensure_ascii=False, default=str).encode()
    return [m for m in markers if m.encode() in raw]


async def test_no_plaintext_in_db_and_logs(db_url: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    out = json.dumps(
        {"type": "final", "summary": OUT_MARKER, "outputs": [], "failures": [], "needs_review": []}
    )
    engine = FakeEngine(output=out, chunk_chars=5)
    async with rig(db_url, engine=engine) as r:
        await r.user("alice")
        job_id = (
            await r.submit(
                "alice", "secret:1:0", content=f"{IN_MARKER} 取引先の口座 1234567", priority_hint="normal"
            )
        ).json()["job_id"]
        evs = real_events(await r.events("alice", job_id))
        assert evs[-1]["data"]["output"] == out  # クライアントには平文で届く
        # 全テーブルの全列を調べる
        found: list[str] = []
        async with r.gw.db.tx() as s:
            for table in Base.metadata.sorted_tables:
                for row in (await s.execute(select(table))).all():
                    for v in row:
                        found += _scan(v, [IN_MARKER, OUT_MARKER])
            payload = await s.get(JobPayload, job_id)
            assert payload is not None
            assert payload.request_ct is None  # 依頼本文は完了で直ちに消す
            assert payload.response_ct is not None
        assert found == []
        # SQLite はファイルそのもの（WAL を含む）も調べる
        if db_url.startswith("sqlite"):
            path = Path(db_url.split("///", 1)[1])
            for f in (path, Path(str(path) + "-wal")):
                if f.exists():
                    data = f.read_bytes()
                    assert IN_MARKER.encode() not in data and OUT_MARKER.encode() not in data, f.name
    logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert IN_MARKER not in logs and OUT_MARKER not in logs
    assert "job done" in logs  # ログ自体は出ている


async def test_results_deleted_ten_minutes_after_completion(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        job_id = (await r.submit("alice", "ttl:1:0")).json()["job_id"]
        assert real_events(await r.events("alice", job_id))[-1]["event"] == "done"
        r.clock.advance(599)
        assert await r.gw.jobs.cleanup_once() == 0
        assert (await r.http.get(f"/v1/steps/{job_id}", headers=r.h("alice"))).json()["state"] == "done"
        r.clock.advance(2)
        # 掃除の前でも、期限を過ぎたものは 404
        assert (await r.http.get(f"/v1/steps/{job_id}/events", headers=r.h("alice"))).status_code == 404
        assert await r.gw.jobs.cleanup_once() == 1
        async with r.gw.db.tx() as s:
            assert await s.get(JobPayload, job_id) is None
            assert (await s.execute(select(JobEvent).where(JobEvent.job_id == job_id))).first() is None
            job = await s.get(Job, job_id)
            assert job is not None and job.state == "done" and job.output_tokens  # メタデータは残る
        res = await r.http.get(f"/v1/steps/{job_id}", headers=r.h("alice"))
        assert res.status_code == 404 and res.json()["error"]["code"] == "NOT_FOUND"
        # 期限切れ後に同じキーで投入し直すと、新しいジョブになる
        again = await r.submit("alice", "ttl:1:0")
        assert again.status_code == 202 and again.json()["job_id"] != job_id


async def test_cancel_deletes_request_immediately(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        r.gw.jobs.pause()
        job_id = (await r.submit("alice", "cx:1:0")).json()["job_id"]
        async with r.gw.db.tx() as s:
            p = await s.get(JobPayload, job_id)
            assert p is not None and p.request_ct is not None and p.key_id == "k1" and len(p.nonce) == 12
        await r.http.delete(f"/v1/steps/{job_id}", headers=r.h("alice"))
        async with r.gw.db.tx() as s:
            p = await s.get(JobPayload, job_id)
            assert p is not None and p.request_ct is None


async def test_migrations_match_models(db_url: str) -> None:
    await reset_pg(db_url)
    engine = make_engine(db_url)
    await upgrade(engine)
    await upgrade(engine)  # 2回目は何もしない

    def diff(conn: Any) -> list[Any]:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return list(compare_metadata(ctx, Base.metadata))

    async with engine.connect() as conn:
        d = await conn.run_sync(diff)
    await engine.dispose()
    assert d == []
