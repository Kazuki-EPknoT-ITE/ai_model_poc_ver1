"""Qdrant（ベクトル索引）。ACL・絞り込みは Qdrant のフィルタ（検索条件）として渡す。"""

from __future__ import annotations

import os
from typing import Any

from qdrant_client import QdrantClient, models

from llm_rag.config import QdrantSettings
from llm_rag.model import ChunkRecord, SearchFilter

_KEYWORD_FIELDS = ("doc_id", "acl_groups", "doc_type", "path_prefixes")


def make_client(s: QdrantSettings) -> QdrantClient:
    if s.url:
        key = os.environ.get(s.api_key_env) if s.api_key_env else None
        return QdrantClient(url=s.url, api_key=key, timeout=s.timeout_s)
    if s.path:
        return QdrantClient(path=s.path)
    return QdrantClient(location=s.location or ":memory:")


def build_filter(flt: SearchFilter) -> models.Filter:
    must: list[models.Condition] = [
        models.FieldCondition(key="acl_groups", match=models.MatchAny(any=list(flt.acl_groups)))
    ]
    if flt.norm_prefix is not None:
        must.append(
            models.FieldCondition(key="path_prefixes", match=models.MatchValue(value=flt.norm_prefix))
        )
    if flt.doc_type is not None:
        must.append(models.FieldCondition(key="doc_type", match=models.MatchValue(value=flt.doc_type)))
    if flt.updated_after is not None:
        must.append(models.FieldCondition(key="updated_ts", range=models.Range(gt=flt.updated_after)))
    return models.Filter(must=must)


class VectorIndex:
    def __init__(self, client: QdrantClient, collection: str, *, server_mode: bool = False) -> None:
        self.client = client
        self.collection = collection
        self.server_mode = server_mode

    def exists(self) -> bool:
        return bool(self.client.collection_exists(self.collection))

    def dim(self) -> int | None:
        if not self.exists():
            return None
        info = self.client.get_collection(self.collection)
        vectors: Any = info.config.params.vectors
        return int(vectors.size) if vectors is not None and hasattr(vectors, "size") else None

    def recreate(self, dim: int) -> None:
        if self.exists():
            self.client.delete_collection(self.collection)
        self.ensure(dim)

    def ensure(self, dim: int) -> None:
        current = self.dim()
        if current is not None:
            if current != dim:
                raise ValueError(f"コレクションの次元数（{current}）と埋め込みモデル（{dim}）が違います")
            return
        self.client.create_collection(
            self.collection, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE)
        )
        if self.server_mode:  # ローカルモードではペイロード索引は効かない（警告が出る）ので作らない
            for f in _KEYWORD_FIELDS:
                self.client.create_payload_index(self.collection, f, models.PayloadSchemaType.KEYWORD)
            self.client.create_payload_index(self.collection, "updated_ts", models.PayloadSchemaType.FLOAT)

    def upsert(self, records: list[ChunkRecord], vectors: list[list[float]]) -> None:
        if not records:
            return
        points = [
            models.PointStruct(id=r.chunk_id, vector=v, payload=r.payload())
            for r, v in zip(records, vectors, strict=True)
        ]
        for i in range(0, len(points), 256):
            self.client.upsert(self.collection, points=points[i : i + 256], wait=True)

    def delete_doc(self, doc_id: str) -> None:
        if not self.exists():
            return
        self.client.delete(
            self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                )
            ),
            wait=True,
        )

    def search(self, vector: list[float], flt: SearchFilter, limit: int) -> list[tuple[str, float]]:
        if not flt.acl_groups or not self.exists():
            return []
        res = self.client.query_points(
            self.collection, query=vector, query_filter=build_filter(flt), limit=limit, with_payload=False
        )
        return [(str(p.id), float(p.score)) for p in res.points]

    def payloads(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        pts = self.client.retrieve(self.collection, ids=ids, with_payload=True, with_vectors=False)
        return {str(p.id): dict(p.payload or {}) for p in pts}

    def count(self) -> int:
        if not self.exists():
            return 0
        return int(self.client.count(self.collection, exact=True).count)
