"""設定（YAML）。秘密情報（共有シークレット・Qdrant の API キー）は環境変数で渡し、ファイルに書かない。

例（`rag.yaml`）::

    org_id: default
    sources: ['\\\\nas\\share']          # 取り込むフォルダ（複数可）
    index_dir: D:/llm-rag/index         # BM25 索引と取り込み状態の置き場
    acl_map: acl.yaml                   # パスの接頭辞 → acl_groups の対応表（相対パスは本ファイル基準）
    qdrant: { url: http://qdrant:6333, api_key_env: QDRANT_API_KEY }
    embedder:
      kind: sentence-transformers
      model_path: D:/models/ruri-v3-310m
      query_prefix: "検索クエリ: "
      passage_prefix: "検索文書: "
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_EXTENSIONS = [".docx", ".xlsx", ".xlsm", ".pdf", ".txt", ".md", ".csv"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AclRule(_Strict):
    prefix: str
    acl_groups: list[str] = Field(min_length=1)
    doc_type: str | None = None
    """この接頭辞の文書の種類（例 contract）。省略時は拡張子（pdf など）。"""


class AclMap(_Strict):
    rules: list[AclRule] = Field(default_factory=list)


class QdrantSettings(_Strict):
    url: str | None = None
    """本番: Qdrant サーバーの URL。"""
    path: str | None = None
    """ローカルモード（ファイル。1プロセスからのみ開ける）。"""
    location: str | None = None
    """":memory:"（試験用）。"""
    api_key_env: str | None = None
    timeout_s: int = 30

    @model_validator(mode="after")
    def _one_target(self) -> QdrantSettings:
        n = sum(x is not None for x in (self.url, self.path, self.location))
        if n > 1:
            raise ValueError("qdrant には url / path / location のどれか1つを指定してください")
        if n == 0:
            self.location = ":memory:"
        return self


class EmbedderSettings(_Strict):
    kind: Literal["hash", "sentence-transformers"] = "hash"
    model_path: str | None = None
    """sentence-transformers のモデルの置き場（事前に配置する。ダウンロードはしない）。"""
    model_id: str | None = None
    """索引に記録する名前（省略時は model_path のフォルダ名）。変わったら全件を取り込み直す。"""
    dim: int = 256
    """hash のときの次元数（sentence-transformers はモデルから取る）。"""
    query_prefix: str = ""
    """例: multilingual-e5 は "query: "、Ruri v3 は "検索クエリ: "。"""
    passage_prefix: str = ""
    """例: multilingual-e5 は "passage: "、Ruri v3 は "検索文書: "。"""
    device: str | None = None
    batch_size: int = 32
    normalize: bool = True
    max_seq_length: int | None = None


class ChunkSettings(_Strict):
    max_tokens: int = Field(default=800, ge=50)
    overlap_tokens: int = Field(default=100, ge=0)
    chars_per_token: float = Field(default=1.5, gt=0)
    """トークン数の概算（protocol §2.5 と同じ「文字数÷1.5」）。"""
    table_header_rows: int = Field(default=1, ge=0)
    """表を分割するとき、各チャンクの先頭に繰り返す見出し行の数。"""

    @property
    def max_chars(self) -> int:
        return int(self.max_tokens * self.chars_per_token)

    @property
    def overlap_chars(self) -> int:
        return int(self.overlap_tokens * self.chars_per_token)

    @model_validator(mode="after")
    def _overlap_lt_max(self) -> ChunkSettings:
        if self.overlap_tokens * 2 >= self.max_tokens:
            raise ValueError("overlap_tokens は max_tokens の半分未満にしてください")
        return self


class SearchSettings(_Strict):
    candidates: int = Field(default=50, ge=1, le=1000)
    """ベクトル・BM25 それぞれから RRF に渡す件数。"""
    rrf_k: int = Field(default=60, ge=1)
    excerpt_chars: int = Field(default=300, ge=20, le=4000)
    max_top_k: int = Field(default=10, ge=1, le=50)


class ServerSettings(_Strict):
    host: str = "127.0.0.1"
    port: int = 8094
    secret_env: str = "LLM_RAG_SECRET"  # noqa: S105 -- 環境変数の名前であって値ではない
    """Gateway と共有する内部トークンの環境変数名（ヘッダー X-Internal-Token で受け取る）。"""


class RagSettings(_Strict):
    org_id: str = "default"
    sources: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=lambda: list(DEFAULT_EXTENSIONS))
    index_dir: str = "./rag-index"
    acl_map: str | AclMap | None = None
    """対応表の YAML ファイルのパス、または直接書いた rules。"""
    tokenizer: Literal["auto", "sudachi", "bigram"] = "auto"
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    embedder: EmbedderSettings = Field(default_factory=EmbedderSettings)
    chunking: ChunkSettings = Field(default_factory=ChunkSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    max_file_mb: int = Field(default=200, ge=1)
    base_dir: str | None = Field(default=None, exclude=True)
    """相対パスの基準（設定ファイルのフォルダ）。"""

    @property
    def collection(self) -> str:
        return f"docs_{self.org_id}"

    def resolve(self, p: str) -> Path:
        path = Path(p)
        if not path.is_absolute() and self.base_dir:
            path = Path(self.base_dir) / path
        return path

    def load_acl_map(self) -> AclMap:
        if self.acl_map is None:
            return AclMap()
        if isinstance(self.acl_map, AclMap):
            return self.acl_map
        raw = yaml.safe_load(self.resolve(self.acl_map).read_text(encoding="utf-8")) or {}
        return AclMap.model_validate(raw)


def load_settings(path: str | Path) -> RagSettings:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    settings = RagSettings.model_validate(raw)
    settings.base_dir = str(p.resolve().parent)
    return settings
