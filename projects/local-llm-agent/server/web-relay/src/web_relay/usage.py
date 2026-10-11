"""利用回数の集計（NET-13）。利用者・月ごとの検索回数を数え、上限で止める。

`try_consume` は「上限の確認と加算」を不可分に行う（同時に来た依頼で上限を超えないように）。
本番で Gateway の `usage_daily.web_searches` に集約する場合は、この Protocol を実装した別の Store を使う。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Protocol


class UsageStore(Protocol):
    def try_consume(self, user_id: str, month: str, kind: str, limit: int | None) -> int | None:
        """1回分を加算して残り回数を返す（limit が None なら -1）。上限に達していれば加算せず None。"""
        ...

    def count(self, user_id: str, month: str, kind: str) -> int: ...


class MemoryUsageStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str, str], int] = {}

    def try_consume(self, user_id: str, month: str, kind: str, limit: int | None) -> int | None:
        key = (user_id, month, kind)
        with self._lock:
            n = self._counts.get(key, 0)
            if limit is not None and n >= limit:
                return None
            self._counts[key] = n + 1
            return -1 if limit is None else limit - (n + 1)

    def count(self, user_id: str, month: str, kind: str) -> int:
        with self._lock:
            return self._counts.get((user_id, month, kind), 0)


class SqliteUsageStore:
    """再起動しても回数が消えないように SQLite に保存する（Relay 単体での運用向け）。"""

    def __init__(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(p), check_same_thread=False, isolation_level=None)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS web_usage_monthly ("
            " user_id TEXT NOT NULL, month TEXT NOT NULL, kind TEXT NOT NULL, n INTEGER NOT NULL,"
            " PRIMARY KEY (user_id, month, kind))"
        )

    def try_consume(self, user_id: str, month: str, kind: str, limit: int | None) -> int | None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT n FROM web_usage_monthly WHERE user_id=? AND month=? AND kind=?",
                    (user_id, month, kind),
                ).fetchone()
                n = int(row[0]) if row else 0
                if limit is not None and n >= limit:
                    self._db.execute("ROLLBACK")
                    return None
                self._db.execute(
                    "INSERT INTO web_usage_monthly(user_id, month, kind, n) VALUES (?,?,?,1)"
                    " ON CONFLICT(user_id, month, kind) DO UPDATE SET n = n + 1",
                    (user_id, month, kind),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            return -1 if limit is None else limit - (n + 1)

    def count(self, user_id: str, month: str, kind: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT n FROM web_usage_monthly WHERE user_id=? AND month=? AND kind=?",
                (user_id, month, kind),
            ).fetchone()
            return int(row[0]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._db.close()
