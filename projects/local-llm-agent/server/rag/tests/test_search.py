"""ハイブリッド検索の順位・filters・RRF・抜粋。"""

from __future__ import annotations

import os
from pathlib import Path

from rag_testkit import ConceptEmbedder, IndexFactory, make_settings, paths_of, write_text

from llm_rag.ingest import run_ingest
from llm_rag.model import SearchFilter, parse_time
from llm_rag.search import Searcher, make_excerpt, rrf

QUERY = "KX-42 の振込期限"


def _hybrid_corpus(docs: Path) -> None:
    write_text(docs / "keyword.txt", "KX-42 の仕様書。寸法と重量を記載する。\n")  # 語の一致だけ
    write_text(docs / "semantic.txt", "代金の支払期日は月末とする。\n")  # 意味だけ（共通の語が無い）
    write_text(docs / "both.txt", "KX-42 の代金の支払期日は翌月10日。\n")  # 両方
    noise = [
        "社員食堂の営業時間は11時から14時まで。",
        "駐車場の利用には申請が必要。",
        "年末年始の休業日のお知らせ。",
        "コピー機のトナー交換の手順。",
        "防災訓練は10月に実施する。",
        "名刺の発注は総務へ依頼する。",
        "社内報の原稿募集について。",
        "健康診断の受診日程。",
    ]
    for i, t in enumerate(noise):
        write_text(docs / f"noise{i}.txt", t + "\n")


def test_hybrid_uses_both_keyword_and_semantic(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = tmp_path / "docs"
    _hybrid_corpus(docs)
    idx = make_index(make_settings(tmp_path, [(str(docs), ["all"])]), embedder=ConceptEmbedder())
    run_ingest(idx)
    flt = SearchFilter(["all"])

    def name(cid: str) -> str:
        return Path(str(idx.vector.payloads([cid])[cid]["path"])).name

    # それぞれ単独では片方を取りこぼす
    bm = [name(c) for c, _ in idx.bm25.search(idx.tokenizer.tokenize(QUERY), flt, 3)]
    vec = [name(c) for c, _ in idx.vector.search(idx.embedder.embed_query(QUERY), flt, 2)]
    assert "semantic.txt" not in bm
    assert "keyword.txt" not in vec

    # ハイブリッドでは両方が上位3件に入り、両方に当たる文書が1位
    top = paths_of(Searcher(idx), QUERY, ["all"], top_k=3)
    assert top[0] == "both.txt"
    assert set(top) == {"both.txt", "keyword.txt", "semantic.txt"}


def test_scores_refs_and_top_k(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = tmp_path / "docs"
    _hybrid_corpus(docs)
    idx = make_index(make_settings(tmp_path, [(str(docs), ["all"])]), embedder=ConceptEmbedder())
    run_ingest(idx)
    hits = Searcher(idx).search(QUERY, ["all"], top_k=4)
    assert [h.ref for h in hits] == ["r1", "r2", "r3", "r4"]
    assert all(0 < h.score <= 1 for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert len(Searcher(idx).search(QUERY, ["all"], top_k=50)) == 10  # max_top_k で頭打ち
    assert Searcher(idx).search("   ", ["all"]) == []


def test_filters(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = tmp_path / "docs"
    a = write_text(docs / "契約" / "2025" / "傭船A.txt", "傭船料の支払期日は月末。\n")
    b = write_text(docs / "契約" / "2026" / "傭船B.txt", "傭船料の支払期日は10日。\n")
    c = write_text(docs / "総務" / "傭船メモ.md", "# メモ\n\n傭船料の支払期日の確認。\n")
    write_text(docs / "契約2" / "傭船C.txt", "傭船料の支払期日は20日。\n")  # 接頭辞「契約」とは別フォルダ
    os.utime(a, (parse_time("2025-03-01"), parse_time("2025-03-01")))
    os.utime(b, (parse_time("2026-03-01"), parse_time("2026-03-01")))
    os.utime(c, (parse_time("2026-06-01"), parse_time("2026-06-01")))
    s = make_settings(tmp_path, [(str(docs), ["all"]), (str(docs / "契約"), ["all"], "contract")])
    idx = make_index(s)
    run_ingest(idx)
    sr = Searcher(idx)
    q = "傭船料の支払期日"
    assert len(sr.search(q, ["all"], top_k=10)) == 4

    # path_prefix: フォルダ単位（区切り・大文字小文字・末尾の区切りの違いを吸収）
    pref = str(docs / "契約").replace("\\", "/").upper() + "/"
    assert sorted(paths_of(sr, q, ["all"], path_prefix=pref, top_k=10)) == ["傭船A.txt", "傭船B.txt"]
    assert paths_of(sr, q, ["all"], path_prefix=str(docs / "契約" / "2026"), top_k=10) == ["傭船B.txt"]
    assert paths_of(sr, q, ["all"], path_prefix=str(docs / "無い"), top_k=10) == []

    # doc_type
    assert sorted(paths_of(sr, q, ["all"], doc_type="contract", top_k=10)) == ["傭船A.txt", "傭船B.txt"]
    assert paths_of(sr, q, ["all"], doc_type="md", top_k=10) == ["傭船メモ.md"]

    # updated_after
    after = parse_time("2026-01-01T00:00:00Z")
    got = sorted(paths_of(sr, q, ["all"], updated_after=after, top_k=10))
    assert "傭船A.txt" not in got and {"傭船B.txt", "傭船メモ.md"} <= set(got)

    # 組み合わせ
    assert paths_of(sr, q, ["all"], doc_type="contract", updated_after=after, top_k=10) == ["傭船B.txt"]


def test_rrf_is_deterministic() -> None:
    fused = rrf([["a", "b", "c"], ["c", "a", "d"]], 60)
    ids = [x for x, _ in fused]
    assert ids[0] == "a"  # 1位＋2位
    assert ids[1] == "c"  # 3位＋1位
    assert set(ids) == {"a", "b", "c", "d"}
    assert rrf([["x"], ["y"]], 60)[0][0] == "x"  # 同点は ID 順


def test_excerpt_is_limited_and_centered() -> None:
    text = "前置き。" * 100 + "ここに支払期日の定めがある。" + "後書き。" * 100
    ex = make_excerpt(text, ["支払", "期日"], 80)
    assert len(ex) <= 80
    assert "支払期日" in ex
    assert ex.startswith("…") and ex.endswith("…")
    assert make_excerpt("短い本文", ["x"], 80) == "短い本文"
    assert len(make_excerpt("あ" * 500, ["zz"], 50)) <= 50


def test_excerpt_limit_applies_to_api_hits(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = tmp_path / "docs"
    write_text(
        docs / "長文.txt", "前置きの文章です。" * 80 + "傭船料の支払期日は月末。" + "後書きの文章です。" * 80
    )
    s = make_settings(tmp_path, [(str(docs), ["all"])])
    s.search.excerpt_chars = 60
    idx = make_index(s)
    run_ingest(idx)
    hits = Searcher(idx).search("傭船料の支払期日", ["all"], top_k=10)
    assert hits and all(len(h.excerpt) <= 60 for h in hits)
    assert any("支払期日" in h.excerpt for h in hits)
