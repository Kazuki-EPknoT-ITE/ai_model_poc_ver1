"""監査・設定・中継とネット上限・死活とメトリクス・ダッシュボード。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from llm_gateway.config import UpstreamSettings, load_profile
from llm_gateway.db import AuditEvent, NetPolicy

from .gw_helpers import real_events, rig, wait_until

pytestmark = pytest.mark.anyio


async def test_audit_dedupe_by_id(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        await r.user("bob")

        def rec(i: str) -> dict[str, Any]:
            return {
                "id": i,
                "at": "2026-10-11T01:02:03Z",
                "action": "file.write",
                "target": "C:\\work\\送金明細.xlsx",
                "result": "ok",
                "detail": {"tool": "xlsx.write_cells", "run_id": "r1"},
            }

        res = await r.http.post(
            "/v1/audit", json={"records": [rec("a"), rec("b"), rec("a")]}, headers=r.h("alice")
        )
        assert res.status_code == 204
        res = await r.http.post(
            "/v1/audit", json={"records": [rec("a"), rec("b"), rec("c")]}, headers=r.h("alice")
        )
        assert res.status_code == 204
        # 別の利用者の同じ id は別の記録
        await r.http.post("/v1/audit", json={"records": [rec("a")]}, headers=r.h("bob"))
        async with r.gw.db.tx() as s:
            rows = list((await s.execute(select(AuditEvent).where(AuditEvent.source == "client"))).scalars())
        assert len(rows) == 4
        assert {(x.user_id, x.client_id) for x in rows} == {
            (r.user_ids["alice"], f"{r.user_ids['alice']}:a"),
            (r.user_ids["alice"], f"{r.user_ids['alice']}:b"),
            (r.user_ids["alice"], f"{r.user_ids['alice']}:c"),
            (r.user_ids["bob"], f"{r.user_ids['bob']}:a"),
        }
        assert rows[0].detail == {"tool": "xlsx.write_cells", "run_id": "r1"}
        bad = await r.http.post("/v1/audit", json={"records": [{"id": "x"}]}, headers=r.h("alice"))
        assert bad.status_code == 400


def _write_profile(tmp: Path) -> Path:
    (tmp / "macros").mkdir()
    (tmp / "instructions.md").write_text("社内の共通指示です。", encoding="utf-8")
    (tmp / "macros" / "monthly.yaml").write_text(
        "name: 月次締め\ndescription: 月次の締め作業\nprompt: 今月の請求書を集計して\n", encoding="utf-8"
    )
    p = tmp / "profile.yaml"
    p.write_text(
        """
org: { id: hirai, name: 平井海運 }
instructions: { version: 4, file: instructions.md }
escalation_target: null
net: { level_cap: browse, monthly_search_limit: 2 }
features: { rag: true, ocr: true, web: true }
macros:
  - { name: 請求書, description: 請求書の抽出, prompt: このフォルダの請求書を読み取って }
