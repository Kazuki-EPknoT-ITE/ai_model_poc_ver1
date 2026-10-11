"""監査ログ（NET-11）。記録するのは 検索語・URL・利用者・日時・結果 だけ。ページ本文は保存しない。

DLP で止めた依頼は、検索語・URL を記録しない（`target=None`, `detail.redacted=true`）。
機密語そのものが監査ログに残り、辞書の中身が漏れるのを防ぐため。
形式は data_model §3 の audit_events に合わせる（source='web_relay'）。
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class AuditEvent:
    at: str  # ISO 8601（UTC）
    user_id: str
    org_id: str
    action: str  # web.search | web.fetch
    target: str | None  # 検索語 / URL（DLP で止めたものは None）
    result: str  # ok | blocked:<code> | error:<code>
    source: str = "web_relay"
    detail: dict[str, Any] = field(default_factory=dict)


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None: ...


class MemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def write(self, event: AuditEvent) -> None:
        with self._lock:
            self.events.append(event)


class JsonlAuditSink:
    """JSON Lines に追記する。Gateway の DB へ取り込む場合はこのファイルを定期的に読み込む。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: AuditEvent) -> None:
        line = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
