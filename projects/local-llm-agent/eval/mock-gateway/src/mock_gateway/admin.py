"""管理 API のモック（local_llm_api.md §9.1）。メモリ上だけで持つ。管理画面（apps/admin-web）の試験用。"""

from __future__ import annotations

import csv
import io
import itertools
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .engine import GatewayError
from .scenario import Scenario

LEVELS = ("off", "search", "browse")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _mask(term: str) -> str:
    return term[:2] + "*" * max(2, len(term) - 2)


class AdminState:
    def __init__(self, scenario: Scenario):
        self.seq = itertools.count(1)
        self.users: dict[str, dict[str, Any]] = {}
        for u in scenario.users:
            self.users[f"u-{u.login}"] = {
                "id": f"u-{u.login}",
                "login": u.login,
                "display_name": u.display_name,
                "role": u.role,
                "department_id": u.department,
                "disabled": False,
                "max_concurrent": 2,
                "net_level_cap": "off",
                "created_at": _now(),
                "last_login_at": None,
            }
        self.passwords = {f"u-{u.login}": u.password for u in scenario.users}
        self.departments: dict[str, dict[str, Any]] = {
            "dev": {"id": "dev", "name": "開発", "acl_groups": ["dev"]}
        }
        self.policies: dict[str, dict[str, Any]] = {
            "org": {"scope": "org", "level_cap": "search", "monthly_search_limit": 300}
        }
        self.sites: list[dict[str, str]] = [{"pattern": "*.go.jp", "kind": "trusted"}]
        self.terms: dict[str, dict[str, Any]] = {}
        self.instructions: list[dict[str, Any]] = []
        self.models: dict[str, dict[str, Any]] = {}
        self.feedback: dict[str, dict[str, Any]] = {}
        self.slots = ["local-12b", "local-26b"]
        self.audit: list[dict[str, Any]] = []
        self.started = time.time()

    def add_feedback(self, user: str, body: dict[str, Any]) -> dict[str, Any]:
        consent = bool(body.get("consent")) and bool(body.get("transcript"))
        key = f"{user}:{body.get('run_id')}"
        fid = self.feedback.get(key, {}).get("id") or f"fb{next(self.seq)}"
        self.feedback[key] = {
            "id": fid,
            "user": user,
            "run_id": str(body.get("run_id")),
            "rating": body.get("rating"),
            "comment": body.get("comment"),
            "consent": consent,
            "status": "pending" if consent else "none",
            "created_at": _now(),
            "transcript": list(body.get("transcript") or []) if consent else [],
        }
        self.log(user, "feedback.submit", f"{body.get('run_id')}:{body.get('rating')}")
        return {"id": fid, "status": self.feedback[key]["status"]}

    def mask(self, text: str) -> str:
        out = text
        for t in sorted((t["term"] for t in self.terms.values()), key=len, reverse=True):
            out = out.replace(t, "［機密語］")
        return re.sub(r"\d[\d,]{3,}", "［数字］", out)

    def log(self, user: str, action: str, target: str) -> None:
        self.audit.append(
            {
                "at": _now(),
                "user": user,
                "source": "admin",
                "action": action,
                "target": target,
                "result": "ok",
            }
        )


