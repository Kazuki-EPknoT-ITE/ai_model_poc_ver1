"""索引で共有する型（チャンクの記録・検索条件）とパスの正規化。"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_NS = uuid.UUID("5b6f4d0e-6d1b-4c55-9a3e-2f1f4e2a7c11")


def norm_path(p: str) -> str:
    """比較用のパス（区切りを / に、末尾の区切りを除き、大文字小文字を区別しない。Windows・NAS 向け）。"""
    return p.replace("\\", "/").rstrip("/").casefold()


def path_prefixes(path: str) -> list[str]:
    """パス自身とその親フォルダすべて（正規化済み）。path_prefix の絞り込みに使う。"""
    parts = norm_path(path).split("/")
    out: list[str] = []
    for i in range(1, len(parts) + 1):
        pre = "/".join(parts[:i])
        if pre.strip("/"):
            out.append(pre)
    return out


def under_prefix(path: str, prefix: str) -> bool:
    n, p = norm_path(path), norm_path(prefix)
    return n == p or n.startswith(p + "/")


def doc_id_for(path: str) -> str:
    return hashlib.sha256(norm_path(path).encode("utf-8")).hexdigest()


def chunk_id_for(doc_id: str, index: int) -> str:
    return str(uuid.uuid5(_NS, f"{doc_id}:{index}"))


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> float:
    """ISO 8601 の日付または日時 → UNIX 時刻（タイムゾーンが無ければ UTC）。"""
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


@dataclass
class ChunkRecord:
    """Qdrant のペイロード（data_model §4）＋絞り込み用の列。"""

    chunk_id: str
    doc_id: str
    path: str
    location: str
    chunk_index: int
    text: str
    doc_type: str
    acl_groups: list[str]
    updated_ts: float
    file_hash: str

    def payload(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "path": self.path,
            "location": self.location,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "doc_type": self.doc_type,
            "acl_groups": list(self.acl_groups),
            "updated_at": iso_utc(self.updated_ts),
            "updated_ts": self.updated_ts,
            "file_hash": self.file_hash,
            "path_prefixes": path_prefixes(self.path),
        }


@dataclass
class SearchFilter:
    """検索条件。acl_groups は必須（空なら何も返さない）。"""

    acl_groups: list[str]
    path_prefix: str | None = None
    doc_type: str | None = None
    updated_after: float | None = None
    _norm_prefix: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.acl_groups = sorted({g.strip() for g in self.acl_groups if g and g.strip()})
        if self.path_prefix is not None:
            self._norm_prefix = norm_path(self.path_prefix) or None

    @property
    def norm_prefix(self) -> str | None:
        return self._norm_prefix
