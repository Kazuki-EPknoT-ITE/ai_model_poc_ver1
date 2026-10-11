"""ベクトル索引（Qdrant）＋ BM25 ＋ 取り込み状態をまとめたもの。取り込みと検索の両方が使う。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

from llm_rag.analyzer import Tokenizer, make_tokenizer
from llm_rag.bm25 import Bm25Index
from llm_rag.config import RagSettings
from llm_rag.embed import Embedder, make_embedder
from llm_rag.model import ChunkRecord, path_prefixes
from llm_rag.vector import VectorIndex, make_client

log = logging.getLogger("llm_rag")
STATE_VERSION = 1


class RagIndex:
    def __init__(
        self,
        settings: RagSettings,
        *,
        client: QdrantClient | None = None,
        embedder: Embedder | None = None,
        tokenizer: Tokenizer | None = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder or make_embedder(settings.embedder)
        self.tokenizer = tokenizer or make_tokenizer(settings.tokenizer)
        q = settings.qdrant
        if q.path:
            q = q.model_copy(update={"path": str(settings.resolve(q.path))})
        self.vector = VectorIndex(
            client or make_client(q),
            settings.collection,
            server_mode=bool(settings.qdrant.url) and client is None,
        )
        self.dir = settings.resolve(settings.index_dir)
        self.bm25_path = self.dir / f"bm25_{settings.org_id}.json"
        self.state_path = self.dir / f"state_{settings.org_id}.json"
        self._lock = threading.RLock()
        self._bm25_mtime: float | None = None
        self.docs: dict[str, dict[str, Any]] = {}
        self.bm25 = Bm25Index(self.tokenizer.name)
        self._load()

    # ------------------------------------------------------------ 読み込み

    def _fingerprint(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "embedder": self.embedder.model_id,
            "dim": self.embedder.dim,
            "tokenizer": self.tokenizer.name,
            "collection": self.settings.collection,
        }

    def _load(self) -> None:
        state: dict[str, Any] = {}
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        fp = self._fingerprint()
        same = all(state.get(k) == v for k, v in fp.items())
        if state and same and self.vector.dim() == self.embedder.dim:
            self.docs = dict(state.get("docs", {}))
            self.bm25 = Bm25Index.load(self.bm25_path, self.tokenizer.name)
            self._bm25_mtime = self._mtime()
        else:
            self.needs_rebuild = bool(state) or self.vector.exists()
            self.docs = {}
            self.bm25 = Bm25Index(self.tokenizer.name)

    needs_rebuild: bool = False
    """埋め込みモデル・トークナイザ・次元数が前回と違う（全件を取り込み直す必要がある）。"""

    def _mtime(self) -> float | None:
        try:
            return self.bm25_path.stat().st_mtime
        except OSError:
            return None

    def reload_if_changed(self) -> bool:
        """取り込みバッチ（別プロセス）が BM25 索引を書き換えたら読み直す。"""
        m = self._mtime()
        if m is None or m == self._bm25_mtime:
            return False
        with self._lock:
            if m == self._bm25_mtime:
                return False
            self.bm25 = Bm25Index.load(self.bm25_path, self.tokenizer.name)
            self._bm25_mtime = m
        log.info("bm25 reloaded chunks=%d", len(self.bm25))
        return True

    # ------------------------------------------------------------ 更新（取り込みバッチから）

    def reset(self) -> None:
        self.vector.recreate(self.embedder.dim)
        self.bm25 = Bm25Index(self.tokenizer.name)
        self.docs = {}
        self.needs_rebuild = False

    def ensure_ready(self) -> None:
        self.vector.ensure(self.embedder.dim)

    def put_doc(self, doc_id: str, records: list[ChunkRecord], meta: dict[str, Any]) -> None:
        vectors: list[list[float]] = []
        texts = [r.text for r in records]
        bs = max(1, self.settings.embedder.batch_size)
        for i in range(0, len(texts), bs):
            vectors.extend(self.embedder.embed_documents(texts[i : i + bs]))
        self.vector.delete_doc(doc_id)
        self.vector.upsert(records, vectors)
        self.bm25.remove_doc(doc_id)
        for r in records:
            self.bm25.add(
                r.chunk_id,
                doc_id=doc_id,
                tokens=self.tokenizer.tokenize(r.text),
                acl_groups=r.acl_groups,
                prefixes=path_prefixes(r.path),
                doc_type=r.doc_type,
                updated_ts=r.updated_ts,
            )
        self.docs[doc_id] = meta

    def delete_doc(self, doc_id: str) -> None:
        self.vector.delete_doc(doc_id)
        self.bm25.remove_doc(doc_id)
        self.docs.pop(doc_id, None)

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.bm25.save(self.bm25_path)
        self._bm25_mtime = self._mtime()
        data = {**self._fingerprint(), "docs": self.docs}
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=0)
            os.replace(tmp, self.state_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
