"""認証（ADR-008）: ロック・ローテーション・再利用検知での全失効・期限切れ・管理者権限。"""

from __future__ import annotations

import jwt
import pytest
from sqlalchemy import select

from llm_gateway.db import AuditEvent, Session

from .gw_helpers import PASSWORD, rig

pytestmark = pytest.mark.anyio


async def test_login_and_wrong_password(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.add_user("alice", department="経理")
        body = await r.login("alice")
        assert body["expires_in"] == 900
        assert body["user"]["display_name"] == "alice" and body["user"]["role"] == "user"
        assert body["user"]["department"] == "経理"
        header = jwt.get_unverified_header(body["access_token"])
        assert header["alg"] == "EdDSA"
        assert (await r.http.get("/v1/config", headers=r.h("alice"))).status_code == 200
        bad = await r.http.post(
            "/v1/auth/login", json={"login": "alice", "password": "nope", "device_name": "x"}
        )
        assert bad.status_code == 401 and bad.json()["error"]["code"] == "UNAUTHENTICATED"
        unknown = await r.http.post("/v1/auth/login", json={"login": "zzz", "password": "nope"})
        assert unknown.status_code == 401
        # リフレッシュトークンは DB にハッシュだけを置く
        async with r.gw.db.tx() as s:
            hashes = list((await s.execute(select(Session.refresh_hash))).scalars())
        assert body["refresh_token"] not in hashes and len(hashes) == 1
        # ログインの成否は監査ログに残る（パスワードは残らない）
        async with r.gw.db.tx() as s:
            audits = list((await s.execute(select(AuditEvent).where(AuditEvent.action == "login"))).scalars())
        assert {a.result for a in audits} == {"ok", "error:UNAUTHENTICATED"}
        assert all("nope" not in str(a.detail) and a.target != "nope" for a in audits)


async def test_lockout_after_five_failures(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.add_user("alice")
        for _ in range(5):
            res = await r.http.post("/v1/auth/login", json={"login": "alice", "password": "wrong"})
            assert res.status_code == 401
        # ロック中は正しいパスワードでも通らない
        locked = await r.http.post("/v1/auth/login", json={"login": "alice", "password": PASSWORD})
        assert locked.status_code == 401
        r.clock.advance(14 * 60)
        locked = await r.http.post("/v1/auth/login", json={"login": "alice", "password": PASSWORD})
        assert locked.status_code == 401
        r.clock.advance(61)
        await r.login("alice")
        # 4回までの失敗ではロックしない（成功で回数は戻る）
        for _ in range(4):
            await r.http.post("/v1/auth/login", json={"login": "alice", "password": "wrong"})
        await r.login("alice")


async def test_refresh_rotation_and_reuse_detection(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.add_user("alice")
        first = await r.login("alice")
        rt1 = first["refresh_token"]
        second = (await r.http.post("/v1/auth/refresh", json={"refresh_token": rt1})).json()
        rt2, at2 = second["refresh_token"], second["access_token"]
        assert rt2 != rt1
        assert (await r.http.get("/v1/config", headers={"Authorization": f"Bearer {at2}"})).status_code == 200
        # 交換済みの rt1 がもう一度使われた → 盗用とみなし、系列（rt2・at2 も）をすべて失効
        reuse = await r.http.post("/v1/auth/refresh", json={"refresh_token": rt1})
        assert reuse.status_code == 401
        assert (await r.http.post("/v1/auth/refresh", json={"refresh_token": rt2})).status_code == 401
        res = await r.http.get("/v1/config", headers={"Authorization": f"Bearer {at2}"})
        assert res.status_code == 401
        # 別の端末（別の系列）は影響を受けない
        other = await r.login("alice")
        assert (
            await r.http.post("/v1/auth/refresh", json={"refresh_token": other["refresh_token"]})
        ).status_code == 200


async def test_token_expiry(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.add_user("alice")
        body = await r.login("alice")
        r.clock.advance(901)
        res = await r.http.get("/v1/config", headers=r.h("alice"))
        assert res.status_code == 401 and res.json()["error"]["code"] == "TOKEN_EXPIRED"
        new = await r.http.post("/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert new.status_code == 200
        # リフレッシュトークンは30日
        r.clock.advance(31 * 86400)
        old = await r.http.post("/v1/auth/refresh", json={"refresh_token": new.json()["refresh_token"]})
        assert old.status_code == 401
        # 改ざんしたトークン
        bad = body["access_token"][:-4] + "AAAA"
        assert (await r.http.get("/v1/config", headers={"Authorization": f"Bearer {bad}"})).status_code == 401


async def test_logout_and_password_change(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.add_user("alice")
        a = await r.login("alice")
        b = await r.login("alice")  # 別の端末
        assert (
            await r.http.post("/v1/auth/logout", json={"refresh_token": a["refresh_token"]})
        ).status_code == 204
        assert (
            await r.http.post("/v1/auth/refresh", json={"refresh_token": a["refresh_token"]})
        ).status_code == 401
        hb = {"Authorization": f"Bearer {b['access_token']}"}
        wrong = await r.http.post(
            "/v1/auth/password", json={"current": "x", "new": "new-password-1"}, headers=hb
        )
        assert wrong.status_code == 403
        short = await r.http.post("/v1/auth/password", json={"current": PASSWORD, "new": "short"}, headers=hb)
        assert short.status_code == 400
        ok = await r.http.post(
            "/v1/auth/password", json={"current": PASSWORD, "new": "new-password-1"}, headers=hb
        )
        assert ok.status_code == 204
        old = await r.http.post("/v1/auth/login", json={"login": "alice", "password": PASSWORD})
        assert old.status_code == 401
        await r.login("alice", "new-password-1")


async def test_admin_api_requires_admin(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        await r.user("root", role="admin")
        for method, path in (("GET", "/admin/v1/users"), ("GET", "/admin/v1/dashboard")):
            res = await r.http.request(method, path, headers=r.h("alice"))
            assert res.status_code == 403 and res.json()["error"]["code"] == "FORBIDDEN"
        assert (await r.http.get("/admin/v1/users")).status_code == 401

        created = await r.http.post(
            "/admin/v1/users",
            json={
                "login": "bob",
                "display_name": "ボブ",
                "password": "initial-pass-1",
                "department": "営業",
                "max_concurrent": 3,
                "net_level_cap": "search",
            },
            headers=r.h("root"),
        )
        assert created.status_code == 201, created.text
        bob = created.json()
        assert bob["max_concurrent"] == 3 and bob["net_level_cap"] == "search" and bob["department"] == "営業"
        assert "password_hash" not in bob
        dup = await r.http.post(
            "/admin/v1/users",
            json={"login": "bob", "display_name": "x", "password": "initial-pass-1"},
            headers=r.h("root"),
        )
        assert dup.status_code == 409
        users = (await r.http.get("/admin/v1/users", headers=r.h("root"))).json()["users"]
        assert {u["login"] for u in users} == {"alice", "root", "bob"}

        await r.login("bob", "initial-pass-1")
        # パスワード再設定 → 古いパスワードとセッションは使えない
        patched = await r.http.patch(
            f"/admin/v1/users/{bob['id']}",
            json={"password": "reset-pass-22", "max_concurrent": 1, "net_level_cap": "browse"},
            headers=r.h("root"),
        )
        assert patched.status_code == 200 and patched.json()["max_concurrent"] == 1
        assert (await r.http.get("/v1/config", headers=r.h("bob"))).status_code == 401
        await r.login("bob", "reset-pass-22")
        cfg = (await r.http.get("/v1/config", headers=r.h("bob"))).json()
        assert cfg["limits"]["max_concurrent"] == 1
        # 無効化 → ログインもトークンも使えない
        assert (
            await r.http.patch(f"/admin/v1/users/{bob['id']}", json={"disabled": True}, headers=r.h("root"))
        ).status_code == 200
        assert (await r.http.get("/v1/config", headers=r.h("bob"))).status_code == 401
        res = await r.http.post("/v1/auth/login", json={"login": "bob", "password": "reset-pass-22"})
        assert res.status_code == 401
        assert (
            await r.http.patch("/admin/v1/users/nope", json={"disabled": True}, headers=r.h("root"))
        ).status_code == 404
        dash = await r.http.get("/admin/v1/dashboard", headers=r.h("root"))
        assert dash.status_code == 200
