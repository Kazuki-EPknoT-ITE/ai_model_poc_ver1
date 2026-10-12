"""評価ボタン（ADM-06、設計書 §8.3）: 「良い／悪い」と、本人が同意したやり取りの収集・管理者の確認・書き出し。

- 同意なし: 評価と感想だけを持つ（やり取りの中身は受け取っても保存しない）。
- 同意あり: やり取りを暗号化して保存し（status=pending）、管理者がマスキングした形で確認して承認／却下する。
  却下したら中身を消す。承認したものだけを、マスキングして学習データの候補として書き出す（JSONL）。
- マスキング: 会社の機密語辞書の語と読み、メールアドレス、電話番号、4桁以上の数字、利用者のフォルダ名。
"""

# from __future__ import annotations は使わない（関数内で作る依存性を FastAPI が解決できなくなるため）
import json
import re
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from .auth import Principal
from .db import DlpTerm, Feedback
from .gateway import Gateway
from .util import ApiError, new_ulid

MAX_MESSAGES = 200
MAX_CHARS = 200_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscriptMessage(_Strict):
    role: Literal["user", "assistant", "tool"]
    content: str = Field(max_length=50_000)


class FeedbackIn(_Strict):
    run_id: str = Field(min_length=1, max_length=64)
    rating: Literal["good", "bad"]
    comment: str | None = Field(default=None, max_length=2000)
    consent: bool = False
    transcript: list[TranscriptMessage] = Field(default_factory=list, max_length=MAX_MESSAGES)


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)0\d{1,4}-\d{1,4}-\d{3,4}(?!\d)")
_NUMBER = re.compile(r"(?<![\w])[¥$]?\d[\d,]{3,}(?:\.\d+)?")
_USER_DIR = re.compile(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\s\"']+")


def mask_text(text: str, terms: list[str]) -> str:
    """機密語・連絡先・数字・利用者名を伏せる（学習データの候補と、管理者の確認画面に使う）。"""
    out = text
    for t in sorted({t for t in terms if t}, key=len, reverse=True):
        out = re.sub(re.escape(t), "［機密語］", out, flags=re.IGNORECASE)
    out = _EMAIL.sub("［メール］", out)
    out = _PHONE.sub("［電話番号］", out)
    out = _USER_DIR.sub(r"\1［利用者］", out)
    out = _NUMBER.sub("［数字］", out)
    return out


