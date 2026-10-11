"""モック Gateway の試験（local_llm_test_spec.md §2、local_llm_api.md）。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import lla_protocol
from mock_gateway.app import create_app
from mock_gateway.scenario import Scenario, load_scenario

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
FINAL = {"type": "final", "summary": "ok", "outputs": [], "failures": [], "needs_review": []}


def make_client(data: dict[str, Any], tmp_path: Path | None = None, **kw: Any) -> TestClient:
    data = {"name": "t", **data}
    sc = Scenario.model_validate(data)
    record = tmp_path / "requests.jsonl" if tmp_path else None
    app = create_app(sc, record_path=record, ping_interval_s=0.05, **kw)
    return TestClient(app)


def login(c: TestClient) -> dict[str, str]:
    r = c.post("/v1/auth/login", json={"login": "dev", "password": "dev", "device_name": "pytest"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def body(text: str = "こんにちは", *, extra: list[dict[str, str]] | None = None, **kw: Any) -> dict[str, Any]:
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": text}, *(extra or [])]
    return {"model": "local-12b", "messages": msgs, "stream": True, **kw}


def submit(c: TestClient, h: dict[str, str], key: str, b: dict[str, Any]) -> Any:
    return c.post("/v1/steps", json=b, headers={**h, "Idempotency-Key": key})


def read_events(
    c: TestClient, h: dict[str, str], job_id: str, last: int | None = None
) -> list[dict[str, Any]]:
    headers = dict(h)
    if last is not None:
        headers["Last-Event-ID"] = str(last)
    return list(parse_sse(c, f"/v1/steps/{job_id}/events", headers))


def parse_sse(c: TestClient, url: str, headers: dict[str, str]) -> Iterator[dict[str, Any]]:
    with c.stream("GET", url, headers=headers) as r:
        assert r.status_code == 200
        ev: dict[str, Any] = {}
        for line in r.iter_lines():
            if line.startswith(":"):
                continue
            if not line:
                if ev:
                    yield ev
                ev = {}
                continue
            k, _, v = line.partition(": ")
            ev[k] = int(v) if k == "id" else (json.loads(v) if k == "data" else v)


def run_action(c: TestClient, h: dict[str, str], key: str, b: dict[str, Any]) -> dict[str, Any]:
    r = submit(c, h, key, b)
    assert r.status_code == 202, r.text
    events = read_events(c, h, r.json()["job_id"])
    done = events[-1]
    assert done["event"] == "done", events
    tokens = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
    assert tokens == done["data"]["output"]
    out: dict[str, Any] = json.loads(done["data"]["output"])
    return out


# ---- シナリオ ----
@pytest.mark.parametrize("name", ["smoke.yaml", "invoice-basic.yaml"])
def test_bundled_scenarios_load(name: str) -> None:
    sc = load_scenario(SCENARIOS / name)
    assert sc.steps


def test_invalid_action_in_scenario_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate({"name": "bad", "steps": [{"action": {"type": "final", "summary": "x"}}]})
    with pytest.raises(ValidationError):
        Scenario.model_validate({"name": "bad", "faults": [{"kind": "http_status", "status": 418}]})


# ---- 認証・設定 ----
def test_auth_required_and_refresh_rotation() -> None:
    with make_client({}) as c:
        assert c.get("/v1/config").status_code == 401
        assert c.post("/v1/auth/login", json={"login": "dev", "password": "x"}).status_code == 401
        r = c.post("/v1/auth/login", json={"login": "dev", "password": "dev"}).json()
        r2 = c.post("/v1/auth/refresh", json={"refresh_token": r["refresh_token"]})
        assert r2.status_code == 200
        # 使ったリフレッシュトークンは失効している
        assert c.post("/v1/auth/refresh", json={"refresh_token": r["refresh_token"]}).status_code == 401
        cfg = c.get("/v1/config", headers={"Authorization": f"Bearer {r2.json()['access_token']}"}).json()
        assert cfg["protocol_version"] == lla_protocol.PROTOCOL_VERSION
        assert cfg["escalation_target"] is None  # 既定は 12B のみ（検証機と同じ。R3）


# ---- 推論ジョブ ----
def test_smoke_flow_returns_valid_actions() -> None:
    sc = load_scenario(SCENARIOS / "smoke.yaml")
    with TestClient(create_app(sc, ping_interval_s=0.05)) as c:
        h = login(c)
        a0 = run_action(c, h, "r1:s1:0", body("フォルダを確認して"))
        assert a0["type"] == "plan_update"
        a1 = run_action(c, h, "r1:s2:0", body("フォルダを確認して"))
        assert a1["type"] == "tool_call" and a1["tool"] == "fs.list"
        tool_result = {
            "role": "user",
            "content": '[tool_result step=2 tool=fs.list ok=true]\n{"entries":[]}\n[/tool_result]',
        }
        a2 = run_action(c, h, "r1:s3:0", body("フォルダを確認して", extra=[tool_result]))
        assert a2["type"] == "final"
        for a in (a0, a1, a2):
            lla_protocol.validate("Action", a)


def test_idempotency_key() -> None:
    with make_client({"steps": [{"action": FINAL, "repeat": True}]}) as c:
        h = login(c)
        assert c.post("/v1/steps", json=body(), headers=h).status_code == 400
        r1 = submit(c, h, "k1", body())
        r2 = submit(c, h, "k1", body())
        assert (r1.status_code, r2.status_code) == (202, 200)
        assert r1.json()["job_id"] == r2.json()["job_id"]
        r3 = submit(c, h, "k1", body("別の中身"))
        assert r3.status_code == 409 and r3.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_resume_with_last_event_id() -> None:
    with make_client({"steps": [{"action": FINAL}]}) as c:
        h = login(c)
        job_id = submit(c, h, "k1", body()).json()["job_id"]
        full = read_events(c, h, job_id)
        assert [e["id"] for e in full] == list(range(1, len(full) + 1))
        resumed = read_events(c, h, job_id, last=3)
        assert resumed == full[3:]


def test_drop_connection_then_resume() -> None:
    data = {
        "steps": [{"action": FINAL}],
        "faults": [{"kind": "drop_connection", "at": {"step_index": 0}, "after_events": 2}],
    }
    with make_client(data) as c:
        h = login(c)
        job_id = submit(c, h, "k1", body()).json()["job_id"]
        first = read_events(c, h, job_id)
        assert len(first) == 2 and first[-1]["event"] != "done"
        rest = read_events(c, h, job_id, last=first[-1]["id"])
        assert rest[-1]["event"] == "done"


def test_http_status_fault_then_success() -> None:
    data = {
        "steps": [{"action": FINAL}],
        "faults": [{"kind": "http_status", "status": 503, "times": 2, "at": {"step_index": 0}}],
    }
    with make_client(data) as c:
        h = login(c)
        for _ in range(2):
            r = submit(c, h, "k1", body())
            assert r.status_code == 503 and r.json()["error"]["retryable"] is True
        assert submit(c, h, "k1", body()).status_code == 202


def test_raw_queue_restart_faults() -> None:
    data = {
        "steps": [{"action": FINAL, "repeat": True}],
        "faults": [
            {"kind": "raw", "output": '{"type":"tool_call","tool":', "at": {"step_index": 0}},
            {"kind": "queue", "positions": [3, 2, 1], "interval_ms": 10, "at": {"step_index": 1}},
            {"kind": "restart", "after_events": 2, "at": {"step_index": 2}},
        ],
    }
    with make_client(data) as c:
        h = login(c)
        ev0 = read_events(c, h, submit(c, h, "k0", body()).json()["job_id"])
        assert ev0[-1]["data"]["output"] == '{"type":"tool_call","tool":'
        r1 = submit(c, h, "k1", body())
        assert r1.json()["position"] == 3
        ev1 = read_events(c, h, r1.json()["job_id"])
        assert [e["data"]["position"] for e in ev1 if e["event"] == "queue"] == [3, 2, 1]
        ev2 = read_events(c, h, submit(c, h, "k2", body()).json()["job_id"])
        kinds = [e["event"] for e in ev2]
        assert "requeued" in kinds and kinds[-1] == "done"
        after = kinds[kinds.index("requeued") :]
        tokens_after = "".join(
            e["data"]["delta"] for e in ev2[len(kinds) - len(after) :] if e["event"] == "token"
        )
        assert tokens_after == ev2[-1]["data"]["output"]  # 再投入後は最初から送り直す


def test_hang_and_cancel() -> None:
    data = {"steps": [{"action": FINAL}], "faults": [{"kind": "hang", "at": {"step_index": 0}}]}
    with make_client(data) as c:
        h = login(c)
        job_id = submit(c, h, "k1", body()).json()["job_id"]
        assert c.get(f"/v1/steps/{job_id}", headers=h).json()["state"] == "queued"
        assert c.delete(f"/v1/steps/{job_id}", headers=h).status_code == 204
        events = read_events(c, h, job_id)
        assert events[-1]["event"] == "error" and events[-1]["data"]["code"] == "CANCELLED"


def test_context_too_large() -> None:
    with make_client({"config": {"context_budget": 100}, "steps": [{"action": FINAL}]}) as c:
        h = login(c)
        r = submit(c, h, "k1", body("あ" * 1000))
        assert r.status_code == 413 and r.json()["error"]["code"] == "CONTEXT_TOO_LARGE"


def test_invoice_extract_uses_fixture_by_ref() -> None:
    sc = load_scenario(SCENARIOS / "invoice-basic.yaml")
    with TestClient(create_app(sc, ping_interval_s=0.05)) as c:
        h = login(c)
        msg = '<untrusted source="file" ref="請求書/INV-002.pdf#p.1">INVOICE ...</untrusted>'
        out = run_action(c, h, "x:1:0", body(msg, purpose="invoice_extract"))
        assert out["total"] == 2000.0 and out["issuer_name"].endswith("2")


def test_net_levels() -> None:
    data = {"config": {"net_level_cap": "search"}, "faults": [{"kind": "dlp_block"}]}
    with make_client(data) as c:
        h = login(c)
        assert (
            c.post("/v1/web/search", json={"query": "x"}, headers=h).status_code == 403
        )  # クライアントがオフ
        hs = {**h, "X-Net-Level": "browse"}
        r = c.post("/v1/web/search", json={"query": "平井丸"}, headers=hs)
        assert r.status_code == 403 and r.json()["error"]["code"] == "DLP_BLOCKED"
        assert c.post("/v1/web/search", json={"query": "x"}, headers=hs).status_code == 200
        r = c.post("/v1/web/fetch", json={"url": "https://example.com"}, headers=hs)  # 上限は search まで
        assert r.status_code == 403 and r.json()["error"]["code"] == "NET_DISABLED"


def test_requests_are_recorded_without_bodies(tmp_path: Path) -> None:
    with make_client({"steps": [{"action": FINAL}]}, tmp_path) as c:
        h = login(c)
        submit(c, h, "k-rec", body("機密の本文"))
    lines = (tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [json.loads(x) for x in lines]
    assert any(r["idempotency_key"] == "k-rec" and r["status"] == 202 for r in recs)
    assert "機密の本文" not in "\n".join(lines)
