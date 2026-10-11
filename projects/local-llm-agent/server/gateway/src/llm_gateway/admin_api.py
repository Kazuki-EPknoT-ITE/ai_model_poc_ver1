"""管理 API の残り（local_llm_api.md §9.1）: 部門・ネットの上限とサイト・機密語辞書・共通指示・監査ログ。

変更系の操作は監査ログに source=admin で残す。機密語の中身はログにも応答にも出さない（一覧は伏せ字）。
"""

# from __future__ import annotations は使わない（関数内で作る AdminDep を FastAPI が解決できなくなるため）

import csv
import io
from datetime import datetime
from typing import Annotated, Any

from fastapi import FastAPI, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select

from .auth import Principal
from .db import AuditEvent, Department, DlpTerm, Instruction, NetPolicy, NetSite
from .gateway import Gateway
from .util import NET_LEVELS, ApiError

INSTRUCTIONS_MAX_TOKENS = 1500  # 文字数÷1.5 の概算（prompts §3）
CATEGORIES = ("client", "vessel", "project", "other")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DepartmentIn(_Strict):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    acl_groups: list[str] = Field(default_factory=list)


class DepartmentPatch(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    acl_groups: list[str] | None = None


class PolicyIn(_Strict):
    scope: str = Field(pattern=r"^(org|department:.{1,64})$")
    level_cap: str
    monthly_search_limit: int | None = Field(default=None, ge=0)


class SiteIn(_Strict):
    pattern: str = Field(pattern=r"^(\*\.)?[A-Za-z0-9.-]{1,253}$")
    kind: str = Field(pattern=r"^(block|trusted)$")


class SitesIn(_Strict):
    sites: list[SiteIn] = Field(max_length=5000)


class TermIn(_Strict):
    term: str = Field(min_length=1, max_length=200)
    category: str = "other"


class TermsIn(_Strict):
    terms: list[TermIn] = Field(min_length=1, max_length=10000)


class InstructionIn(_Strict):
    body: str = Field(max_length=20000)


def mask(term: str) -> str:
    return term[:2] + "*" * max(2, len(term) - 2)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def register_admin(app: FastAPI, gw: Gateway, admin_dep: Any) -> None:
    AdminDep = Annotated[Principal, admin_dep]
    org = gw.profile.org.id

    async def log(p: Principal, action: str, target: str | None = None) -> None:
        await gw.audit.record(source="admin", action=action, result="ok", user_id=p.user_id, target=target)

    # ---------------------------------------------------------------- 部門
    @app.get("/admin/v1/departments")
    async def departments(_p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            rows = (await s.execute(select(Department).order_by(Department.id))).scalars().all()
            return {
                "departments": [
                    {"id": d.id, "name": d.name, "acl_groups": list(d.acl_groups or [])} for d in rows
                ]
            }

    @app.post("/admin/v1/departments", status_code=201)
    async def create_department(body: DepartmentIn, p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            if await s.get(Department, body.id):
                raise ApiError(409, "CONFLICT", "その部門 ID は使われています")
            s.add(Department(id=body.id, org_id=org, name=body.name, acl_groups=body.acl_groups))
        await log(p, "admin.department.create", body.id)
        return body.model_dump()

    @app.patch("/admin/v1/departments/{dep_id}")
    async def patch_department(dep_id: str, body: DepartmentPatch, p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            d = await s.get(Department, dep_id)
            if d is None:
                raise ApiError(404, "NOT_FOUND", "部門がありません")
            if body.name is not None:
                d.name = body.name
            if body.acl_groups is not None:
                d.acl_groups = body.acl_groups
            out = {"id": d.id, "name": d.name, "acl_groups": list(d.acl_groups or [])}
        await log(p, "admin.department.update", dep_id)
        return out

    # ---------------------------------------------------------------- ネット
    @app.get("/admin/v1/net/policies")
    async def policies(_p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            rows = (await s.execute(select(NetPolicy).order_by(NetPolicy.scope))).scalars().all()
            out = [
                {"scope": r.scope, "level_cap": r.level_cap, "monthly_search_limit": r.monthly_search_limit}
                for r in rows
            ]
        if not any(r["scope"] == "org" for r in out):
            # 全社の行が無ければ会社プロファイルの既定値を見せる
            out.insert(
                0,
                {
                    "scope": "org",
                    "level_cap": gw.profile.net.level_cap,
                    "monthly_search_limit": gw.profile.net.monthly_search_limit,
                },
            )
        return {"policies": out}

    @app.put("/admin/v1/net/policies")
    async def put_policy(body: PolicyIn, p: AdminDep) -> dict[str, Any]:
        if body.level_cap not in NET_LEVELS:
            raise ApiError(400, "INVALID_REQUEST", "level_cap が不正です")
        async with gw.db.tx() as s:
            row = await s.get(NetPolicy, body.scope)
            if row is None:
                s.add(
                    NetPolicy(
                        scope=body.scope,
                        level_cap=body.level_cap,
                        monthly_search_limit=body.monthly_search_limit,
                    )
                )
            else:
                row.level_cap = body.level_cap
                row.monthly_search_limit = body.monthly_search_limit
        await log(p, "admin.net.policy", f"{body.scope}={body.level_cap}")
        return body.model_dump()

    @app.get("/admin/v1/net/sites")
    async def sites(_p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            rows = (await s.execute(select(NetSite).order_by(NetSite.pattern))).scalars().all()
            return {"sites": [{"pattern": r.pattern, "kind": r.kind} for r in rows]}

    @app.put("/admin/v1/net/sites")
    async def put_sites(body: SitesIn, p: AdminDep) -> dict[str, Any]:
        uniq = {x.pattern.lower(): x for x in body.sites}
        async with gw.db.tx() as s:
            await s.execute(delete(NetSite))
            s.add_all(NetSite(pattern=x.pattern.lower(), kind=x.kind) for x in uniq.values())
        await log(p, "admin.net.sites", str(len(uniq)))
        return {"sites": [{"pattern": k, "kind": v.kind} for k, v in sorted(uniq.items())]}

    # ---------------------------------------------------------------- 機密語辞書
    @app.get("/admin/v1/dlp/terms")
    async def terms(_p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            rows = (
                (await s.execute(select(DlpTerm).where(DlpTerm.org_id == org).order_by(DlpTerm.id)))
                .scalars()
                .all()
            )
            return {
                "terms": [
                    {
                        "id": str(r.id),
                        "masked": mask(r.term),
                        "category": r.category,
                        "updated_at": _iso(r.updated_at),
                    }
                    for r in rows
                ]
            }

    @app.post("/admin/v1/dlp/terms")
    async def add_terms(body: TermsIn, p: AdminDep) -> dict[str, Any]:
        added = skipped = 0
        async with gw.db.tx() as s:
            existing = set((await s.execute(select(DlpTerm.term).where(DlpTerm.org_id == org))).scalars())
            for t in body.terms:
                term = t.term.strip()
                if not term or term in existing:
                    skipped += 1
                    continue
                cat = t.category if t.category in CATEGORIES else "other"
                s.add(
                    DlpTerm(
                        org_id=org, term=term, category=cat, updated_by=p.user_id, updated_at=gw.clock.now()
                    )
                )
                existing.add(term)
                added += 1
        await log(p, "admin.dlp.add", str(added))  # 語そのものは記録しない
        return {"added": added, "skipped": skipped}

    @app.delete("/admin/v1/dlp/terms/{term_id}", status_code=204)
    async def delete_term(term_id: int, p: AdminDep) -> Response:
        async with gw.db.tx() as s:
            await s.execute(delete(DlpTerm).where(DlpTerm.id == term_id, DlpTerm.org_id == org))
        await log(p, "admin.dlp.delete", str(term_id))
        return Response(status_code=204)

    # ---------------------------------------------------------------- 共通指示（AGT-10）
    @app.get("/admin/v1/instructions")
    async def instructions(_p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            rows = (
                (
                    await s.execute(
                        select(Instruction)
                        .where(Instruction.org_id == org)
                        .order_by(Instruction.version.desc())
                    )
                )
                .scalars()
                .all()
            )
            cur = rows[0] if rows else None
            return {
                "current": {"version": cur.version, "body": cur.body, "published_at": _iso(cur.published_at)}
                if cur
                else None,
                "history": [{"version": r.version, "published_at": _iso(r.published_at)} for r in rows],
            }

    @app.post("/admin/v1/instructions")
    async def publish(body: InstructionIn, p: AdminDep) -> dict[str, Any]:
        if len(body.body) / 1.5 > INSTRUCTIONS_MAX_TOKENS:
            raise ApiError(400, "INSTRUCTIONS_TOO_LONG", "共通指示は1,500トークン（約2,250字）までです")
        async with gw.db.tx() as s:
            last = (
                await s.execute(select(func.max(Instruction.version)).where(Instruction.org_id == org))
            ).scalar_one()
            version = int(last or 0) + 1
            s.add(Instruction(org_id=org, version=version, body=body.body, published_at=gw.clock.now()))
        await log(p, "admin.instructions.publish", f"v{version}")
        return {"version": version}

    # ---------------------------------------------------------------- 監査ログ
    @app.get("/admin/v1/audit", response_model=None)
    async def audit(
        _p: AdminDep,
        user: str = "",
        action: str = "",
        from_: Annotated[str, Query(alias="from")] = "",
        to: str = "",
        limit: int = 500,
        format: str = "json",  # noqa: A002 - API のパラメータ名
    ) -> Response | dict[str, Any]:
        q = (
            select(AuditEvent)
            .order_by(AuditEvent.at.desc(), AuditEvent.id.desc())
            .limit(max(1, min(limit, 10000)))
        )
        if user:
            q = q.where(AuditEvent.user_id == user)
        if action:
            q = q.where(AuditEvent.action.contains(action))
        if from_:
            q = q.where(AuditEvent.at >= datetime.fromisoformat(from_))
        if to:
            q = q.where(AuditEvent.at < datetime.fromisoformat(to))
        async with gw.db.tx() as s:
            rows = (await s.execute(q)).scalars().all()
            events = [
                {
                    "at": _iso(r.at),
                    "user": r.user_id,
                    "source": r.source,
                    "action": r.action,
                    "target": r.target,
                    "result": r.result,
                }
                for r in rows
            ]
        if format == "csv":
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=["at", "user", "source", "action", "target", "result"])
            w.writeheader()
            w.writerows(events)
            return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8")
        return JSONResponse({"events": events})