macros_dir: macros
""",
        encoding="utf-8",
    )
    return p


async def test_config_from_profile(db_url: str, tmp_path: Path) -> None:
    profile = load_profile(_write_profile(tmp_path))
    async with rig(db_url, profile=profile) as r:
        await r.user("alice", max_concurrent=3, net_level_cap="search")
        cfg = (await r.http.get("/v1/config", headers=r.h("alice"))).json()
        assert cfg["protocol_version"] == "1.0"
        assert cfg["org"] == {"id": "hirai", "name": "平井海運"}
        assert cfg["models"] == [
            {"id": "local-12b", "label": "高速", "available": True, "context_budget": 4000}
        ]
        assert cfg["escalation_target"] is None
        assert cfg["instructions"] == {"version": 4, "body": "社内の共通指示です。"}
        assert cfg["net"] == {"level_cap": "search", "monthly_search_limit": 2, "used_this_month": 0}
        assert cfg["limits"] == {"max_concurrent": 3, "max_tool_result_tokens": 6000}
        assert cfg["features"] == {"rag": False, "ocr": False, "web": False}  # 上流が未設定
        assert cfg["macros"] == [
            {"name": "請求書", "description": "請求書の抽出", "prompt": "このフォルダの請求書を読み取って"},
            {"name": "月次締め", "description": "月次の締め作業", "prompt": "今月の請求書を集計して"},
        ]
        assert cfg["invoice_schemas"] == ["invoice.v1"]


async def test_relay_unconfigured_is_503(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice", net_level_cap="browse")
        for path in ("/v1/rag/search", "/v1/web/search", "/v1/web/fetch"):
            res = await r.http.post(
                path, json={"query": "x"}, headers=r.h("alice", **{"X-Net-Level": "browse"})
            )
            assert res.status_code == 503, path
            assert res.json()["error"]["code"] == "FEATURE_UNAVAILABLE"
        res = await r.http.post(
            "/v1/vision/ocr", files={"image": ("p.png", b"\x89PNG")}, headers=r.h("alice")
        )
        assert res.status_code == 503


class Upstream:
    """上流（RAG・Web Relay・OCR）の代わり。受け取ったリクエストを記録する。"""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/search":
            return httpx.Response(
                200, json={"results": [{"rank": 1, "title": "t", "url": "https://x", "snippet": ""}]}
            )
        if path == "/fetch":
            if b"blocked" in request.content:
                return httpx.Response(
                    403, json={"error": {"code": "DLP_BLOCKED", "message": "x", "retryable": False}}
                )
            return httpx.Response(200, json={"url": "https://x", "text": "本文", "truncated": False})
        if path == "/v1/rag/search":
            return httpx.Response(200, json={"hits": []})
        if path == "/v1/vision/ocr":
            return httpx.Response(
                200, json={"text": "請求書", "lines": [], "engine_used": "ocr", "confidence": 0.9}
            )
        return httpx.Response(404)


UPSTREAMS = UpstreamSettings(rag_url="http://rag", web_relay_url="http://relay", ocr_url="http://ocr")


@pytest.fixture(autouse=True)
def _upstream_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    # 上流と共有する内部トークン（本番は環境変数かファイルで渡す）
    monkeypatch.setenv("LLMGW_WEB_RELAY_SECRET", "relay-secret-0123456789")
    monkeypatch.setenv("LLMGW_OCR_SECRET", "ocr-secret")
    monkeypatch.setenv("LLMGW_RAG_SECRET", "rag-secret")


async def test_relay_net_level_checks(db_url: str, tmp_path: Path) -> None:
    up = Upstream()
    profile = load_profile(_write_profile(tmp_path))  # 全社: browse, 月2回
    async with rig(
        db_url, profile=profile, upstreams=UPSTREAMS, upstream_transport=httpx.MockTransport(up)
    ) as r:
        await r.user("alice", net_level_cap="search", department="経理")
        await r.user("bob", net_level_cap="browse", department="営業")

        async def post(login: str, path: str, level: str | None, body: dict[str, Any]) -> httpx.Response:
            extra = {"X-Net-Level": level} if level else {}
            return await r.http.post(path, json=body, headers=r.h(login, **extra))

        # クライアントがオフ・未送信なら拒否（二重の確認）
        assert (await post("alice", "/v1/web/search", None, {"query": "q"})).status_code == 403
        assert (await post("alice", "/v1/web/search", "off", {"query": "q"})).status_code == 403
        # 利用者の上限 search → 検索は可、取得（browse）は不可
        ok = await post("alice", "/v1/web/search", "browse", {"query": "q"})
        assert ok.status_code == 200 and ok.json()["remaining_quota"] == 1
        sent = json.loads(up.calls[-1].content)
        # Web Relay の契約: 実効の level（小さいほう）と利用者を本文で、内部トークンをヘッダーで渡す
        assert sent["level"] == "search" and sent["user_id"] == r.user_ids["alice"]
        assert sent["monthly_search_limit"] is None  # 回数は Gateway が数える（二重に数えない）
        assert up.calls[-1].url.path == "/search"
        assert up.calls[-1].headers["X-Relay-Secret"] == "relay-secret-0123456789"
        res = await post("alice", "/v1/web/fetch", "browse", {"url": "https://x"})
        assert res.status_code == 403 and res.json()["error"]["code"] == "NET_DISABLED"
        # bob は browse まで
        assert (await post("bob", "/v1/web/fetch", "browse", {"url": "https://x"})).status_code == 200
        # 上流のエラー（DLP）はそのまま返す
        dlp = await post("bob", "/v1/web/fetch", "browse", {"url": "https://blocked"})
        assert dlp.status_code == 403 and dlp.json()["error"]["code"] == "DLP_BLOCKED"
        # 部門の上限 off（経理）→ 利用者が search でも拒否
        async with r.gw.db.tx() as s:
            s.add(NetPolicy(scope="department:経理", level_cap="off", monthly_search_limit=None))
        assert (await post("alice", "/v1/web/search", "search", {"query": "q"})).status_code == 403
        cfg = (await r.http.get("/v1/config", headers=r.h("alice"))).json()
        assert cfg["net"]["level_cap"] == "off" and cfg["features"]["web"] is True
        # 全社停止
        async with r.gw.db.tx() as s:
            s.add(NetPolicy(scope="org", level_cap="off", monthly_search_limit=None))
        assert (await post("bob", "/v1/web/fetch", "browse", {"url": "https://x"})).status_code == 403
        # 監査に拒否が残る
        async with r.gw.db.tx() as s:
            n = (
                await s.execute(
                    select(func.count()).where(
                        AuditEvent.result == "blocked:NET_DISABLED", AuditEvent.source == "gateway"
                    )
                )
            ).scalar_one()
        assert n >= 4


async def test_relay_search_quota(db_url: str, tmp_path: Path) -> None:
    up = Upstream()
    profile = load_profile(_write_profile(tmp_path))  # 月2回
    async with rig(
        db_url, profile=profile, upstreams=UPSTREAMS, upstream_transport=httpx.MockTransport(up)
    ) as r:
        await r.user("bob", net_level_cap="browse")
        h = r.h("bob", **{"X-Net-Level": "search"})
        assert (await r.http.post("/v1/web/search", json={"query": "1"}, headers=h)).json()[
            "remaining_quota"
        ] == 1
        assert (await r.http.post("/v1/web/search", json={"query": "2"}, headers=h)).json()[
            "remaining_quota"
        ] == 0
        res = await r.http.post("/v1/web/search", json={"query": "3"}, headers=h)
        assert res.status_code == 429 and res.json()["error"]["code"] == "QUOTA_EXCEEDED"
        assert res.json()["error"]["retryable"] is False
        cfg = (await r.http.get("/v1/config", headers=r.h("bob"))).json()
        assert cfg["net"]["used_this_month"] == 2


async def test_relay_rag_and_ocr(db_url: str) -> None:
    up = Upstream()
    async with rig(db_url, upstreams=UPSTREAMS, upstream_transport=httpx.MockTransport(up)) as r:
        await r.user("alice", department="経理")
        res = await r.http.post(
            "/v1/rag/search",
            json={"query": "傭船料", "top_k": 5, "acl_groups": ["everything"]},
            headers=r.h("alice", **{"X-Request-Id": "req-123"}),
        )
        assert res.status_code == 200 and res.json() == {"hits": []}
        assert res.headers["x-request-id"] == "req-123"
        sent = json.loads(up.calls[-1].content)
        # クライアントが送った acl_groups は使わず、サーバー側で付ける（SEC-07）
        assert sent["acl_groups"] == [f"user:{r.user_ids['alice']}", "department:経理"]
        assert up.calls[-1].headers["X-Request-Id"] == "req-123"
        assert up.calls[-1].headers["X-Internal-Token"] == "rag-secret"
        ocr = await r.http.post(
            "/v1/vision/ocr",
            files={"image": ("page.png", b"\x89PNG fake", "image/png")},
            data={"engine": "auto", "lang": "ja"},
            headers=r.h("alice"),
        )
        assert ocr.status_code == 200 and ocr.json()["text"] == "請求書"
        assert up.calls[-1].headers["content-type"].startswith("multipart/form-data")
        assert b"\x89PNG fake" in up.calls[-1].content
        assert up.calls[-1].headers["X-Internal-Token"] == "ocr-secret"


async def test_relay_upstream_down(db_url: str) -> None:
    def down(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with rig(db_url, upstreams=UPSTREAMS, upstream_transport=httpx.MockTransport(down)) as r:
        await r.user("alice")
        res = await r.http.post("/v1/rag/search", json={"query": "x"}, headers=r.h("alice"))
        assert res.status_code == 503 and res.json()["error"]["retryable"] is True


async def test_health_ready_metrics_dashboard(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        await r.user("root", role="admin")
        assert (await r.http.get("/healthz")).json() == {"status": "ok"}
        ready = (await r.http.get("/readyz")).json()
        assert ready["engine"] == {"local-12b": "ok"} and ready["db"] == "ok"
        job_id = (await r.submit("alice", "m:1:0")).json()["job_id"]
        assert real_events(await r.events("alice", job_id))[-1]["event"] == "done"
        r.engine.healthy = False
        ready = (await r.http.get("/readyz")).json()
        assert ready["engine"] == {"local-12b": "unavailable"}
        r.engine.healthy = True
        await wait_until(lambda: r.gw.jobs.running_count == 0)
        text = (await r.http.get("/metrics")).text
        assert 'llmgw_queue_depth{model="local-12b"} 0' in text
        assert 'llmgw_jobs_total{state="done"} 1' in text
        assert 'llmgw_job_seconds_bucket{model="local-12b",le="+Inf"} 1' in text
        assert "llmgw_running_jobs 0" in text
        dash = (await r.http.get("/admin/v1/dashboard", headers=r.h("root"))).json()
        assert dash["jobs"]["done"] == 1 and dash["queue"] == {"queued": 0, "running": 0, "by_model": {}}
        assert dash["latency_seconds"]["total"]["count"] == 1
        assert dash["users"] == {"total": 2, "active": 1}
        assert dash["error_rate"] == 0.0
