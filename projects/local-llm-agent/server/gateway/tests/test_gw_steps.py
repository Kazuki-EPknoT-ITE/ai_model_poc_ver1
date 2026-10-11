"""推論ジョブの API: 冪等キー・413・SSE（再開・ping・requeued）・取り消し。"""

from __future__ import annotations

import asyncio

import pytest

from llm_gateway.db import Job
from llm_gateway.engines import FakeEngine, FakeScript

from .gw_helpers import real_events, rig, tokens_text, wait_until

pytestmark = pytest.mark.anyio

OUT = '{"type":"final","summary":"完了しました。請求書3件を処理しました。","outputs":[],"failures":[],"needs_review":[]}'


async def test_infer_basic_sse_and_poll(db_url: str) -> None:
    async with rig(db_url, engine=FakeEngine(output=OUT)) as r:
        await r.user("alice")
        res = await r.submit("alice", "run1:s1:0")
        assert res.status_code == 202, res.text
        body = res.json()
        assert set(body) == {"job_id", "state", "position", "eta_seconds"}
        assert body["state"] == "queued"
        assert res.headers["x-request-id"]
        evs = real_events(await r.events("alice", body["job_id"]))
        kinds = [e["event"] for e in evs]
        assert kinds[0] == "queue" and "started" in kinds and kinds[-1] == "done"
        assert [e["id"] for e in evs] == list(range(1, len(evs) + 1))
        assert tokens_text(evs) == OUT
        done = evs[-1]["data"]
        assert done["output"] == OUT and done["finish_reason"] == "stop"
        assert set(done["usage"]) == {"input_tokens", "output_tokens"}
        poll = (await r.http.get(f"/v1/steps/{body['job_id']}", headers=r.h("alice"))).json()
        assert poll["state"] == "done" and poll["output"] == OUT and poll["usage"] == done["usage"]


