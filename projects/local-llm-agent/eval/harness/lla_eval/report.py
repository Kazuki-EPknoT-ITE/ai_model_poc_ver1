"""結果の組み立て・合否表・モデル台帳向けの行（local_llm_test_spec.md §3.1、design §8.4）。

合否の既定は「未達」。値が無い（証拠が無い）指標は合格にしない。
"""

from __future__ import annotations

import unicodedata
from typing import Any

from .manifest import Manifest, Metric
from .scoring import Score

VERDICT_PASS = "合格"  # noqa: S105 - 判定の文言
VERDICT_FAIL = "未達"
VERDICT_NO_EVIDENCE = "未達（証拠なし）"


def evaluate_metrics(manifest: Manifest, score: Score) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in manifest.metrics:
        value = score.metrics.get(m.name)
        ok = m.check(value)
        verdict = VERDICT_NO_EVIDENCE if ok is None else (VERDICT_PASS if ok else VERDICT_FAIL)
        rows.append({"name": m.name, "target": m.target, "value": value, "verdict": verdict})
    for name, value in score.metrics.items():
        if not any(m.name == name for m in manifest.metrics):
            rows.append({"name": name, "target": "（参考）", "value": value, "verdict": "-"})
    return rows


def overall(rows: list[dict[str, Any]]) -> str:
    judged = [r for r in rows if r["verdict"] != "-"]
    if not judged:
        return VERDICT_NO_EVIDENCE
    return VERDICT_PASS if all(r["verdict"] == VERDICT_PASS for r in judged) else VERDICT_FAIL


def build_result(
    loaded: dict[str, Any], manifest: Manifest, record: dict[str, Any], score: Score
) -> dict[str, Any]:
    rows = evaluate_metrics(manifest, score)
    return {
        "eval_set": manifest.name,
        "set_version": manifest.version,
        "requirement_ids": manifest.requirement_ids,
        "run_id": record.get("run_id"),
        "model_id": record.get("model_id"),
        "gateway": record.get("gateway"),
        "gateway_kind": record.get("gateway_kind"),
        "started_at": record.get("started_at"),
        "duration_s": record.get("duration_s"),
        "config": {
            "runner": manifest.runner,
            "scorer": manifest.scorer,
            "repeat": manifest.repeat,
            "seed": manifest.seed,
            "temperature": manifest.temperature,
            "timeout_per_item_s": manifest.timeout_per_item_s,
            **(record.get("config") or {}),
        },
        "data_hash": loaded.get("data_hash"),
        "split_check": loaded.get("split_check"),
        "metrics": rows,
        "verdict": overall(rows),
        "info": score.info,
        "items": score.items,
        "notes": record.get("notes", []),
    }


def registry_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """モデル台帳に登録する形（[{eval_set, metric, value}]）。"""
    return [
        {"eval_set": result["eval_set"], "metric": r["name"], "value": r["value"]}
        for r in result["metrics"]
        if r["value"] is not None
    ]


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _width(s))


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return str(v)


def table(result: dict[str, Any]) -> str:
    head = ["指標", "目標", "実測", "合否"]
    body = [[r["name"], r["target"], _fmt(r["value"]), r["verdict"]] for r in result["metrics"]]
    widths = [max(_width(x) for x in col) for col in zip(head, *body, strict=False)]
    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    out = [
        f"評価セット {result['eval_set']} v{result['set_version']}  run={result['run_id']}  model={result.get('model_id')}",
        f"接続先 {result.get('gateway')}（{result.get('gateway_kind')}）  学習データとの照合: {(result.get('split_check') or {}).get('status')}",
        line,
        "| " + " | ".join(_pad(h, w) for h, w in zip(head, widths, strict=True)) + " |",
        line,
    ]
    for row in body:
        out.append("| " + " | ".join(_pad(c, w) for c, w in zip(row, widths, strict=True)) + " |")
    out.append(line)
    out.append(f"総合: {result['verdict']}")
    failed = [i for i in result["items"] if not i.get("ok")]
    if failed:
        out.append(f"不合格の item: {', '.join(str(i['id']) for i in failed[:20])}")
    return "\n".join(out)


__all__ = ["Metric", "build_result", "evaluate_metrics", "overall", "registry_rows", "table"]
