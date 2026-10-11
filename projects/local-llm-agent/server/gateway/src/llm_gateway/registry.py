"""モデル台帳（設計書 §8.4、要件 7.4）: 登録・評価結果・本番化（評価で旧版以上を強制）・切り戻し。

台帳の1行は「ある枠（slot = Gateway のモデル ID。local-12b など）に載せる版」。LoRA を使う版は、
本番化のときに推論エンジンへ読み込ませ、その枠の推論はその LoRA の名前で行う。
"""

# from __future__ import annotations は使わない（関数内で作る AdminDep を FastAPI が解決できなくなるため）
import logging
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .auth import Principal
from .db import EvalResult, ModelRegistry
from .gateway import Gateway
from .util import ApiError

log = logging.getLogger("llm_gateway.registry")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelIn(_Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9._+-]{1,120}$")
    slot: str = Field(min_length=1, max_length=64)  # Gateway のモデル ID
    base_model: str = Field(min_length=1, max_length=200)
    quant: str = Field(min_length=1, max_length=40)
    lora: str | None = Field(default=None, max_length=120)
    lora_path: str | None = Field(default=None, max_length=1024)
    train_data_hash: str | None = Field(default=None, max_length=128)
    protocol_version: str = "1.0"


class EvalIn(_Strict):
    eval_set: str = Field(min_length=1, max_length=80)
    metric: str = Field(min_length=1, max_length=80)
    value: float


class EvalsIn(_Strict):
    results: list[EvalIn] = Field(min_length=1, max_length=500)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


async def _latest_evals(s: Any, model_id: str) -> dict[tuple[str, str], float]:
    rows = (
        (
            await s.execute(
                select(EvalResult).where(EvalResult.model_id == model_id).order_by(EvalResult.run_at)
            )
        )
        .scalars()
        .all()
    )
    out: dict[tuple[str, str], float] = {}
    for r in rows:  # 古い順なので、後の値で上書きされて最新が残る
        out[(r.eval_set, r.metric)] = float(r.value)
    return out


def _view(m: ModelRegistry, evals: dict[tuple[str, str], float]) -> dict[str, Any]:
    cfg = m.serve_config or {}
    return {
        "id": m.id,
        "slot": cfg.get("slot"),
        "base_model": m.base_model,
        "quant": m.quant,
        "lora": m.lora,
        "status": m.status,
        "train_data_hash": m.train_data_hash,
        "protocol_version": m.protocol_version,
        "created_at": _iso(m.created_at),
        "promoted_at": _iso(m.promoted_at),
        "evals": [{"eval_set": k[0], "metric": k[1], "value": v} for k, v in sorted(evals.items())],
    }


def gate(
    candidate: dict[tuple[str, str], float], production: dict[tuple[str, str], float]
) -> list[dict[str, Any]]:
    """本番化の条件: 本番版が持つすべての指標で、候補が同じ以上（要件 7.4「評価セットで旧版以上」）。"""
    failures = []
    for key, prod in sorted(production.items()):
        cand = candidate.get(key)
        if cand is None or cand < prod:
            failures.append({"eval_set": key[0], "metric": key[1], "production": prod, "candidate": cand})
    return failures


async def activate(gw: Gateway, m: ModelRegistry | None, slot: str) -> None:
    """その枠の推論で使う LoRA を切り替える（m が None なら基本の設定に戻す）。"""
    eng = gw.engines.get(slot)
    if eng is None:
        return
    cfg = (m.serve_config or {}) if m else {}
    await eng.activate_lora(m.lora if m else None, cfg.get("lora_path"))


async def apply_production(gw: Gateway) -> None:
    """起動時に、台帳の本番版を推論エンジンへ反映する。失敗しても起動は止めない（基本の設定で動く）。"""
    async with gw.db.tx() as s:
        rows = (
            (await s.execute(select(ModelRegistry).where(ModelRegistry.status == "production")))
            .scalars()
            .all()
        )
    for m in rows:
        slot = (m.serve_config or {}).get("slot")
        try:
            await activate(gw, m, str(slot))
            log.info("model registry: slot=%s uses %s", slot, m.id)
        except Exception as e:  # noqa: BLE001 - 起動は続ける
            log.warning("model registry: failed to activate %s: %s", m.id, type(e).__name__)


