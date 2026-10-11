"""CLI（YAML 設定・ACL 対応表ファイル・Qdrant のローカルモード）と、索引の読み直し・設定の検証。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from rag_testkit import IndexFactory, make_settings, write_text

from llm_rag.analyzer import BigramTokenizer, make_tokenizer, sudachi_available
from llm_rag.cli import main
from llm_rag.config import ChunkSettings, QdrantSettings, load_settings
from llm_rag.embed import HashEmbedder
from llm_rag.ingest import run_ingest
from llm_rag.search import Searcher


def _config(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    write_text(docs / "公開" / "経費.md", "# 経費\n\n交通費の精算は月末締め。\n")
    write_text(docs / "役員" / "報酬.md", "# 報酬\n\n役員報酬の改定案。\n")
    (tmp_path / "acl.yaml").write_text(
        "rules:\n"
        f"  - {{prefix: '{docs / '公開'}', acl_groups: [all]}}\n"
        f"  - {{prefix: '{docs / '役員'}', acl_groups: [executive], doc_type: board}}\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "rag.yaml"
    cfg.write_text(
        "org_id: cli\n"
        f"sources: ['{docs}']\n"
        "index_dir: index\n"
        "acl_map: acl.yaml\n"
        "tokenizer: bigram\n"
        "qdrant: {path: qdrant}\n"
        "embedder: {kind: hash, dim: 64, query_prefix: 'query: ', passage_prefix: 'passage: '}\n",
        encoding="utf-8",
    )
    return cfg


def test_cli_ingest_search_eval(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _config(tmp_path)
    with pytest.raises(SystemExit) as e:
        main(["ingest", "--config", str(cfg)])
    assert e.value.code == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["added"] == 2 and stats["failed"] == 0
    assert (tmp_path / "index" / "bm25_cli.json").exists() and (tmp_path / "qdrant").is_dir()

    with pytest.raises(SystemExit):
        main(["search", "--config", str(cfg), "--query", "交通費", "--groups", "all"])
    out = json.loads(capsys.readouterr().out)
    assert out["hits"][0]["path"].endswith("経費.md")

    items = tmp_path / "items.jsonl"
    items.write_text(
        json.dumps(
            {
                "id": "1",
                "question": "交通費の締め",
                "acl_user": "u1",
                "evidence": [{"path": str(tmp_path / "docs" / "公開" / "経費.md")}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "users.yaml").write_text("u1: [all]\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e2:
        main(
            [
                "eval",
                "--config",
                str(cfg),
                "--items",
                str(items),
                "--users",
                str(tmp_path / "users.yaml"),
                "--out",
                str(tmp_path / "result.json"),
            ]
        )
    assert e2.value.code == 0
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))["value"] == 1.0


def test_serve_requires_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_RAG_SECRET", raising=False)
    with pytest.raises(SystemExit) as e:
        main(["serve", "--config", str(_config(tmp_path))])
    assert e.value.code == 2


def test_settings_validation(tmp_path: Path) -> None:
    s = load_settings(_config(tmp_path))
    assert s.collection == "docs_cli"
    assert len(s.load_acl_map().rules) == 2
    assert s.resolve("index") == tmp_path / "index"
    with pytest.raises(ValueError):
        QdrantSettings(url="http://x", path="y")
    with pytest.raises(ValueError):
        ChunkSettings(max_tokens=100, overlap_tokens=60)
    assert QdrantSettings().location == ":memory:"
    c = ChunkSettings()
    assert (c.max_chars, c.overlap_chars) == (1200, 150)


def test_searcher_reloads_bm25_after_nightly_ingest(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = tmp_path / "docs"
    write_text(docs / "a.txt", "初版の文書。\n")
    s = make_settings(tmp_path, [(str(docs), ["all"])])
    client = QdrantClient(location=":memory:")
    serving = make_index(s, client=client)
    run_ingest(make_index(s, client=client))  # 別の取り込みプロセス（索引ファイルを書き換える）
    write_text(docs / "b.txt", "追加された議事録。\n")
    run_ingest(make_index(s, client=client))
    sr = Searcher(serving)
    assert [Path(h.path).name for h in sr.search("議事録", ["all"])][0] == "b.txt"


def test_tokenizers_and_embedder_prefixes() -> None:
    t = BigramTokenizer()
    assert t.tokenize("支払期日 KX-42") == ["支払", "払期", "期日", "kx-42"]
    assert t.tokenize("ＡＢＣ　株式") == ["abc", "株式"]  # NFKC で全角英数を正規化
    assert make_tokenizer("bigram").name == "bigram"
    assert make_tokenizer("auto").name == ("sudachi" if sudachi_available() else "bigram")
    e = HashEmbedder(32, "query: ", "passage: ")
    assert e.embed_query("支払") != e.embed_documents(["支払"])[0]  # 前処理（接頭辞）が効く
    assert HashEmbedder(32).embed_query("支払") == HashEmbedder(32).embed_documents(["支払"])[0]
    v = e.embed_query("支払期日")
    assert len(v) == 32 and abs(sum(x * x for x in v) - 1.0) < 1e-9