def register_feedback(app: FastAPI, gw: Gateway, user_dep: Any, admin_dep: Any) -> None:
    UserDep = Annotated[Principal, user_dep]
    AdminDep = Annotated[Principal, admin_dep]

    async def org_terms(s: Any, org_id: str) -> list[str]:
        rows = (await s.execute(select(DlpTerm).where(DlpTerm.org_id == org_id))).scalars().all()
        out: list[str] = []
        for r in rows:
            out.append(r.term)
            out.extend(r.readings or [])
        return out

    def transcript_of(f: Feedback) -> list[dict[str, str]]:
        if f.payload_ct is None:
            return []
        data: list[dict[str, str]] = json.loads(gw.cipher.decrypt(f.id, "feedback", f.payload_ct))
        return data

    @app.post("/v1/feedback")
    async def submit(body: FeedbackIn, p: UserDep) -> dict[str, Any]:
        if sum(len(m.content) for m in body.transcript) > MAX_CHARS:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "やり取りが長すぎます（20万字まで）")
        keep = body.consent and len(body.transcript) > 0
        client_id = f"{p.user_id}:{body.run_id}"
        async with gw.db.tx() as s:
            f = (
                await s.execute(select(Feedback).where(Feedback.client_id == client_id))
            ).scalar_one_or_none()
            if f is None:
                f = Feedback(
                    id=new_ulid(),
                    client_id=client_id,
                    user_id=p.user_id,
                    org_id=p.org_id,
                    created_at=gw.clock.now(),
                )
                s.add(f)
            elif f.status in ("approved", "rejected"):
                raise ApiError(409, "CONFLICT", "この評価は管理者の確認が済んでいるため、変更できません")
            f.rating = body.rating
            f.comment = body.comment
            f.consent = keep
            f.status = "pending" if keep else "none"
            # 同意がなければ中身は持たない（送られてきても捨てる）
            f.payload_ct = (
                gw.cipher.encrypt(
                    f.id,
                    "feedback",
                    json.dumps([m.model_dump() for m in body.transcript], ensure_ascii=False).encode(),
                )
                if keep
                else None
            )
            fid, status = f.id, f.status
        await gw.audit.record(
            source="gateway",
            action="feedback.submit",
            result="ok",
            user_id=p.user_id,
            target=f"{body.run_id}:{body.rating}:{status}",
        )
        return {"id": fid, "status": status}

    @app.get("/admin/v1/feedback")
    async def list_feedback(
        _p: AdminDep,
        status: Literal["none", "pending", "approved", "rejected"] | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        async with gw.db.tx() as s:
            q = select(Feedback).order_by(Feedback.created_at.desc()).limit(limit)
            if status:
                q = q.where(Feedback.status == status)
            rows = (await s.execute(q)).scalars().all()
            counts = dict(
                (await s.execute(select(Feedback.status, func.count()).group_by(Feedback.status))).all()
            )  # noqa: C416
            ratings = dict(
                (await s.execute(select(Feedback.rating, func.count()).group_by(Feedback.rating))).all()
            )  # noqa: C416
            terms_by_org: dict[str, list[str]] = {}
            items = []
            for f in rows:
                if f.org_id not in terms_by_org:
                    terms_by_org[f.org_id] = await org_terms(s, f.org_id)
                terms = terms_by_org[f.org_id]
                items.append(
                    {
                        "id": f.id,
                        "rating": f.rating,
                        "comment": mask_text(f.comment, terms) if f.comment else None,
                        "consent": f.consent,
                        "status": f.status,
                        "created_at": f.created_at.isoformat(),
                        # 管理者にもマスキングした形だけを見せる
                        "transcript": [
                            {**m, "content": mask_text(m["content"], terms)} for m in transcript_of(f)
                        ],
                    }
                )
        return {
            "items": items,
            "counts": {k: int(v) for k, v in counts.items()},
            "ratings": {k: int(v) for k, v in ratings.items()},
        }

    async def review(fid: str, p: Principal, approve: bool) -> dict[str, Any]:
        async with gw.db.tx() as s:
            f = await s.get(Feedback, fid)
            if f is None:
                raise ApiError(404, "NOT_FOUND", "評価がありません")
            if f.status != "pending":
                raise ApiError(409, "CONFLICT", "確認待ちの評価だけを承認・却下できます")
            f.status = "approved" if approve else "rejected"
            f.reviewed_by = p.user_id
            f.reviewed_at = gw.clock.now()
            if not approve:
                f.payload_ct = None  # 却下したら中身を消す
        await gw.audit.record(
            source="admin",
            action=f"admin.feedback.{'approve' if approve else 'reject'}",
            result="ok",
            user_id=p.user_id,
            target=fid,
        )
        return {"id": fid, "status": "approved" if approve else "rejected"}

    @app.post("/admin/v1/feedback/{fid}/approve")
    async def approve(fid: str, p: AdminDep) -> dict[str, Any]:
        return await review(fid, p, True)

    @app.post("/admin/v1/feedback/{fid}/reject")
    async def reject(fid: str, p: AdminDep) -> dict[str, Any]:
        return await review(fid, p, False)

    @app.get("/admin/v1/feedback/export")
    async def export(p: AdminDep) -> Response:
        """承認済みのやり取りを、マスキングして JSONL で書き出す（ml/datagen の入力。設計書 §8.3）。"""
        lines = []
        async with gw.db.tx() as s:
            rows = (
                (
                    await s.execute(
                        select(Feedback).where(Feedback.status == "approved").order_by(Feedback.created_at)
                    )
                )
                .scalars()
                .all()
            )
            terms_by_org: dict[str, list[str]] = {}
            for f in rows:
                if f.org_id not in terms_by_org:
                    terms_by_org[f.org_id] = await org_terms(s, f.org_id)
                terms = terms_by_org[f.org_id]
                lines.append(
                    json.dumps(
                        {
                            "id": f.id,
                            "rating": f.rating,
                            "comment": mask_text(f.comment, terms) if f.comment else None,
                            "messages": [
                                {**m, "content": mask_text(m["content"], terms)} for m in transcript_of(f)
                            ],
                        },
                        ensure_ascii=False,
                    )
                )
        await gw.audit.record(
            source="admin",
            action="admin.feedback.export",
            result="ok",
            user_id=p.user_id,
            target=str(len(lines)),
        )
        return Response(
            "\n".join(lines) + ("\n" if lines else ""), media_type="application/x-ndjson; charset=utf-8"
        )
