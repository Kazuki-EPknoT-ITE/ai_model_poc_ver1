"""社内文書検索の評価（試験仕様 §3.2 `rag_qa`: 上位5件に根拠を含む割合 = Recall@5、目標 90%）。

item: `{ id, question, acl_user }`、正解: `{ evidence: [{ path, location? }] }`。
1行1件の JSONL に `{"id", "question", "acl_user" or "acl_groups", "evidence": [...]}` をまとめて置く。
acl_user は利用者 → acl_groups の対応表（YAML）で解決する。
根拠のどれか1つが上位 k 件に入れば正解（location を書いた根拠は、hit の location に含まれることも条件）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from llm_rag.model import norm_path
from llm_rag.search import Hit

SearchFn = Callable[[str, list[str], int], list[Hit]]


@dataclass(frozen=True)
class Evidence:
    path: str
    location: str | None = None


@dataclass
class EvalItem:
    id: str
    question: str
    acl_groups: list[str]
    evidence: list[Evidence]


@dataclass
class EvalResult:
    k: int
    total: int
    found: int
    misses: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.found / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": f"recall@{self.k}",
            "value": round(self.recall, 4),
            "total": self.total,
            "found": self.found,
            "misses": self.misses,
        }


def is_evidence_hit(hit: Hit, ev: Evidence) -> bool:
    if norm_path(hit.path) != norm_path(ev.path):
        return False
    return ev.location is None or ev.location in hit.location


def recall_at_k(items: Iterable[EvalItem], search: SearchFn, k: int = 5) -> EvalResult:
    res = EvalResult(k=k, total=0, found=0)
    for it in items:
        res.total += 1
        hits = search(it.question, it.acl_groups, k)[:k]
        if any(is_evidence_hit(h, ev) for h in hits for ev in it.evidence):
            res.found += 1
        else:
            res.misses.append(it.id)  # ID だけを残す（質問文は残さない）
    return res


def load_items(path: Path, users: dict[str, list[str]] | None = None) -> list[EvalItem]:
    items: list[EvalItem] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if "acl_groups" in raw:
            groups = [str(g) for g in raw["acl_groups"]]
        else:
            user = str(raw.get("acl_user", ""))
            if users is None or user not in users:
                raise ValueError(f"{n} 行目: acl_user の所属グループが対応表にありません")
            groups = users[user]
        ev = [Evidence(str(e["path"]), e.get("location")) for e in raw["evidence"]]
        items.append(EvalItem(str(raw["id"]), str(raw["question"]), groups, ev))
    return items


def load_users(path: Path) -> dict[str, list[str]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(u): [str(g) for g in gs] for u, gs in raw.items()}