def register_registry(app: FastAPI, gw: Gateway, admin_dep: Any) -> None:
    AdminDep = Annotated[Principal, admin_dep]

    async def log_admin(p: Principal, action: str, target: str) -> None:
        await gw.audit.record(source="admin", action=action, result="ok", user_id=p.user_id, target=target)

    @app.get("/admin/v1/models")
    async def models(_p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            rows = (
                (await s.execute(select(ModelRegistry).order_by(ModelRegistry.created_at.desc())))
                .scalars()
                .all()
            )
            return {
                "models": [_view(m, await _latest_evals(s, m.id)) for m in rows],
                "slots": [m.id for m in gw.settings.models],
            }

    @app.post("/admin/v1/models", status_code=201)
    async def register(body: ModelIn, p: AdminDep) -> dict[str, Any]:
        if body.slot not in gw.engines:
            raise ApiError(400, "INVALID_REQUEST", f"枠 {body.slot} はありません")
        if (body.lora is None) != (body.lora_path is None):
            raise ApiError(400, "INVALID_REQUEST", "lora と lora_path は両方指定してください")
        async with gw.db.tx() as s:
            if await s.get(ModelRegistry, body.id):
                raise ApiError(409, "CONFLICT", "その ID は登録済みです")
            m = ModelRegistry(
                id=body.id,
                base_model=body.base_model,
                quant=body.quant,
                lora=body.lora,
                train_data_hash=body.train_data_hash,
                protocol_version=body.protocol_version,
                status="candidate",
                serve_config={"slot": body.slot, "lora_path": body.lora_path},
                created_at=gw.clock.now(),
            )
            s.add(m)
            view = _view(m, {})
        await log_admin(p, "admin.model.register", body.id)
        return view

    @app.post("/admin/v1/models/{model_id}/evals")
    async def add_evals(model_id: str, body: EvalsIn, p: AdminDep) -> dict[str, Any]:
        now = gw.clock.now()
        async with gw.db.tx() as s:
            if await s.get(ModelRegistry, model_id) is None:
                raise ApiError(404, "NOT_FOUND", "モデルがありません")
            for r in body.results:
                s.add(
                    EvalResult(
                        model_id=model_id,
                        eval_set=r.eval_set,
                        metric=r.metric,
                        value=Decimal(str(r.value)),
                        run_at=now,
                    )
                )
        await log_admin(p, "admin.model.evals", f"{model_id}:{len(body.results)}")
        return {"added": len(body.results)}

    @app.post("/admin/v1/models/{model_id}/promote")
    async def promote(model_id: str, p: AdminDep) -> dict[str, Any]:
        async with gw.db.tx() as s:
            m = await s.get(ModelRegistry, model_id)
            if m is None:
                raise ApiError(404, "NOT_FOUND", "モデルがありません")
            if m.status == "production":
                raise ApiError(409, "CONFLICT", "既に本番です")
            slot = (m.serve_config or {}).get("slot")
            current = (
                (
                    await s.execute(
                        select(ModelRegistry)
                        .where(ModelRegistry.status == "production")
                        .order_by(ModelRegistry.promoted_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            current = [c for c in current if (c.serve_config or {}).get("slot") == slot]
            cand_evals = await _latest_evals(s, m.id)
            if current:
                failures = gate(cand_evals, await _latest_evals(s, current[0].id))
                if failures:
                    raise ApiError(
                        409,
                        "EVAL_GATE_FAILED",
                        "評価セットで本番版を下回る指標があるため本番化できません",
                        detail={"failures": failures},
                    )
            elif not cand_evals:
                raise ApiError(409, "EVAL_GATE_FAILED", "評価結果のないモデルは本番化できません")
        # 先にエンジンへ反映し、成功したら台帳を更新する（読み込みに失敗したら本番にしない）
        try:
            await activate(gw, m, str(slot))
        except Exception as e:  # noqa: BLE001
            raise ApiError(
                502,
                "ENGINE_ERROR",
                f"推論エンジンへの反映に失敗しました（{type(e).__name__}）",
                retryable=True,
            ) from None
        async with gw.db.tx() as s:
            for c in current:
                row = await s.get(ModelRegistry, c.id)
                assert row is not None
                row.status = "retired"
            row = await s.get(ModelRegistry, model_id)
            assert row is not None
            row.status = "production"
            row.promoted_at = gw.clock.now()
            view = _view(row, await _latest_evals(s, model_id))
        await log_admin(p, "admin.model.promote", model_id)
        return view

    @app.post("/admin/v1/models/{model_id}/rollback")
    async def rollback(model_id: str, p: AdminDep) -> dict[str, Any]:
        """本番の model_id を退役させ、同じ枠で直前に本番だった版に戻す（無ければ基本の設定に戻す）。"""
        async with gw.db.tx() as s:
            m = await s.get(ModelRegistry, model_id)
            if m is None or m.status != "production":
                raise ApiError(409, "CONFLICT", "本番のモデルだけを切り戻せます")
            slot = (m.serve_config or {}).get("slot")
            prev = [
                r
                for r in (
                    await s.execute(
                        select(ModelRegistry)
                        .where(ModelRegistry.status == "retired", ModelRegistry.promoted_at.is_not(None))
                        .order_by(ModelRegistry.promoted_at.desc())
                    )
                ).scalars()
                if (r.serve_config or {}).get("slot") == slot
            ]
            target = prev[0] if prev else None
        await activate(gw, target, str(slot))
        async with gw.db.tx() as s:
            row = await s.get(ModelRegistry, model_id)
            assert row is not None
            row.status = "retired"
            if target is not None:
                t = await s.get(ModelRegistry, target.id)
                assert t is not None
                t.status = "production"
                t.promoted_at = gw.clock.now()
        await log_admin(p, "admin.model.rollback", f"{model_id}->{target.id if target else 'base'}")
        return {"slot": slot, "production": target.id if target else None}
