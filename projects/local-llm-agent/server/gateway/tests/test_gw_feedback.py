"""評価ボタン（ADM-06）: 同意したやり取りだけを暗号化して持ち、管理者はマスキングした形で確認・承認し、書き出す。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from llm_gateway.db import Feedback
from llm_gateway.feedback import mask_text

from .gw_helpers import rig

pytestmark = pytest.mark.anyio

TRANSCRIPT = [
    {"role": "user", "content": "平井丸の請求書を Excel にまとめて"},
    {
        "role": "tool",
        "content": "請求元 Example Co. 金額 1,234,567 口座 1234567 連絡先 taro@example.co.jp 03-1234-5678",
    },
    {
        "role": "assistant",
        "content": '{"type":"final","summary":"C:\\\\Users\\\\yamada\\\\Desktop\\\\送金明細.xlsx を作りました"}',
    },
]


def test_mask_text() -> None:
    out = mask_text(
        TRANSCRIPT[1]["content"] + " ひらいまる C:\\Users\\yamada\\a.xlsx", ["平井丸", "ひらいまる"]
    )
    assert "1,234,567" not in out and "1234567" not in out and "［数字］" in out
    assert "taro@example.co.jp" not in out and "［メール］" in out
    assert "03-1234-5678" not in out and "［電話番号］" in out
    assert "ひらいまる" not in out and "［機密語］" in out
    assert "yamada" not in out and "Users\\［利用者］" in out
    assert "Example Co." in out  # 辞書に無い語はそのまま


async def test_feedback_consent_review_and_export(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        await r.user("root", role="admin")
        h = r.h("root")
        await r.http.post(
            "/admin/v1/dlp/terms", json={"terms": [{"term": "平井丸", "readings": ["ひらいまる"]}]}, headers=h
        )

        # 同意なし: 中身を送ってきても保存しない
        no = await r.http.post(
            "/v1/feedback",
            json={
                "run_id": "run-1",
                "rating": "bad",
                "comment": "遅い",
                "consent": False,
                "transcript": TRANSCRIPT,
            },
            headers=r.h("alice"),
        )
        assert no.json()["status"] == "none"
        # 同意あり: 確認待ち。DB には暗号文だけ
        yes = await r.http.post(
            "/v1/feedback",
            json={
                "run_id": "run-2",
                "rating": "good",
                "comment": "平井丸の件で助かった",
                "consent": True,
                "transcript": TRANSCRIPT,
            },
            headers=r.h("alice"),
        )
        assert yes.json()["status"] == "pending"
        async with r.gw.db.tx() as s:
            rows = {f.client_id.split(":")[1]: f for f in (await s.execute(select(Feedback))).scalars().all()}
        assert rows["run-1"].payload_ct is None
        assert rows["run-2"].payload_ct is not None and b"1234567" not in rows["run-2"].payload_ct
        assert b"\xe5\xb9\xb3\xe4\xba\x95" not in rows["run-2"].payload_ct  # 「平井」が平文で無い

        # 利用者は管理 API を使えない
        assert (await r.http.get("/admin/v1/feedback", headers=r.h("alice"))).status_code == 403
        listed = (await r.http.get("/admin/v1/feedback?status=pending", headers=h)).json()
        assert listed["counts"] == {"none": 1, "pending": 1}
        assert listed["ratings"] == {"bad": 1, "good": 1}
        (item,) = listed["items"]
        body = json.dumps(item, ensure_ascii=False)
        for secret in ("平井丸", "1234567", "taro@example.co.jp", "yamada"):
            assert secret not in body, secret
        assert item["comment"] == "［機密語］の件で助かった"

        fid = item["id"]
        assert (
            await r.http.post(f"/admin/v1/feedback/{rows['run-1'].id}/approve", headers=h)
        ).status_code == 409
        assert (await r.http.post(f"/admin/v1/feedback/{fid}/approve", headers=h)).json()[
            "status"
        ] == "approved"
        # 確認済みのものは利用者が書き換えられない
        again = await r.http.post(
            "/v1/feedback", json={"run_id": "run-2", "rating": "bad", "consent": False}, headers=r.h("alice")
        )
        assert again.status_code == 409

        export = await r.http.get("/admin/v1/feedback/export", headers=h)
        assert export.headers["content-type"].startswith("application/x-ndjson")
        lines = [json.loads(x) for x in export.text.splitlines()]
        assert len(lines) == 1 and lines[0]["rating"] == "good" and len(lines[0]["messages"]) == 3
        assert "平井丸" not in export.text and "1234567" not in export.text

        audit = (await r.http.get("/admin/v1/audit", headers=h)).text
        for action in ("feedback.submit", "admin.feedback.approve", "admin.feedback.export"):
            assert action in audit


async def test_reject_deletes_the_transcript(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        await r.user("root", role="admin")
        fid = (
            await r.http.post(
                "/v1/feedback",
                json={"run_id": "run-9", "rating": "bad", "consent": True, "transcript": TRANSCRIPT[:1]},
                headers=r.h("alice"),
            )
        ).json()["id"]
        assert (await r.http.post(f"/admin/v1/feedback/{fid}/reject", headers=r.h("root"))).json()[
            "status"
        ] == "rejected"
        async with r.gw.db.tx() as s:
            f = await s.get(Feedback, fid)
            assert f is not None and f.payload_ct is None
        assert (await r.http.get("/admin/v1/feedback/export", headers=r.h("root"))).text == ""


async def test_feedback_limits(db_url: str) -> None:
    async with rig(db_url) as r:
        await r.user("alice")
        big = [{"role": "tool", "content": "あ" * 50_000}] * 5
        res = await r.http.post(
            "/v1/feedback",
            json={"run_id": "r", "rating": "good", "consent": True, "transcript": big},
            headers=r.h("alice"),
        )
        assert res.status_code == 413
        bad = await r.http.post("/v1/feedback", json={"run_id": "r", "rating": "meh"}, headers=r.h("alice"))
        assert bad.status_code == 400
