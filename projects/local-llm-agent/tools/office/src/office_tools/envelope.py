"""ToolResult の封筒（protocol §2.3）と、長い結果の分割（§2.5）。"""

from __future__ import annotations

import json
import math
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from office_tools.errors import ToolFailure

TOKEN_BUDGET = 6000
"""1回の結果の目安（トークン）。"""

CHARS_PER_TOKEN = 1.5
MAX_SINGLE_ITEM_FACTOR = 3
"""1要素だけで予算のこの倍を超える場合は RESULT_TOO_LARGE にする。"""


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def estimate_tokens(obj: Any) -> int:
    return math.ceil(len(dumps(obj)) / CHARS_PER_TOKEN)


def untrusted_block(ref: str, text: str, source: str = "file") -> dict[str, Any]:
    return {"source": source, "ref": ref[:1024], "text": text}


@dataclass
class Outcome:
    """ツールの戻り値（封筒にする前）。"""

    data: dict[str, Any]
    untrusted: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    continuation: str | None = None


def success(outcome: Outcome) -> dict[str, Any]:
    env: dict[str, Any] = {"ok": True, "data": outcome.data}
    if outcome.untrusted:
        env["untrusted"] = outcome.untrusted
    if outcome.continuation:
        env["continuation"] = outcome.continuation
    if outcome.artifacts:
        env["artifacts"] = outcome.artifacts
    if outcome.warnings:
        env["warnings"] = [w[:500] for w in outcome.warnings]
    env["tokens_estimate"] = 0
    env["tokens_estimate"] = estimate_tokens(env)
    return env


def failure(err: ToolFailure) -> dict[str, Any]:
    env: dict[str, Any] = {"ok": False, "error": err.to_error(), "tokens_estimate": 0}
    env["tokens_estimate"] = estimate_tokens(env)
    return env


# ---------------------------------------------------------------- pagination


@dataclass
class Item:
    """分割の単位。`fields` は data のリスト項目名 → この要素の値。"""

    fields: dict[str, Any]
    untrusted: list[dict[str, Any]] = field(default_factory=list)

    def size_chars(self) -> int:
        n = len(dumps(self.fields))
        for u in self.untrusted:
            n += len(u["text"]) + len(u["ref"]) + 40
        return n


@dataclass
class _Pending:
    tool: str
    base: dict[str, Any]
    list_keys: tuple[str, ...]
    items: list[Item]
    pos: int
    max_items: int
    warnings: list[str]


class ContinuationStore:
    """continuation トークン → 残りの結果。会話が続く間だけ有効なメモリ上のキャッシュ。"""

    def __init__(self, capacity: int = 64) -> None:
        self._data: OrderedDict[str, _Pending] = OrderedDict()
        self._capacity = capacity
        self._lock = threading.Lock()

    def put(self, pending: _Pending) -> str:
        token = "c-" + secrets.token_urlsafe(18)
        with self._lock:
            self._data[token] = pending
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)
        return token

    def take(self, tool: str, token: str) -> _Pending:
        with self._lock:
            pending = self._data.pop(token, None)
        if pending is None:
            raise ToolFailure(
                "INVALID_ARGS",
                "continuation の有効期限が切れたか、不正です。最初から読み直してください",
            )
        if pending.tool != tool:
            raise ToolFailure("INVALID_ARGS", f"この continuation は {pending.tool} のものです")
        return pending

    def __len__(self) -> int:
        return len(self._data)


def paginate(
    store: ContinuationStore,
    tool: str,
    base: dict[str, Any],
    list_keys: tuple[str, ...],
    items: list[Item],
    *,
    max_items: int,
    warnings: list[str] | None = None,
) -> Outcome:
    """items を予算内に収まる分だけ返し、残りは continuation にする。"""
    pending = _Pending(tool, base, list_keys, items, 0, max(1, max_items), list(warnings or []))
    return next_page(store, pending)


def resume(store: ContinuationStore, tool: str, token: str) -> Outcome:
    return next_page(store, store.take(tool, token))


def next_page(store: ContinuationStore, p: _Pending) -> Outcome:
    budget_chars = int(TOKEN_BUDGET * CHARS_PER_TOKEN)
    used = len(dumps(p.base)) + 200 + sum(len(w) for w in p.warnings)
    taken: list[Item] = []
    i = p.pos
    while i < len(p.items) and len(taken) < p.max_items:
        size = p.items[i].size_chars()
        if taken and used + size > budget_chars:
            break
        if not taken and size > budget_chars * MAX_SINGLE_ITEM_FACTOR:
            raise ToolFailure(
                "RESULT_TOO_LARGE",
                "結果が大きすぎます。範囲を狭めてください",
                detail={"item_index": i},
            )
        taken.append(p.items[i])
        used += size
        i += 1
    data = dict(p.base)
    for key in p.list_keys:
        data[key] = []
    untrusted: list[dict[str, Any]] = []
    for it in taken:
        for key, value in it.fields.items():
            data.setdefault(key, []).append(value)
        untrusted.extend(it.untrusted)
    out = Outcome(data=data, untrusted=untrusted, warnings=list(p.warnings))
    if i < len(p.items):
        p.pos = i
        p.warnings = []
        out.continuation = store.put(p)
        out.data["remaining"] = len(p.items) - i
    return out
