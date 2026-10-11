"""管理 API（local_llm_api.md §9.1）: 部門・ネット・機密語・共通指示・監査ログ・ダッシュボード。"""

from __future__ import annotations

import pytest

from .gw_helpers import rig

pytestmark = pytest.mark.anyio


async def test_admin_endpoints_require_admin(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        for path in (
            "/admin/v1/departments",
            "/admin/v1/net/policies",
            "/admin/v1/dlp/terms",
            "/admin/v1/instructions",
            "/admin/v1/audit",
        ):
            res = await r.http.get(path, headers=r.h("alice"))
            assert res.status_code == 403, path


async def test_dlp_terms_are_masked_everywhere(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("root", role="admin")
        h = r.h("root")
        add = await r.http.post(
            "/admin/v1/dlp/terms",
            json={
                "terms": [
                    {"term": "平井丸", "category": "vessel"},
                    {"term": "平井丸"},
                    {"term": "三井商事", "category": "client"},
                ]
            },
            headers=h,
        )
        assert add.json() == {"added": 2, "skipped": 1}
        listed = await r.http.get("/admin/v1/dlp/terms", headers=h)
        body = listed.text
        assert "平井丸" not in body and "三井商事" not in body
        terms = listed.json()["terms"]
        assert [t["masked"] for t in terms] == ["平井**", "三井**"]
        # 削除
        assert (await r.http.delete(f"/admin/v1/dlp/terms/{terms[0]['id']}", headers=h)).status_code == 204
        assert len((await r.http.get("/admin/v1/dlp/terms", headers=h)).json()["terms"]) == 1
        # 監査ログ（JSON・CSV）にも語は残らない
        audit = await r.http.get("/admin/v1/audit", headers=h)
        csv = await r.http.get("/admin/v1/audit?format=csv", headers=h)
        for text in (audit.text, csv.text):
            assert "平井丸" not in text and "三井商事" not in text
        assert "admin.dlp.add" in audit.text
        assert csv.headers["content-type"].startswith("text/csv") and csv.text.startswith("﻿at,user")


async def test_net_policies_sites_and_org_stop(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("root", role="admin")
        h = r.h("root")
        pol = (await r.http.get("/admin/v1/net/policies", headers=h)).json()["policies"]
        assert pol[0]["scope"] == "org"
        assert (
            await r.http.put("/admin/v1/net/policies", json={"scope": "org", "level_cap": "maybe"}, headers=h)
        ).status_code == 400
        ok = await r.http.put(
            "/admin/v1/net/policies",
            json={"scope": "org", "level_cap": "off", "monthly_search_limit": 50},
            headers=h,
        )
        assert ok.status_code == 200
        # 全社停止は利用者の設定画面（/v1/config の上限）にも効く
        await r.user("alice", net_level_cap="browse")
        cfg = (await r.http.get("/v1/config", headers=r.h("alice"))).json()
        assert cfg["net"]["level_cap"] == "off"
        sites = await r.http.put(
            "/admin/v1/net/sites",
            json={
                "sites": [
                    {"pattern": "*.Example.com", "kind": "block"},
                    {"pattern": "*.go.jp", "kind": "trusted"},
                    {"pattern": "*.example.com", "kind": "block"},
                ]
            },
            headers=h,
        )
        assert sites.json()["sites"] == [
            {"pattern": "*.example.com", "kind": "block"},
            {"pattern": "*.go.jp", "kind": "trusted"},
        ]
        bad = await r.http.put(
            "/admin/v1/net/sites", json={"sites": [{"pattern": "http://x/", "kind": "block"}]}, headers=h
        )
        assert bad.status_code == 400


async def test_instructions_versions_and_limit(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("root", role="admin")
        h = r.h("root")
        assert (await r.http.get("/admin/v1/instructions", headers=h)).json() == {
            "current": None,
            "history": [],
        }
        assert (
            await r.http.post("/admin/v1/instructions", json={"body": "金額は税込で"}, headers=h)
        ).json() == {"version": 1}
        assert (
            await r.http.post("/admin/v1/instructions", json={"body": "日付は和暦で"}, headers=h)
        ).json() == {"version": 2}
        cur = (await r.http.get("/admin/v1/instructions", headers=h)).json()
        assert cur["current"]["body"] == "日付は和暦で" and [x["version"] for x in cur["history"]] == [2, 1]
        too_long = await r.http.post("/admin/v1/instructions", json={"body": "あ" * 2300}, headers=h)
        assert too_long.status_code == 400 and too_long.json()["error"]["code"] == "INSTRUCTIONS_TOO_LONG"


async def test_departments_and_dashboard_shape(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("root", role="admin")
        h = r.h("root")
        assert (
            await r.http.post(
                "/admin/v1/departments",
                json={"id": "acc", "name": "経理", "acl_groups": ["finance"]},
                headers=h,
            )
        ).status_code == 201
        assert (
            await r.http.post("/admin/v1/departments", json={"id": "acc", "name": "経理"}, headers=h)
        ).status_code == 409
        p = await r.http.patch(
            "/admin/v1/departments/acc", json={"acl_groups": ["finance", "exec"]}, headers=h
        )
        assert p.json()["acl_groups"] == ["finance", "exec"]
        deps = (await r.http.get("/admin/v1/departments", headers=h)).json()["departments"]
        assert {"id": "acc", "name": "経理", "acl_groups": ["finance", "exec"]} in deps
        dash = (await r.http.get("/admin/v1/dashboard", headers=h)).json()
        for k in (
            "jobs_today",
            "jobs_running",
            "queue_depth",
            "errors_today",
            "users_active_today",
            "latency_ms",
            "gpu",
        ):
            assert k in dash, k
        assert dash["gpu"] == []
