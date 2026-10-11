"""ACL（SEC-07 / ADM-02）: 権限の無いグループには、他の文書が上位に来ても絶対に出ない。
件数・順位・点数からも推測できない（ACL を検索条件として両方の索引に入れているため）。
"""

from __future__ import annotations

from pathlib import Path

from rag_testkit import ConceptEmbedder, IndexFactory, make_settings, write_text

from llm_rag.ingest import run_ingest
from llm_rag.model import SearchFilter
from llm_rag.search import Hit, Searcher

QUERY = "役員報酬の支払期日"


def _public(docs: Path) -> None:
    write_text(docs / "公開" / "給与.txt", "給与の支払期日は毎月25日とする。\n")
    write_text(docs / "公開" / "経費.txt", "経費の振込は翌月末までに行う。\n")
    write_text(docs / "公開" / "会議.txt", "会議室の予約は前日までに行う。\n")


def _secret(docs: Path) -> None:
    # 検索語にぴったり当たる役員向け資料（公開文書より上位に来る内容）
    for i in range(5):
        write_text(
            docs / "役員" / f"報酬{i}.txt",
            f"役員報酬の支払期日は毎月末日。役員報酬の支払期日の変更案その{i}。役員報酬。\n",
        )


def _search_all(idx_searcher: Searcher, groups: list[str]) -> list[Hit]:
    return idx_searcher.search(QUERY, groups, top_k=10)


def test_hidden_documents_never_appear(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = tmp_path / "docs"
    _public(docs)
    _secret(docs)
    s = make_settings(tmp_path, [(str(docs / "公開"), ["all"]), (str(docs / "役員"), ["executive"])])
    idx = make_index(s, embedder=ConceptEmbedder())
    run_ingest(idx)
    sr = Searcher(idx)

    exec_hits = sr.search(QUERY, ["executive"], top_k=5)
    assert len(exec_hits) == 5 and all("役員" in Path(h.path).parent.name for h in exec_hits)

    staff_hits = sr.search(QUERY, ["all"], top_k=10)
    assert staff_hits, "公開文書は見つかる"
    assert all("役員" not in h.path for h in staff_hits)
    assert all("役員報酬" not in h.excerpt for h in staff_hits)
    # 両方の索引の候補にも出ない（後から除外しているのではない）
    flt = SearchFilter(["all"])
    vec_ids = [c for c, _ in idx.vector.search(idx.embedder.embed_query(QUERY), flt, 100)]
    bm_ids = [c for c, _ in idx.bm25.search(idx.tokenizer.tokenize(QUERY), flt, 100)]
    secret_ids = {cid for cid, d in idx.bm25._docs.items() if "executive" in d.acl}
    assert secret_ids and not (set(vec_ids) | set(bm_ids)) & secret_ids
    visible = {cid for cid, d in idx.bm25._docs.items() if "all" in d.acl}
    assert set(vec_ids) == visible  # ベクトル側は件数上限内なら見える全チャンクが候補（見えないものは0件）
    assert set(bm_ids) <= visible

    # グループが無い・知らないグループは何も返さない
    assert sr.search(QUERY, [], top_k=5) == []
    assert sr.search(QUERY, ["unknown-group"], top_k=5) == []


def test_results_are_identical_with_or_without_hidden_documents(
    tmp_path: Path, make_index: IndexFactory
) -> None:
    """権限の無い文書の有無で、件数・順位・点数・抜粋が1つも変わらない（存在を推測できない）。"""

    def run(with_secret: bool, root: Path) -> list[Hit]:
        docs = root / "docs"
        _public(docs)
        if with_secret:
            _secret(docs)
        s = make_settings(root, [(str(docs / "公開"), ["all"]), (str(docs / "役員"), ["executive"])])
        idx = make_index(s, embedder=ConceptEmbedder())
        run_ingest(idx)
        return _search_all(Searcher(idx), ["all"])

    a = run(False, tmp_path / "a")
    b = run(True, tmp_path / "b")

    def view(hits: list[Hit]) -> list[tuple[str, str, str, float, str]]:
        return [(h.ref, Path(h.path).name, h.location, h.score, h.excerpt) for h in hits]

    assert view(a) == view(b)
    assert len(a) == 3


def test_bm25_statistics_use_only_visible_documents(tmp_path: Path, make_index: IndexFactory) -> None:
    """BM25 の IDF・平均長は見える文書だけで計算する（見えない文書の語の多さが点数に出ない）。"""
    docs = tmp_path / "docs"
    write_text(docs / "公開" / "a.txt", "監査の手順。\n")
    write_text(docs / "公開" / "b.txt", "監査の記録。\n")
    for i in range(20):
        write_text(docs / "役員" / f"s{i}.txt", "監査" * 50 + "\n")
    s = make_settings(tmp_path, [(str(docs / "公開"), ["all"]), (str(docs / "役員"), ["executive"])])
    idx = make_index(s)
    run_ingest(idx)
    with_secret = idx.bm25.search(idx.tokenizer.tokenize("監査"), SearchFilter(["all"]), 10)
    for did in [d for d, m in idx.docs.items() if "executive" in m["acl_groups"]]:
        idx.bm25.remove_doc(did)
    without = idx.bm25.search(idx.tokenizer.tokenize("監査"), SearchFilter(["all"]), 10)
    assert with_secret == without


def test_user_with_multiple_groups_sees_union(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = tmp_path / "docs"
    _public(docs)
    _secret(docs)
    s = make_settings(tmp_path, [(str(docs / "公開"), ["all"]), (str(docs / "役員"), ["executive"])])
    idx = make_index(s, embedder=ConceptEmbedder())
    run_ingest(idx)
    hits = Searcher(idx).search(QUERY, ["all", "executive"], top_k=10)
    dirs = {Path(h.path).parent.name for h in hits}
    assert dirs == {"公開", "役員"}
