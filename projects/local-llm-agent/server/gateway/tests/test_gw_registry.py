"""モデル台帳（設計書 §8.4、要件 7.4）と、Web Relay 用の内部 API（/internal/v1/net-policy）。"""

from __future__ import annotations

from typing import Any

import pytest

from llm_gateway.engines import FakeEngine
from llm_gateway.registry import apply_production, gate

from .gw_helpers import rig

pytestmark = pytest.mark.anyio

RELAY_SECRET = "relay-secret-0123456789"  # noqa: S105 - 試験用


@pytest.fixture(autouse=True)
def _relay_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMGW_WEB_RELAY_SECRET", RELAY_SECRET)


def test_gate_requires_every_production_metric() -> None:
    prod = {("office", "pass_rate"): 0.8, ("protocol", "valid_rate"): 0.99}
    assert gate({("office", "pass_rate"): 0.8, ("protocol", "valid_rate"): 1.0}, prod) == []
    failures = gate({("office", "pass_rate"): 0.9}, prod)
    assert failures == [
        {"eval_set": "protocol", "metric": "valid_rate", "production": 0.99, "candidate": None}
    ]
    assert gate({("office", "pass_rate"): 0.7, ("protocol", "valid_rate"): 1.0}, prod)[0]["candidate"] == 0.7


def _model(mid: str, **kw: Any) -> dict[str, Any]:
    return {
        "id": mid,
        "slot": "local-12b",
        "base_model": "gemma-3-12b",
        "quant": "awq",
        "lora": kw.get("lora", mid),
        "lora_path": kw.get("lora_path", f"/models/lora/{mid}"),
    }


async def test_registry_promote_gate_and_rollback(db_url: str) -> None:
    engine = FakeEngine()
    async with rig(db_url, engine=engine) as r:
        await r.user("alice")
        await r.user("root", role="admin")
        h = r.h("root")
        assert (await r.http.get("/admin/v1/models", headers=r.h("alice"))).status_code == 403
        # 登録の検証
        bad_slot = await r.http.post("/admin/v1/models", json={**_model("v1"), "slot": "nope"}, headers=h)
        assert bad_slot.status_code == 400
        half = await r.http.post("/admin/v1/models", json={**_model("v1"), "lora_path": None}, headers=h)
        assert half.status_code == 400
        for mid in ("v1", "v2", "v3"):
            res = await r.http.post("/admin/v1/models", json=_model(mid), headers=h)
            assert res.status_code == 201 and res.json()["status"] == "candidate"
        assert (await r.http.post("/admin/v1/models", json=_model("v1"), headers=h)).status_code == 409

        # 評価結果のない版は本番化できない
        no_eval = await r.http.post("/admin/v1/models/v1/promote", headers=h)
        assert no_eval.status_code == 409 and no_eval.json()["error"]["code"] == "EVAL_GATE_FAILED"

        async def evals(mid: str, value: float) -> None:
            res = await r.http.post(
                f"/admin/v1/models/{mid}/evals",
                json={"results": [{"eval_set": "office", "metric": "pass_rate", "value": value}]},
                headers=h,
            )
            assert res.json() == {"added": 1}

        await evals("v1", 0.8)
        ok = await r.http.post("/admin/v1/models/v1/promote", headers=h)
        assert ok.status_code == 200 and ok.json()["status"] == "production"
        assert engine.active_lora == "v1"
        assert engine.lora_activations[-1] == ("v1", "/models/lora/v1")

        # 本番版を下回る候補は弾かれ、エンジンも切り替わらない
        await evals("v2", 0.7)
        worse = await r.http.post("/admin/v1/models/v2/promote", headers=h)
        assert worse.status_code == 409
        err = worse.json()["error"]
        assert err["code"] == "EVAL_GATE_FAILED"
        assert err["detail"]["failures"][0]["production"] == 0.8
        assert engine.active_lora == "v1"

        # 同じ以上なら本番化でき、旧版は退役
        await evals("v3", 0.85)
        assert (await r.http.post("/admin/v1/models/v3/promote", headers=h)).status_code == 200
        listed = {m["id"]: m for m in (await r.http.get("/admin/v1/models", headers=h)).json()["models"]}
        assert listed["v3"]["status"] == "production" and listed["v1"]["status"] == "retired"
        assert listed["v3"]["evals"] == [{"eval_set": "office", "metric": "pass_rate", "value": 0.85}]
        assert engine.active_lora == "v3"

        # 切り戻し: 直前の本番（v1）に戻る
        assert (await r.http.post("/admin/v1/models/v1/rollback", headers=h)).status_code == 409
        back = await r.http.post("/admin/v1/models/v3/rollback", headers=h)
        assert back.json() == {"slot": "local-12b", "production": "v1"}
        assert engine.active_lora == "v1"

        # 再起動時に台帳の本番版が反映される
        engine.active_lora = None
        await apply_production(r.gw)
        assert engine.active_lora == "v1"

        audit = (await r.http.get("/admin/v1/audit", headers=h)).text
        for action in ("admin.model.register", "admin.model.promote", "admin.model.rollback"):
            assert action in audit


async def test_internal_net_policy_for_relay(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("root", role="admin")
        h = r.h("root")
        await r.http.post(
            "/admin/v1/dlp/terms",
            json={"terms": [{"term": "平井丸", "category": "vessel", "readings": ["ひらいまる", " "]}]},
            headers=h,
        )
        await r.http.put(
            "/admin/v1/net/sites", json={"sites": [{"pattern": "*.example.com", "kind": "block"}]}, headers=h
        )
        # 内部トークンが無い・違う・利用者のトークンでは読めない
        assert (await r.http.get("/internal/v1/net-policy")).status_code == 401
        assert (
            await r.http.get("/internal/v1/net-policy", headers={"X-Relay-Secret": "x"})
        ).status_code == 401
        assert (await r.http.get("/internal/v1/net-policy", headers=h)).status_code == 401

        res = await r.http.get("/internal/v1/net-policy", headers={"X-Relay-Secret": RELAY_SECRET})
        assert res.status_code == 200
        body = res.json()
        assert body["sites"] == [{"pattern": "*.example.com", "kind": "block"}]
        (terms,) = body["orgs"].values()
        assert terms["dlp_terms"] == [{"term": "平井丸", "category": "vessel", "readings": ["ひらいまる"]}]
        v1 = body["version"]
        assert len(v1) == 16
        # 内容が変わらなければ版も変わらず、変われば変わる
        again = await r.http.get("/internal/v1/net-policy", headers={"X-Relay-Secret": RELAY_SECRET})
        assert again.json()["version"] == v1
        await r.http.post("/admin/v1/dlp/terms", json={"terms": [{"term": "三井商事"}]}, headers=h)
        changed = await r.http.get("/internal/v1/net-policy", headers={"X-Relay-Secret": RELAY_SECRET})
        assert changed.json()["version"] != v1


async def test_internal_net_policy_closed_without_secret(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLMGW_WEB_RELAY_SECRET")
    async with rig(db_url) as r:
        res = await r.http.get("/internal/v1/net-policy", headers={"X-Relay-Secret": ""})
        assert res.status_code == 401
