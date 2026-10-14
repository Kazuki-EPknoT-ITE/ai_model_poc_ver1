"""同じ評価セットの2回の結果（基準＝ベースモデル、候補＝学習後など）を比べる（POC 条件2「ベースモデルを上回る」の判定材料）。

- 同じセット・同じ data_hash の結果どうしでしか比べない（中身が違えば比べられない）。
- item ごとの合否の入れ替わり（良くなった／悪くなった）と、雛形ごとの成功率、落ちた確認の種類を出す。
- 入れ替わった item だけを使う符号検定（両側、二項分布の正確な p 値）で、差が偶然の範囲かを示す。
  件数が少ないうちは p 値が大きくなりやすい。判定は「上回る」と「有意」を分けて書く。
"""

from __future__ import annotations

import re
from collections import Counter
from math import comb
from typing import Any


class CompareError(Exception):
    pass


def _template(item_id: str) -> str:
    # tool_tasks_synth の ID は「雛形名-連番」。それ以外は ID の先頭（英字の部分）でまとめる
    m = re.match(r"^(.*?)-\d+$", item_id)
    return m.group(1) if m else re.sub(r"\d+$", "", item_id) or item_id


def sign_test_p(improved: int, regressed: int) -> float:
    """入れ替わった n 件のうち、少ない側が k 件以下になる確率の2倍（両側）。入れ替わりが無ければ 1。"""
    n = improved + regressed
    if n == 0:
        return 1.0
    k = min(improved, regressed)
    p: float = sum(comb(n, i) for i in range(k + 1)) / float(2**n)
    return min(1.0, 2 * p)


def _items(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for it in result.get("items") or []:
        out[str(it["id"])] = it
    if not out:
        raise CompareError(f"{result.get('run_id')} に item ごとの結果がありません")
    return out


def _failed_checks(it: dict[str, Any]) -> list[str]:
    return [str(c.get("type")) for c in it.get("checks") or [] if not c.get("ok")]


def compare(base: dict[str, Any], cand: dict[str, Any], *, alpha: float = 0.05) -> dict[str, Any]:
    problems = []
    if base.get("eval_set") != cand.get("eval_set"):
        problems.append(f"評価セットが違います（{base.get('eval_set')} と {cand.get('eval_set')}）")
    if base.get("data_hash") != cand.get("data_hash"):
        problems.append("data_hash が違います（評価セットの中身が違う結果どうしは比べられません）")
    if problems:
        raise CompareError(" / ".join(problems))
    bi, ci = _items(base), _items(cand)
    ids = sorted(set(bi) & set(ci))
    missing = sorted(set(bi) ^ set(ci))
    improved = [i for i in ids if not bi[i].get("ok") and ci[i].get("ok")]
    regressed = [i for i in ids if bi[i].get("ok") and not ci[i].get("ok")]
    by_template: dict[str, dict[str, int]] = {}
    for i in ids:
        t = by_template.setdefault(_template(i), {"n": 0, "base_ok": 0, "cand_ok": 0})
        t["n"] += 1
        t["base_ok"] += int(bool(bi[i].get("ok")))
        t["cand_ok"] += int(bool(ci[i].get("ok")))
    base_ok = sum(int(bool(bi[i].get("ok"))) for i in ids)
    cand_ok = sum(int(bool(ci[i].get("ok"))) for i in ids)
    p = sign_test_p(len(improved), len(regressed))
    return {
        "eval_set": base.get("eval_set"),
        "data_hash": base.get("data_hash"),
        "base": {"run_id": base.get("run_id"), "model_id": base.get("model_id"), "success": base_ok},
        "cand": {"run_id": cand.get("run_id"), "model_id": cand.get("model_id"), "success": cand_ok},
        "n": len(ids),
        "missing_items": missing,
        "base_rate": round(base_ok / len(ids), 4) if ids else 0.0,
        "cand_rate": round(cand_ok / len(ids), 4) if ids else 0.0,
        "improved": improved,
        "regressed": regressed,
        "sign_test_p": round(p, 4),
        "cand_better": cand_ok > base_ok,
        "significant": p < alpha and len(improved) > len(regressed),
        "by_template": dict(sorted(by_template.items())),
        "failed_checks": {
            "base": dict(Counter(c for i in ids for c in _failed_checks(bi[i])).most_common()),
            "cand": dict(Counter(c for i in ids for c in _failed_checks(ci[i])).most_common()),
        },
    }


def compare_table(r: dict[str, Any]) -> str:
    lines = [
        f"評価セット {r['eval_set']}（{r['n']} 件）",
        f"基準 {r['base']['model_id']}（{r['base']['run_id']}）: {r['base']['success']}/{r['n']} = {r['base_rate']:.1%}",
        f"候補 {r['cand']['model_id']}（{r['cand']['run_id']}）: {r['cand']['success']}/{r['n']} = {r['cand_rate']:.1%}",
        f"良くなった {len(r['improved'])} 件 / 悪くなった {len(r['regressed'])} 件（符号検定 p = {r['sign_test_p']}）",
        "",
        "雛形ごとの成功数（基準 → 候補）:",
    ]
    width = max((len(t) for t in r["by_template"]), default=0)
    for t, v in r["by_template"].items():
        mark = " ↑" if v["cand_ok"] > v["base_ok"] else " ↓" if v["cand_ok"] < v["base_ok"] else ""
        lines.append(f"  {t.ljust(width)}  {v['base_ok']}/{v['n']} → {v['cand_ok']}/{v['n']}{mark}")
    if r["regressed"]:
        lines.append(f"悪くなった item: {', '.join(r['regressed'])}")
    if r["missing_items"]:
        lines.append(f"片方にしか無い item（比較から除外）: {', '.join(r['missing_items'])}")
    verdict = (
        "候補がベースを上回る（有意）"
        if r["significant"]
        else "候補がベースを上回る（ただし差は偶然の範囲を出ない）"
        if r["cand_better"]
        else "候補はベースを上回らない"
    )
    lines.append(f"判定: {verdict}")
    return "\n".join(lines)


__all__ = ["CompareError", "compare", "compare_table", "sign_test_p"]
