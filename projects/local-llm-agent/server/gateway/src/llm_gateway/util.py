"""時計・ID・エラー形式などの共通部品。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

# ---- 時計（試験で差し替える） ----


class Clock:
    """現在時刻（UTC）。試験では FakeClock に差し替えて時間を進める。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 10, 11, 0, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


# ---- ULID（時刻順に並ぶ26桁の ID。data_model §1） ----

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ulid_lock = threading.Lock()
_ulid_last: tuple[int, int] = (0, 0)


def new_ulid() -> str:
    """単調増加する ULID（同じミリ秒内では乱数部を1ずつ増やす）。"""
    global _ulid_last
    with _ulid_lock:
        ms = int(time.time() * 1000)
        last_ms, last_rand = _ulid_last
        if ms <= last_ms:
            ms = last_ms
            rand = last_rand + 1
        else:
            rand = int.from_bytes(os.urandom(10), "big") >> 1  # 先頭1ビットを空けて桁あふれを防ぐ
        _ulid_last = (ms, rand)
    value = (ms << 80) | (rand & ((1 << 80) - 1))
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


# ---- エラー（local_llm_api.md §1.1） ----


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.retry_after_ms is not None:
            err["retry_after_ms"] = self.retry_after_ms
        return {"error": err}


def body_hash(body: Any) -> str:
    """依頼の中身の sha256（冪等キーの照合用。キー順に依存しない）。"""
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def estimate_tokens(messages: list[dict[str, Any]], chars_per_token: float) -> int:
    """トークン数の見積もり（トークナイザーを持たないため文字数から概算する）。"""
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return int(chars / chars_per_token)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


NET_LEVELS = {"off": 0, "search": 1, "browse": 2}
NET_NAMES = {v: k for k, v in NET_LEVELS.items()}


def net_min(*levels: str | None) -> str:
    """ネットの上限の最小（None は制約なしとして無視する）。不明な値は off とみなす。"""
    vals = [NET_LEVELS.get(lv, 0) for lv in levels if lv is not None]
    return NET_NAMES[min(vals)] if vals else "off"
