"""ハイブリッド検索（ベクトル＋BM25 を RRF で統合）。設計書 §6.4、API §6、protocol §3.8。

ACL と絞り込みは両方の索引に検索条件として渡す。結果を取ってから除外することはしない
（順位・件数・点数から権限の無い文書の存在が推測できないようにするため）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from llm_rag.analyzer import normalize
from llm_rag.index import RagIndex
from llm_rag.model import SearchFilter

log = logging.getLogger("llm_rag")


@dataclass(frozen=True)
class Hit:
    ref: str
    path: str
    location: str
    score: float
    excerpt: str


def rrf(rankings: list[list[str]], k: int) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion。同点は ID 順（決定的）。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def make_excerpt(text: str, query_tokens: list[str], limit: int) -> str:
    """検索語が最初に現れる付近を limit 文字まで切り出す。"""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    low = normalize(flat)
    pos = -1
    if len(low) == len(flat):  # 正規化で長さが変わらないときだけ位置を合わせられる
        for t in sorted(set(query_tokens), key=len, reverse=True):
            p = low.find(t)
            if p != -1 and (pos == -1 or p < pos):
                pos = p
    start = 0 if pos < limit // 3 else pos - limit // 3
    start = max(0, min(start, len(flat) - (limit - 1)))
    lead = "…" if start else ""
    room = limit - len(lead)
    if start + room >= len(flat):
        return lead + flat[start:]
    return lead + flat[start : start + room - 1] + "…"


class Searcher:
    def __init__(self, index: RagIndex) -> None:
        self.index = index
        self.settings = index.settings.search

    def search(
        self,
        query: str,
        acl_groups: list[str],
        *,
        top_k: int = 5,
        path_prefix: str | None = None,
        doc_type: str | None = None,
        updated_after: float | None = None,
    ) -> list[Hit]:
        t0 = time.monotonic()
        flt = SearchFilter(acl_groups, path_prefix, doc_type, updated_after)
        top_k = max(1, min(top_k, self.settings.max_top_k))
        if not flt.acl_groups or not query.strip():
            log.info("rag.search hits=0 took_ms=%d", int((time.monotonic() - t0) * 1000))
            return []
        self.index.reload_if_changed()
        n = max(self.settings.candidates, top_k)
        tokens = self.index.tokenizer.tokenize(query)
        vec_hits = self.index.vector.search(self.index.embedder.embed_query(query), flt, n)
        bm_hits = self.index.bm25.search(tokens, flt, n)
        fused = rrf([[c for c, _ in vec_hits], [c for c, _ in bm_hits]], self.settings.rrf_k)
        best = 2.0 / (self.settings.rrf_k + 1)
        chosen = fused[:top_k]
        payloads = self.index.vector.payloads([c for c, _ in chosen])
        hits: list[Hit] = []
        allowed = set(flt.acl_groups)
        for cid, score in chosen:
            p = payloads.get(cid)
            # 索引の不整合（BM25 と Qdrant の世代ずれ）への安全網。通常は起きない
            if p is None or not allowed.intersection(p.get("acl_groups", [])):
                log.warning("rag.search index inconsistency dropped=1")
                continue
            hits.append(
                Hit(
                    ref=f"r{len(hits) + 1}",
                    path=str(p.get("path", "")),
                    location=str(p.get("location", "")),
                    score=round(score / best, 4),
                    excerpt=make_excerpt(str(p.get("text", "")), tokens, self.settings.excerpt_chars),
                )
            )
        log.info(
            "rag.search hits=%d vec=%d bm25=%d took_ms=%d",
            len(hits),
            len(vec_hits),
            len(bm_hits),
            int((time.monotonic() - t0) * 1000),
        )
        return hits
