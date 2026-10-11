"""差分取り込み: 追加・変更・削除・ACL の変更・規則外・読めないファイル・NAS が見えないとき。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from qdrant_client import QdrantClient
from rag_testkit import IndexFactory, make_settings, paths_of, write_docx, write_text

from llm_rag.ingest import run_ingest
from llm_rag.search import Searcher


def _setup(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    write_text(docs / "総務" / "休暇規程.md", "# 休暇\n\n有給休暇は年20日とする。\n")
    write_text(docs / "総務" / "備品.txt", "備品の購入は総務部に申請する。\n")
    write_docx(
        docs / "契約" / "傭船契約.docx", [("h1", "第5条 支払期日"), ("p", "傭船料は毎月末日に支払う。")]
    )
    return docs


def test_add_unchanged_modify_delete(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = _setup(tmp_path)
    s = make_settings(tmp_path, [(str(docs), ["all"])])
    client = QdrantClient(location=":memory:")
    idx = make_index(s, client=client)

    st = run_ingest(idx)
    assert (st.scanned, st.added, st.updated, st.unchanged, st.deleted, st.failed) == (3, 3, 0, 0, 0, 0)
    assert st.chunks_written == idx.vector.count() == len(idx.bm25)

    # 2回目: 何も変わっていなければ飛ばす（状態はファイルから読み直す）
    idx2 = make_index(s, client=client)
    st2 = run_ingest(idx2)
    assert (st2.added, st2.updated, st2.unchanged, st2.deleted, st2.chunks_written) == (0, 0, 3, 0, 0)

    # 変更: 中身が変わった文書だけ取り込み直す。古い内容は検索に出ない
    write_text(docs / "総務" / "備品.txt", "備品の購入は経理部に申請する。文房具を含む。\n")
    # 削除
    (docs / "総務" / "休暇規程.md").unlink()
    # 追加
    write_text(docs / "総務" / "出張.md", "# 出張\n\n出張旅費は実費精算とする。\n")
    idx3 = make_index(s, client=client)
    st3 = run_ingest(idx3)
    assert (st3.added, st3.updated, st3.unchanged, st3.deleted) == (1, 1, 1, 1)
    sr = Searcher(idx3)
    assert paths_of(sr, "文房具 経理部", ["all"])[0] == "備品.txt"
    assert "休暇規程.md" not in paths_of(sr, "有給休暇", ["all"], top_k=10)
    assert paths_of(sr, "出張旅費", ["all"])[0] == "出張.md"
    total_chunks = sum(int(d["chunks"]) for d in idx3.docs.values())
    assert idx3.vector.count() == len(idx3.bm25) == total_chunks


def test_acl_change_reingests_and_unmapped_is_removed(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = _setup(tmp_path)
    client = QdrantClient(location=":memory:")
    s1 = make_settings(tmp_path, [(str(docs / "総務"), ["general"]), (str(docs / "契約"), ["executive"])])
    run_ingest(make_index(s1, client=client))
    assert "傭船契約.docx" not in paths_of(Searcher(make_index(s1, client=client)), "傭船料", ["general"])

    # 契約フォルダを chartering にも開放し、総務フォルダの規則を外す（規則外は取り込まない＝消す）
    s2 = make_settings(tmp_path, [(str(docs / "契約"), ["executive", "chartering"])])
    idx = make_index(s2, client=client)
    st = run_ingest(idx)
    assert st.updated == 1 and st.unchanged == 0
    assert st.skipped_no_acl == 2 and st.deleted == 2
    sr = Searcher(idx)
    assert paths_of(sr, "傭船料", ["chartering"])[0] == "傭船契約.docx"
    assert paths_of(sr, "備品", ["general"]) == []


def test_longest_prefix_rule_and_doc_type(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = _setup(tmp_path)
    write_text(docs / "契約" / "役員" / "報酬.txt", "役員報酬の改定案。\n")
    s = make_settings(
        tmp_path,
        [
            (str(docs), ["all"]),
            (str(docs / "契約"), ["chartering"], "contract"),
            (str(docs / "契約" / "役員"), ["executive"]),
        ],
    )
    idx = make_index(s)
    run_ingest(idx)
    by_name = {Path(d["path"]).name: d for d in idx.docs.values()}
    assert by_name["報酬.txt"]["acl_groups"] == ["executive"]
    assert by_name["傭船契約.docx"]["acl_groups"] == ["chartering"]
    assert by_name["傭船契約.docx"]["doc_type"] == "contract"
    assert by_name["備品.txt"]["doc_type"] == "txt"


def test_corrupt_file_counts_as_failure_and_keeps_previous(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = _setup(tmp_path)
    client = QdrantClient(location=":memory:")
    s = make_settings(tmp_path, [(str(docs), ["all"])])
    run_ingest(make_index(s, client=client))
    target = docs / "契約" / "傭船契約.docx"
    target.write_bytes(b"broken")
    write_text(docs / "壊れ.docx", "not a docx")
    idx = make_index(s, client=client)
    st = run_ingest(idx)
    assert st.failed == 2 and st.failures == {"CORRUPT_FILE": 2}
    assert st.deleted == 0
    # 読めなくなった文書は前回の索引を残す
    assert paths_of(Searcher(idx), "傭船料", ["all"])[0] == "傭船契約.docx"


def test_missing_source_does_not_delete_everything(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = _setup(tmp_path)
    client = QdrantClient(location=":memory:")
    s = make_settings(tmp_path, [(str(docs), ["all"])])
    run_ingest(make_index(s, client=client))
    moved = tmp_path / "offline"
    shutil.move(str(docs), str(moved))  # NAS が見えない状態
    idx = make_index(s, client=client)
    st = run_ingest(idx)
    assert st.deleted == 0 and st.failures.get("SOURCE_NOT_FOUND") == 1
    assert len(idx.docs) == 3


def test_skips_office_temp_files_and_rebuilds_on_model_change(
    tmp_path: Path, make_index: IndexFactory
) -> None:
    docs = _setup(tmp_path)
    write_text(docs / "~$傭船契約.docx", "lock")
    client = QdrantClient(location=":memory:")
    s = make_settings(tmp_path, [(str(docs), ["all"])])
    assert run_ingest(make_index(s, client=client)).scanned == 3
    # 埋め込みモデル（次元数）を変えたら全件を取り込み直す
    s.embedder.dim = 64
    idx = make_index(s, client=client)
    assert idx.needs_rebuild
    st = run_ingest(idx)
    assert st.rebuilt and st.added == 3
    assert idx.vector.dim() == 64


def test_mtime_becomes_updated_at(tmp_path: Path, make_index: IndexFactory) -> None:
    docs = _setup(tmp_path)
    f = docs / "総務" / "備品.txt"
    os.utime(f, (1_700_000_000, 1_700_000_000))
    idx = make_index(make_settings(tmp_path, [(str(docs), ["all"])]))
    run_ingest(idx)
    pts = idx.vector.client.scroll(idx.vector.collection, limit=100, with_payload=True)[0]
    p = next(x.payload for x in pts if x.payload and x.payload["path"].endswith("備品.txt"))
    assert p["updated_at"] == "2023-11-14T22:13:20Z"
    assert set(p) >= {
        "doc_id",
        "path",
        "location",
        "chunk_index",
        "text",
        "doc_type",
        "acl_groups",
        "updated_at",
        "file_hash",
    }
