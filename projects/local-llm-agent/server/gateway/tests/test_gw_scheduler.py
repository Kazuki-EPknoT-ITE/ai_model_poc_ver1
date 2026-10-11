"""公平スケジューリング（RUN-10）: 利用者間のラウンドロビン・priority・low の後回し・上限。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from llm_gateway.config import EngineSettings, ModelSettings
from llm_gateway.db import Job
from llm_gateway.engines import FakeEngine, FakeScript
from llm_gateway.jobs import QueuedRow, simulate_order

from .gw_helpers import real_events, rig, wait_until

pytestmark = pytest.mark.anyio


async def _run_all(r, ids: list[str], login_of: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
    for jid in ids:
        evs = real_events(await r.events(login_of[jid], jid, timeout=20))
        assert evs[-1]["event"] == "done", evs[-1]


async def _users_in_call_order(r, engine: FakeEngine) -> list[str]:  # type: ignore[no-untyped-def]
    async with r.gw.db.tx() as s:
        owner = {k: v for k, v in (await s.execute(select(Job.id, Job.user_id))).all()}
    by_id = {v: k for k, v in r.user_ids.items()}
    return [by_id[owner[j]] for j in engine.calls]


async def test_round_robin_between_users(db_url: str) -> None:
    engine = FakeEngine()
    async with rig(db_url, engine=engine, max_concurrent=1) as r:
        await r.user("alice", max_concurrent=4)
        await r.user("bob", max_concurrent=4)
        r.gw.jobs.pause()
        login_of: dict[str, str] = {}
        for i in range(3):
            jid = (await r.submit("alice", f"a:{i}:0", priority_hint="normal")).json()["job_id"]
            login_of[jid] = "alice"
        for i in range(3):
            jid = (await r.submit("bob", f"b:{i}:0", priority_hint="normal")).json()["job_id"]
            login_of[jid] = "bob"
        r.gw.jobs.resume()
        await _run_all(r, list(login_of), login_of)
        assert await _users_in_call_order(r, engine) == ["alice", "bob"] * 3


async def test_low_priority_runs_last(db_url: str) -> None:
    engine = FakeEngine()
    async with rig(db_url, engine=engine, max_concurrent=1) as r:
        await r.user("alice")
        await r.user("bob")
        await r.user("carol")
        r.gw.jobs.pause()
        login_of: dict[str, str] = {}
        low = (await r.submit("alice", "low:1:0", priority_hint="low")).json()
        login_of[low["job_id"]] = "alice"
        for login in ("bob", "carol"):
            jid = (await r.submit(login, f"{login}:1:0", priority_hint="normal")).json()["job_id"]
            login_of[jid] = login
        # 待ち順の見積もりでも low は最後（queue イベントで 0 → 2 に更新される）
        assert low["position"] == 0
        await wait_until(lambda: r.gw.jobs._last_pos.get(low["job_id"]) == 2)
        r.gw.jobs.resume()
        await _run_all(r, list(login_of), login_of)
        assert engine.calls[-1] == low["job_id"]
        # queue イベントで位置が更新されている（0 → 2）
        evs = real_events(await r.events("alice", low["job_id"]))
        positions = [e["data"]["position"] for e in evs if e["event"] == "queue"]
        assert positions[0] == 0 and 2 in positions


async def test_interactive_first_within_user(db_url: str) -> None:
    engine = FakeEngine()
    async with rig(db_url, engine=engine, max_concurrent=1) as r:
        await r.user("alice")
        r.gw.jobs.pause()
        normal = (await r.submit("alice", "n:1:0", priority_hint="normal")).json()["job_id"]
        inter = (await r.submit("alice", "i:1:0", priority_hint="interactive")).json()["job_id"]
        r.gw.jobs.resume()
        await _run_all(r, [normal, inter], {normal: "alice", inter: "alice"})
        assert engine.calls == [inter, normal]


async def test_long_input_is_not_interactive(db_url: str) -> None:
    models = [
        ModelSettings(id="local-12b", label="高速", context_budget=32768, engine=EngineSettings(kind="fake"))
    ]
    async with rig(db_url, models=models) as r:
        await r.user("alice")
        r.gw.jobs.pause()
        short = (await r.submit("alice", "s:1:0")).json()["job_id"]
        long_ = (await r.submit("alice", "l:1:0", content="あ" * 7000, priority_hint="interactive")).json()[
            "job_id"
        ]
        async with r.gw.db.tx() as s:
            pri = {k: v for k, v in (await s.execute(select(Job.id, Job.priority))).all()}
        assert pri[short] == 0  # 指定なし・短い → interactive
        assert pri[long_] == 1  # 4K トークン超 → normal に下げる


async def test_per_user_and_global_limits(db_url: str) -> None:
    gate = asyncio.Event()
    engine = FakeEngine()
    engine.queue(*[FakeScript(gate=gate) for _ in range(6)])
    async with rig(db_url, engine=engine, max_concurrent=3) as r:
        await r.user("alice", max_concurrent=1)
        await r.user("bob", max_concurrent=4)
        login_of: dict[str, str] = {}
        for i in range(3):
            login_of[(await r.submit("alice", f"a:{i}:0")).json()["job_id"]] = "alice"
        for i in range(3):
            login_of[(await r.submit("bob", f"b:{i}:0")).json()["job_id"]] = "bob"
        await wait_until(lambda: engine.active == 3)
        await asyncio.sleep(0.2)
        async with r.gw.db.tx() as s:
            rows = (await s.execute(select(Job.user_id, Job.state))).all()
        running = [u for u, st in rows if st == "running"]
        assert len(running) == 3  # 全体の上限
        assert running.count(r.user_ids["alice"]) == 1  # 利用者の上限
        assert running.count(r.user_ids["bob"]) == 2
        gate.set()
        await _run_all(r, list(login_of), login_of)
        assert engine.max_active == 3


def test_simulate_order_round_robin_and_low() -> None:
    t = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        QueuedRow("1", "A", "m", 1, t),
        QueuedRow("2", "A", "m", 1, t),
        QueuedRow("3", "A", "m", 2, t),
        QueuedRow("4", "B", "m", 1, t),
        QueuedRow("5", "C", "m", 0, t),
    ]
    order = [r.id for r in simulate_order(rows, {})]
    assert order == ["1", "4", "5", "2", "3"]
    # 直前に A を取り出していたら B が先
    order2 = [r.id for r in simulate_order(rows, {"A": 5})]
    assert order2[:3] == ["4", "5", "1"]
