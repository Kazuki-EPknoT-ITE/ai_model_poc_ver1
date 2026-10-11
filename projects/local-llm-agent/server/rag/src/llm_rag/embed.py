"""埋め込み。本番は sentence-transformers 互換の日本語モデル（Ruri v3 / multilingual-e5）、試験は HashEmbedder。

モデルごとの前処理（e5 の "query: " / "passage: "、Ruri v3 の "検索クエリ: " / "検索文書: "）は
設定の query_prefix / passage_prefix で持つ。モデルは事前に配置したフォルダから読み、ダウンロードはしない。
"""

from __future__ import annotations

import hashlib
import importlib
import math
import os
from pathlib import Path
from typing import Any, Protocol

from llm_rag.analyzer import BigramTokenizer
from llm_rag.config import EmbedderSettings


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _l2(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else v


class HashEmbedder:
    """決定的な特徴ハッシュ（文字 bi-gram と英数字の語）。意味は捉えないが、試験と開発で使う。"""

    def __init__(self, dim: int = 256, query_prefix: str = "", passage_prefix: str = "") -> None:
        self._dim = dim
        self._tok = BigramTokenizer()
        self.query_prefix = query_prefix
        self.passage_prefix = passage_prefix

    @property
    def model_id(self) -> str:
        return f"hash-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self._dim
        for t in self._tok.tokenize(text):
            h = hashlib.blake2b(t.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self._dim
            v[idx] += 1.0 if h[4] & 1 else -1.0
        return _l2(v)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(self.passage_prefix + t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(self.query_prefix + text)


class SentenceTransformerEmbedder:
    """sentence-transformers で読めるモデル（任意依存 `llm-rag[embed]`）。"""

    def __init__(self, s: EmbedderSettings) -> None:
        if not s.model_path:
            raise ValueError("embedder.model_path を設定してください（事前に配置したモデルのフォルダ）")
        if not Path(s.model_path).exists():
            raise FileNotFoundError("embedder.model_path のフォルダがありません")
        # 外向き通信をしない（モデルのハブへ問い合わせない）
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        st: Any = importlib.import_module("sentence_transformers")
        self._model: Any = st.SentenceTransformer(s.model_path, device=s.device, local_files_only=True)
        if s.max_seq_length:
            self._model.max_seq_length = s.max_seq_length
        self._s = s
        self._dim = int(self._model.get_sentence_embedding_dimension())
        self._id = s.model_id or Path(s.model_path).name

    @property
    def model_id(self) -> str:
        return self._id

    @property
    def dim(self) -> int:
        return self._dim

    def _encode(self, texts: list[str]) -> list[list[float]]:
        arr: Any = self._model.encode(
            texts,
            batch_size=self._s.batch_size,
            normalize_embeddings=self._s.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [[float(x) for x in row] for row in arr]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode([self._s.passage_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([self._s.query_prefix + text])[0]


def make_embedder(s: EmbedderSettings) -> Embedder:
    if s.kind == "hash":
        return HashEmbedder(s.dim, s.query_prefix, s.passage_prefix)
    return SentenceTransformerEmbedder(s)
