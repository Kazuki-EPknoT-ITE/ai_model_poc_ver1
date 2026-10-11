"""BM25 の索引（ローカルファイル）。

ACL は検索条件として扱う: 利用者のグループで見えるチャンクだけを母集団にし、
文書数・平均長・文書頻度（IDF）もその母集団だけで計算する。権限の無い文書は順位にも点数にも影響しない。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_rag.model import SearchFilter

FORMAT_VERSION = 1


@dataclass
class _Doc:
    doc_id: str
    tf: dict[str, int]
    length: int
    acl: list[str]
    prefixes: list[str]
    doc_type: str
    updated_ts: float


class Bm25Index:
    def __init__(self, tokenizer: str, k1: float = 1.2, b: float = 0.75) -> None:
        self.tokenizer = tokenizer
        self.k1 = k1
        self.b = b
        self._docs: dict[str, _Doc] = {}
        self._postings: dict[str, dict[str, int]] = defaultdict(dict)
        self._by_group: dict[str, set[str]] = defaultdict(set)
        self._by_doc: dict[str, set[str]] = defaultdict(set)

    def __len__(self) -> int:
        return len(self._docs)

    # ------------------------------------------------------------ 更新

    def add(
        self,
        chunk_id: str,
        *,
        doc_id: str,
        tokens: list[str],
        acl_groups: list[str],
        prefixes: list[str],
        doc_type: str,
        updated_ts: float,
    ) -> None:
        if chunk_id in self._docs:
            self._remove_chunk(chunk_id)
        tf = dict(Counter(tokens))
        d = _Doc(doc_id, tf, len(tokens), sorted(set(acl_groups)), prefixes, doc_type, updated_ts)
        self._docs[chunk_id] = d
        for term, n in tf.items():
            self._postings[term][chunk_id] = n
        for g in d.acl:
            self._by_group[g].add(chunk_id)
        self._by_doc[doc_id].add(chunk_id)

    def _remove_chunk(self, chunk_id: str) -> None:
        d = self._docs.pop(chunk_id)
        for term in d.tf:
            post = self._postings.get(term)
            if post is not None:
                post.pop(chunk_id, None)
                if not post:
                    del self._postings[term]
        for g in d.acl:
            members = self._by_group.get(g)
            if members is not None:
                members.discard(chunk_id)
                if not members:
                    del self._by_group[g]
        chunks = self._by_doc.get(d.doc_id)
        if chunks is not None:
            chunks.discard(chunk_id)
            if not chunks:
                del self._by_doc[d.doc_id]

    def remove_doc(self, doc_id: str) -> int:
        ids = list(self._by_doc.get(doc_id, ()))
        for cid in ids:
            self._remove_chunk(cid)
        return len(ids)

    # ------------------------------------------------------------ 検索

    def _visible(self, flt: SearchFilter) -> set[str]:
        ids: set[str] = set()
        for g in flt.acl_groups:
            ids |= self._by_group.get(g, set())
        if flt.norm_prefix is None and flt.doc_type is None and flt.updated_after is None:
            return ids
        out: set[str] = set()
        for cid in ids:
            d = self._docs[cid]
            if flt.norm_prefix is not None and flt.norm_prefix not in d.prefixes:
                continue
            if flt.doc_type is not None and d.doc_type != flt.doc_type:
                continue
            if flt.updated_after is not None and not d.updated_ts > flt.updated_after:
                continue
            out.add(cid)
        return out

    def search(self, tokens: list[str], flt: SearchFilter, limit: int) -> list[tuple[str, float]]:
        if not flt.acl_groups or not tokens:
            return []
        visible = self._visible(flt)
        n = len(visible)
        if n == 0:
            return []
        avgdl = sum(self._docs[c].length for c in visible) / n or 1.0
        scores: dict[str, float] = defaultdict(float)
        for term, qtf in Counter(tokens).items():
            post = self._postings.get(term)
            if not post:
                continue
            hits = [(c, tf) for c, tf in post.items() if c in visible]
            if not hits:
                continue
            df = len(hits)
            idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
            for c, tf in hits:
                dl = self._docs[c].length
                denom = tf + self.k1 * (1 - self.b + self.b * dl / avgdl)
                scores[c] += qtf * idf * tf * (self.k1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:limit]

    # ------------------------------------------------------------ 保存

    def to_json(self) -> dict[str, Any]:
        return {
            "version": FORMAT_VERSION,
            "tokenizer": self.tokenizer,
            "k1": self.k1,
            "b": self.b,
            "chunks": {
                cid: {
                    "d": d.doc_id,
                    "tf": d.tf,
                    "n": d.length,
                    "acl": d.acl,
                    "pp": d.prefixes,
                    "t": d.doc_type,
                    "u": d.updated_ts,
                }
                for cid, d in self._docs.items()
            },
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".bm25-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_json(), f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path: Path, tokenizer: str) -> Bm25Index:
        if not path.exists():
            return cls(tokenizer)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != FORMAT_VERSION:
            raise ValueError("BM25 索引の形式が違います。取り込みをやり直してください")
        idx = cls(str(raw["tokenizer"]), float(raw.get("k1", 1.2)), float(raw.get("b", 0.75)))
        if idx.tokenizer != tokenizer:
            raise ValueError(
                f"BM25 索引のトークナイザ（{idx.tokenizer}）と設定（{tokenizer}）が違います。取り込みをやり直してください"
            )
        for cid, c in raw["chunks"].items():
            d = _Doc(
                str(c["d"]),
                dict(c["tf"]),
                int(c["n"]),
                list(c["acl"]),
                list(c["pp"]),
                str(c["t"]),
                float(c["u"]),
            )
            idx._docs[cid] = d
            for term, n in d.tf.items():
                idx._postings[term][cid] = n
            for g in d.acl:
                idx._by_group[g].add(cid)
            idx._by_doc[d.doc_id].add(cid)
        return idx