def admin_router(state: AdminState, is_admin: Any) -> APIRouter:
    r = APIRouter(prefix="/admin/v1")

    def require(request: Request) -> str:
        login = getattr(request.state, "user", None)
        if not login or not is_admin(login):
            raise GatewayError(403, "FORBIDDEN", "管理者だけが使えます")
        return str(login)

    @r.get("/dashboard")
    async def dashboard(request: Request) -> dict[str, Any]:
        require(request)
        return {
            "jobs_today": 12,
            "jobs_running": 1,
            "queue_depth": 0,
            "errors_today": 0,
            "users_active_today": len(state.users),
            "latency_ms": {"p50": 1200, "p95": 3400},
            "gpu": [
                {
                    "name": "（モック）RTX 5080",
                    "temp_c": 58,
                    "util_pct": 42,
                    "mem_used_mb": 9800,
                    "mem_total_mb": 16303,
                }
            ],
        }

    @r.get("/users")
    async def users(request: Request) -> dict[str, Any]:
        require(request)
        return {"users": list(state.users.values())}

    @r.post("/users", status_code=201)
    async def create_user(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        who = require(request)
        login = str(body.get("login", "")).strip()
        if not login or not body.get("password"):
            raise GatewayError(400, "INVALID_REQUEST", "ログインIDとパスワードが必要です")
        if any(u["login"] == login for u in state.users.values()):
            raise GatewayError(409, "CONFLICT", "そのログインIDは使われています")
        uid = f"u-{login}"
        state.users[uid] = {
            "id": uid,
            "login": login,
            "display_name": body.get("display_name") or login,
            "role": body.get("role", "user"),
            "department_id": body.get("department_id"),
            "disabled": False,
            "max_concurrent": int(body.get("max_concurrent", 2)),
            "net_level_cap": body.get("net_level_cap", "off"),
            "created_at": _now(),
            "last_login_at": None,
        }
        state.passwords[uid] = str(body["password"])
        state.log(who, "admin.user.create", login)
        return state.users[uid]

    @r.patch("/users/{uid}")
    async def patch_user(uid: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        who = require(request)
        u = state.users.get(uid)
        if u is None:
            raise GatewayError(404, "NOT_FOUND", "利用者がいません")
        if body.get("net_level_cap") not in (None, *LEVELS):
            raise GatewayError(400, "INVALID_REQUEST", "net_level_cap が不正です")
        for k in ("display_name", "role", "department_id", "disabled", "max_concurrent", "net_level_cap"):
            if k in body:
                u[k] = body[k]
        if body.get("password"):
            state.passwords[uid] = str(body["password"])
        state.log(who, "admin.user.update", u["login"])
        return u

    @r.get("/departments")
    async def departments(request: Request) -> dict[str, Any]:
        require(request)
        return {"departments": list(state.departments.values())}

    @r.get("/net/policies")
    async def policies(request: Request) -> dict[str, Any]:
        require(request)
        return {"policies": list(state.policies.values())}

    @r.put("/net/policies")
    async def put_policy(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        who = require(request)
        if body.get("level_cap") not in LEVELS:
            raise GatewayError(400, "INVALID_REQUEST", "level_cap が不正です")
        scope = str(body.get("scope", "org"))
        state.policies[scope] = {
            "scope": scope,
            "level_cap": body["level_cap"],
            "monthly_search_limit": body.get("monthly_search_limit"),
        }
        state.log(who, "admin.net.policy", scope)
        return state.policies[scope]

    @r.get("/net/sites")
    async def sites(request: Request) -> dict[str, Any]:
        require(request)
        return {"sites": state.sites}

    @r.put("/net/sites")
    async def put_sites(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        who = require(request)
        state.sites = [{"pattern": str(s["pattern"]), "kind": str(s["kind"])} for s in body.get("sites", [])]
        state.log(who, "admin.net.sites", str(len(state.sites)))
        return {"sites": state.sites}

    @r.get("/dlp/terms")
    async def terms(request: Request) -> dict[str, Any]:
        require(request)
        hidden = ("term", "readings")  # 語も読みも一覧には出さない
        return {"terms": [{k: v for k, v in t.items() if k not in hidden} for t in state.terms.values()]}

    @r.post("/dlp/terms")
    async def add_terms(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        who = require(request)
        added = skipped = 0
        existing = {t["term"] for t in state.terms.values()}
        for t in body.get("terms", []):
            term = str(t.get("term", "")).strip()
            if not term or term in existing:
                skipped += 1
                continue
            tid = f"t{next(state.seq)}"
            state.terms[tid] = {
                "id": tid,
                "term": term,
                "masked": _mask(term),
                "category": t.get("category", "other"),
                "readings": [str(x).strip() for x in t.get("readings", []) if str(x).strip()],
                "updated_at": _now(),
            }
            existing.add(term)
            added += 1
        state.log(who, "admin.dlp.add", str(added))  # 語そのものは記録しない
        return {"added": added, "skipped": skipped}

    @r.delete("/dlp/terms/{tid}", status_code=204)
    async def delete_term(tid: str, request: Request) -> Response:
        who = require(request)
        state.terms.pop(tid, None)
        state.log(who, "admin.dlp.delete", tid)
        return Response(status_code=204)

    @r.get("/instructions")
    async def instructions(request: Request) -> dict[str, Any]:
        require(request)
        cur = state.instructions[-1] if state.instructions else None
        return {
            "current": cur,
            "history": [
                {"version": i["version"], "published_at": i["published_at"]}
                for i in reversed(state.instructions)
            ],
        }

    @r.post("/instructions")
    async def publish(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        who = require(request)
        text = str(body.get("body", ""))
        if len(text) / 1.5 > 1500:
            raise GatewayError(400, "INSTRUCTIONS_TOO_LONG", "共通指示は1,500トークン（約2,250字）までです")
        version = len(state.instructions) + 1
        state.instructions.append({"version": version, "body": text, "published_at": _now()})
        state.log(who, "admin.instructions.publish", f"v{version}")
        return {"version": version}

    # ---- モデル台帳（本物は server/gateway/src/llm_gateway/registry.py）----
    def evals_of(m: dict[str, Any]) -> dict[tuple[str, str], float]:
        return {(e["eval_set"], e["metric"]): e["value"] for e in m["evals"]}

    @r.get("/models")
    async def models(request: Request) -> dict[str, Any]:
        require(request)
        rows = sorted(state.models.values(), key=lambda m: m["created_at"], reverse=True)
        return {"models": rows, "slots": state.slots}

    @r.post("/models", status_code=201)
    async def register_model(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        who = require(request)
        mid = str(body.get("id", ""))
        if not mid or body.get("slot") not in state.slots:
            raise GatewayError(400, "INVALID_REQUEST", "ID と枠を指定してください")
        if mid in state.models:
            raise GatewayError(409, "CONFLICT", "その ID は登録済みです")
        state.models[mid] = {
            "id": mid,
            "slot": body["slot"],
            "base_model": body.get("base_model", ""),
            "quant": body.get("quant", ""),
            "lora": body.get("lora"),
            "status": "candidate",
            "created_at": _now(),
            "promoted_at": None,
            "evals": [],
        }
        state.log(who, "admin.model.register", mid)
        return state.models[mid]

    @r.post("/models/{mid}/evals")
    async def add_evals(mid: str, request: Request, body: dict[str, Any]) -> dict[str, Any]:
        who = require(request)
        m = state.models.get(mid)
        if m is None:
            raise GatewayError(404, "NOT_FOUND", "モデルがありません")
        latest = evals_of(m)
        for e in body.get("results", []):
            latest[(str(e["eval_set"]), str(e["metric"]))] = float(e["value"])
        m["evals"] = [{"eval_set": k[0], "metric": k[1], "value": v} for k, v in sorted(latest.items())]
        state.log(who, "admin.model.evals", mid)
        return {"added": len(body.get("results", []))}

    @r.post("/models/{mid}/promote")
    async def promote(mid: str, request: Request) -> dict[str, Any]:
        who = require(request)
        m = state.models.get(mid)
        if m is None:
            raise GatewayError(404, "NOT_FOUND", "モデルがありません")
        cur = [x for x in state.models.values() if x["status"] == "production" and x["slot"] == m["slot"]]
        if cur:
            cand = evals_of(m)
            failures = [
                {"eval_set": k[0], "metric": k[1], "production": v, "candidate": cand.get(k)}
                for k, v in sorted(evals_of(cur[0]).items())
                if cand.get(k) is None or cand[k] < v
            ]
            if failures:
                raise GatewayError(
                    409,
                    "EVAL_GATE_FAILED",
                    "評価セットで本番版を下回る指標があるため本番化できません",
                    detail={"failures": failures},
                )
        elif not m["evals"]:
            raise GatewayError(409, "EVAL_GATE_FAILED", "評価結果のないモデルは本番化できません")
        for x in cur:
            x["status"] = "retired"
        m["status"], m["promoted_at"] = "production", _now()
        state.log(who, "admin.model.promote", mid)
        return m

    @r.post("/models/{mid}/rollback")
    async def rollback(mid: str, request: Request) -> dict[str, Any]:
        who = require(request)
        m = state.models.get(mid)
        if m is None or m["status"] != "production":
            raise GatewayError(409, "CONFLICT", "本番のモデルだけを切り戻せます")
        prev = sorted(
            (
                x
                for x in state.models.values()
                if x["status"] == "retired" and x["promoted_at"] and x["slot"] == m["slot"]
            ),
            key=lambda x: x["promoted_at"],
            reverse=True,
        )
        m["status"] = "retired"
        target = prev[0] if prev else None
        if target:
            target["status"], target["promoted_at"] = "production", _now()
        state.log(who, "admin.model.rollback", mid)
        return {"slot": m["slot"], "production": target["id"] if target else None}

    # ---- 評価データ候補（ADM-06）----
    def fb_view(f: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f["id"],
            "rating": f["rating"],
            "comment": state.mask(f["comment"]) if f.get("comment") else None,
            "consent": f["consent"],
            "status": f["status"],
            "created_at": f["created_at"],
            "transcript": [{**m, "content": state.mask(str(m.get("content", "")))} for m in f["transcript"]],
        }

    @r.get("/feedback")
    async def feedback(request: Request, status: str | None = None) -> dict[str, Any]:
        require(request)
        items = [
            fb_view(f) for f in reversed(list(state.feedback.values())) if not status or f["status"] == status
        ]
        counts: dict[str, int] = {}
        ratings: dict[str, int] = {}
        for f in state.feedback.values():
            counts[f["status"]] = counts.get(f["status"], 0) + 1
            ratings[f["rating"]] = ratings.get(f["rating"], 0) + 1
        return {"items": items, "counts": counts, "ratings": ratings}

    def find_fb(fid: str) -> dict[str, Any]:
        for f in state.feedback.values():
            if f["id"] == fid:
                return f
        raise GatewayError(404, "NOT_FOUND", "評価がありません")

    @r.post("/feedback/{fid}/{decision}")
    async def review(fid: str, decision: str, request: Request) -> dict[str, Any]:
        who = require(request)
        if decision not in ("approve", "reject"):
            raise GatewayError(404, "NOT_FOUND", "ありません")
        f = find_fb(fid)
        if f["status"] != "pending":
            raise GatewayError(409, "CONFLICT", "確認待ちの評価だけを承認・却下できます")
        f["status"] = "approved" if decision == "approve" else "rejected"
        if decision == "reject":
            f["transcript"] = []
        state.log(who, f"admin.feedback.{decision}", fid)
        return {"id": fid, "status": f["status"]}

    @r.get("/feedback/export")
    async def export(request: Request) -> Response:
        who = require(request)
        rows = [fb_view(f) for f in state.feedback.values() if f["status"] == "approved"]
        state.log(who, "admin.feedback.export", str(len(rows)))
        body = "".join(
            json.dumps(
                {"id": v["id"], "rating": v["rating"], "comment": v["comment"], "messages": v["transcript"]},
                ensure_ascii=False,
            )
            + "\n"
            for v in rows
        )
        return Response(body, media_type="application/x-ndjson; charset=utf-8")

    @r.get("/audit")
    async def audit(request: Request, user: str = "", action: str = "", format: str = "json") -> Any:  # noqa: A002
        require(request)
        rows = [
            e
            for e in state.audit
            if (not user or e["user"] == user) and (not action or action in e["action"])
        ]
        if format == "csv":
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=["at", "user", "source", "action", "target", "result"])
            w.writeheader()
            w.writerows(rows)
            return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8")
        return JSONResponse({"events": list(reversed(rows))})

    return r