async def test_idempotency_key(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        await r.user("bob")
        a = await r.submit("alice", "k:1:0", content="同じ依頼")
        assert a.status_code == 202
        again = await r.submit("alice", "k:1:0", content="同じ依頼")
        assert again.status_code == 200
        assert again.json()["job_id"] == a.json()["job_id"]
        diff = await r.submit("alice", "k:1:0", content="違う依頼")
        assert diff.status_code == 409
        assert diff.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        other = await r.submit("bob", "k:1:0", content="同じ依頼")
        assert other.status_code == 409
        missing = await r.http.post(
            "/v1/steps",
            json={"model": "local-12b", "messages": [{"role": "user", "content": "x"}]},
            headers=r.h("alice"),
        )
        assert missing.status_code == 400
        assert missing.json()["error"] == {
            "code": "INVALID_REQUEST",
            "message": "Idempotency-Key ヘッダーが必要です",
            "retryable": False,
        }
        async with r.gw.db.tx() as s:
            from sqlalchemy import func, select

            assert (await s.execute(select(func.count()).select_from(Job))).scalar_one() == 1


async def test_context_too_large_413(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        res = await r.submit("alice", "big:1:0", content="あ" * 7000)  # 7000 / 1.5 > 4000
        assert res.status_code == 413
        assert res.json()["error"]["code"] == "CONTEXT_TOO_LARGE"
        assert res.json()["error"]["retryable"] is False


async def test_invalid_requests(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        bad_model = await r.submit("alice", "m:1:0", model="cloud-x")
        assert bad_model.status_code == 400
        bad_purpose = await r.submit("alice", "p:1:0", purpose="other")
        assert bad_purpose.status_code == 400
        assert "SECRET" not in bad_purpose.text
        big_schema = await r.submit(
            "alice", "s:1:0", response_format={"type": "json_schema", "schema": {"description": "x" * 70000}}
        )
        assert big_schema.status_code == 400
        noauth = await r.http.post("/v1/steps", json={})
        assert noauth.status_code == 401
        assert noauth.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_sse_resume_with_last_event_id(db_url: str) -> None:
    engine = FakeEngine(output=OUT, chunk_chars=4, token_delay_s=0.01)
    async with rig(db_url, engine=engine) as r:
        await r.user("alice")
        job_id = (await r.submit("alice", "resume:1:0")).json()["job_id"]
        first = real_events(await r.events("alice", job_id, stop_after=5))
        assert len(first) == 5
        last = first[-1]["id"]
        rest = real_events(await r.events("alice", job_id, last=last))
        assert rest[0]["id"] == last + 1
        assert rest[-1]["event"] == "done"
        ids = [e["id"] for e in first + rest]
        assert ids == sorted(set(ids)) == list(range(1, len(ids) + 1))
        assert tokens_text(first + rest) == OUT
        # 完了後に最初から読み直しても同じ列が返る
        again = real_events(await r.events("alice", job_id))
        assert [e["id"] for e in again] == ids


async def test_sse_ping_while_waiting(db_url: str) -> None:
    engine = FakeEngine()
    engine.queue(FakeScript(hang=True))
    async with rig(db_url, engine=engine, ping_interval_s=0.1) as r:
        await r.user("alice")
        job_id = (await r.submit("alice", "ping:1:0")).json()["job_id"]
        evs = await r.events("alice", job_id, stop_after=2)  # queue, started
        assert [e["event"] for e in real_events(evs)] == ["queue", "started"]
        # started の後は何も来ないので ping が届く
        got_ping = False

        async def read_ping() -> None:
            nonlocal got_ping
            async with r.http.stream(
                "GET", f"/v1/steps/{job_id}/events", headers=r.h("alice", **{"Last-Event-ID": "2"})
            ) as res:
                async for line in res.aiter_lines():
                    if line.startswith(": ping"):
                        got_ping = True
                        return

        await asyncio.wait_for(read_ping(), 3)
        assert got_ping
        await r.http.delete(f"/v1/steps/{job_id}", headers=r.h("alice"))


async def test_requeued_when_engine_crashes(db_url: str) -> None:
    engine = FakeEngine(output=OUT, chunk_chars=8)
    engine.queue(FakeScript(fail_after_tokens=3))
    async with rig(db_url, engine=engine) as r:
        await r.user("alice")
        job_id = (await r.submit("alice", "crash:1:0")).json()["job_id"]
        evs = real_events(await r.events("alice", job_id))
        kinds = [e["event"] for e in evs]
        assert "requeued" in kinds
        i = kinds.index("requeued")
        assert kinds[:i].count("token") == 3
        assert kinds[i + 1 :].count("started") == 1
        assert kinds[-1] == "done"
        assert tokens_text(evs) == OUT
        assert evs[i]["data"]["reason"] == "engine_unavailable"
        async with r.gw.db.tx() as s:
            job = await s.get(Job, job_id)
            assert job is not None and job.requeue_count == 1 and job.state == "done"


async def test_engine_gives_up_after_max_requeues(db_url: str) -> None:
    engine = FakeEngine()
    engine.queue(*[FakeScript(fail_after_tokens=0) for _ in range(3)])
    async with rig(db_url, engine=engine, max_requeues=2) as r:
        await r.user("alice")
        job_id = (await r.submit("alice", "dead:1:0")).json()["job_id"]
        evs = real_events(await r.events("alice", job_id))
        assert [e["event"] for e in evs].count("requeued") == 2
        assert evs[-1]["event"] == "error"
        assert evs[-1]["data"] == {"code": "ENGINE_UNAVAILABLE", "retryable": True}
        poll = (await r.http.get(f"/v1/steps/{job_id}", headers=r.h("alice"))).json()
        assert poll["state"] == "failed" and poll["error"]["code"] == "ENGINE_UNAVAILABLE"


async def test_engine_request_error_fails_job(db_url: str) -> None:
    engine = FakeEngine()
    engine.queue(FakeScript(request_error=True))
    async with rig(db_url, engine=engine) as r:
        await r.user("alice")
        job_id = (await r.submit("alice", "bad:1:0")).json()["job_id"]
        evs = real_events(await r.events("alice", job_id))
        assert evs[-1]["data"] == {"code": "ENGINE_ERROR", "retryable": False}


async def test_cancel_queued_and_running(db_url: str) -> None:
    engine = FakeEngine()
    engine.queue(FakeScript(hang=True))
    async with rig(db_url, engine=engine, max_concurrent=1) as r:
        await r.user("alice")
        running = (await r.submit("alice", "c:1:0")).json()["job_id"]
        queued = (await r.submit("alice", "c:2:0")).json()["job_id"]
        await r.events("alice", running, stop_after=2)  # started まで
        await wait_until(lambda: engine.active == 1)

        # 待ち行列のジョブを取り消す
        assert (await r.http.delete(f"/v1/steps/{queued}", headers=r.h("alice"))).status_code == 204
        evs = real_events(await r.events("alice", queued))
        assert evs[-1]["event"] == "error"
        assert evs[-1]["data"] == {"code": "CANCELLED", "retryable": False}

        # 実行中のジョブを取り消す → エンジンへの依頼も止まる
        reader = asyncio.create_task(r.events("alice", running, last=2))
        await asyncio.sleep(0.1)
        assert (await r.http.delete(f"/v1/steps/{running}", headers=r.h("alice"))).status_code == 204
        evs = real_events(await reader)
        assert evs[-1]["data"] == {"code": "CANCELLED", "retryable": False}
        await wait_until(lambda: engine.active == 0)
        poll = (await r.http.get(f"/v1/steps/{running}", headers=r.h("alice"))).json()
        assert poll["state"] == "cancelled"
        # 2回目の取り消しも 204（冪等）
        assert (await r.http.delete(f"/v1/steps/{running}", headers=r.h("alice"))).status_code == 204


async def test_other_user_cannot_see_job(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        await r.user("bob")
        job_id = (await r.submit("alice", "own:1:0")).json()["job_id"]
        for method, path in (
            ("GET", f"/v1/steps/{job_id}"),
            ("GET", f"/v1/steps/{job_id}/events"),
            ("DELETE", f"/v1/steps/{job_id}"),
        ):
            res = await r.http.request(method, path, headers=r.h("bob"))
            assert res.status_code == 404, (method, path)
            assert res.json()["error"]["code"] == "NOT_FOUND"


async def test_queue_limit_per_user(db_url: str) -> None:
    async with rig(db_url, max_queued_per_user=2) as r:
        await r.user("alice")
        r.gw.jobs.pause()
        assert (await r.submit("alice", "q:1:0")).status_code == 202
        assert (await r.submit("alice", "q:2:0")).status_code == 202
        res = await r.submit("alice", "q:3:0")
        assert res.status_code == 429
        assert res.json()["error"]["code"] == "QUEUE_LIMIT" and res.json()["error"]["retryable"] is True
