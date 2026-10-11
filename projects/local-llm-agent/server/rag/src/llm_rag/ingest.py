"""取り込みバッチ（夜間。OPS-01）。フォルダを走査し、ファイルのハッシュで差分だけを取り込む。

- 変わっていない（ハッシュ・ACL・種類が同じ）文書は飛ばす。ACL だけ変わった文書も取り込み直す。
- 消えた文書・ACL の規則から外れた文書は索引から消す。
- 読めない文書は前回の索引を残し、失敗として数える。
- ログには件数と時間だけを出す（パス・本文は出さない）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from llm_rag.acl import AclResolver
from llm_rag.chunking import chunk_units
from llm_rag.extract import ExtractError, extract_units
from llm_rag.index import RagIndex
from llm_rag.model import ChunkRecord, chunk_id_for, doc_id_for

log = logging.getLogger("llm_rag")
SAVE_EVERY = 100


@dataclass
class IngestStats:
    scanned: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    failed: int = 0
    skipped_no_acl: int = 0
    skipped_too_large: int = 0
    chunks_written: int = 0
    rebuilt: bool = False
    seconds: float = 0.0
    failures: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def walk_files(root: Path, extensions: set[str], errors: list[OSError] | None = None) -> Iterator[Path]:
    """フォルダを走査する。読めないフォルダがあれば errors に入れる（消えた文書の判定を止めるため）。"""

    def onerror(e: OSError) -> None:
        if errors is not None:
            errors.append(e)

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith((".", "~$")))
        for name in sorted(filenames):
            if name.startswith(("~$", ".")):
                continue  # Office の一時ファイル・隠しファイル
            if Path(name).suffix.lower() in extensions:
                yield Path(dirpath) / name


def run_ingest(index: RagIndex) -> IngestStats:
    s = index.settings
    t0 = time.monotonic()
    stats = IngestStats()
    failures: Counter[str] = Counter()
    if index.needs_rebuild:
        index.reset()
        stats.rebuilt = True
    index.ensure_ready()
    resolver = AclResolver(s.load_acl_map())
    exts = {e.lower() for e in s.extensions}
    max_bytes = s.max_file_mb * 1024 * 1024
    seen: set[str] = set()
    changes = 0
    walk_errors: list[OSError] = []

    for root in s.sources:
        root_path = s.resolve(root)
        if not root_path.is_dir():
            failures["SOURCE_NOT_FOUND"] += 1
            continue
        for real in walk_files(root_path, exts, walk_errors):
            stats.scanned += 1
            path = str(real)
            did = doc_id_for(path)
            decision = resolver.resolve(path)
            if decision is None:
                stats.skipped_no_acl += 1
                continue
            seen.add(did)  # ここから先で失敗しても前回の索引は残す
            try:
                st = real.stat()
                if st.st_size > max_bytes:
                    stats.skipped_too_large += 1
                    continue
                file_hash = _sha256(real)
            except OSError:
                failures["FILE_LOCKED"] += 1
                continue
            doc_type = decision.doc_type or real.suffix.lower().lstrip(".")
            prev = index.docs.get(did)
            if (
                prev is not None
                and prev.get("file_hash") == file_hash
                and prev.get("acl_groups") == decision.acl_groups
                and prev.get("doc_type") == doc_type
            ):
                stats.unchanged += 1
                continue
            try:
                units = extract_units(real, header_rows=s.chunking.table_header_rows)
            except ExtractError as e:
                failures[e.code] += 1
                continue
            chunks = chunk_units(units, s.chunking)
            records = [
                ChunkRecord(
                    chunk_id=chunk_id_for(did, c.index),
                    doc_id=did,
                    path=path,
                    location=c.location,
                    chunk_index=c.index,
                    text=c.text,
                    doc_type=doc_type,
                    acl_groups=decision.acl_groups,
                    updated_ts=st.st_mtime,
                    file_hash=file_hash,
                )
                for c in chunks
            ]
            meta = {
                "path": path,
                "file_hash": file_hash,
                "acl_groups": decision.acl_groups,
                "doc_type": doc_type,
                "chunks": len(records),
                "updated_ts": st.st_mtime,
            }
            index.put_doc(did, records, meta)
            stats.chunks_written += len(records)
            if prev is None:
                stats.added += 1
            else:
                stats.updated += 1
            changes += 1
            if changes % SAVE_EVERY == 0:
                index.save()

    if walk_errors:
        failures["WALK_ERROR"] += len(walk_errors)
    if failures["SOURCE_NOT_FOUND"] or walk_errors:
        # NAS が見えない・一部のフォルダが読めないときに全件削除しないよう、削除の判定を飛ばす
        log.warning("ingest skipped deletions: unreadable sources")
    else:
        for did in [d for d in index.docs if d not in seen]:
            index.delete_doc(did)
            stats.deleted += 1
    index.save()

    stats.failures = dict(failures)
    stats.failed = sum(v for k, v in failures.items() if k not in ("SOURCE_NOT_FOUND", "WALK_ERROR"))
    stats.seconds = round(time.monotonic() - t0, 3)
    log.info(
        "ingest done scanned=%d added=%d updated=%d unchanged=%d deleted=%d failed=%d "
        "skipped_no_acl=%d chunks=%d seconds=%.1f",
        stats.scanned,
        stats.added,
        stats.updated,
        stats.unchanged,
        stats.deleted,
        stats.failed,
        stats.skipped_no_acl,
        stats.chunks_written,
        stats.seconds,
    )
    return stats
