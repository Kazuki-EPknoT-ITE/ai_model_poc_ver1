"""分割: 見出し・表・ページの境界、重なり、出典表示。"""

from __future__ import annotations

from pathlib import Path

import pytest
from rag_testkit import write_docx, write_pdf, write_text, write_xlsx

from llm_rag.chunking import Unit, chunk_units
from llm_rag.config import ChunkSettings
from llm_rag.extract import ExtractError, extract_units

SMALL = ChunkSettings(max_tokens=100, overlap_tokens=20)  # 150 文字・重なり 30 文字


def _sentences(prefix: str, n: int) -> list[str]:
    return [f"{prefix}の文その{i:03d}です。" for i in range(n)]


def test_long_section_is_split_with_overlap() -> None:
    lines = _sentences("本文", 40)
    chunks = chunk_units([Unit("section", lines, location="第1章", context="第1章")], SMALL)
    assert len(chunks) > 3
    for c in chunks:
        assert len(c.text) <= SMALL.max_chars
        assert c.text.startswith("第1章\n")
        assert c.location == "第1章"
    # 連続するチャンクは前のチャンクの末尾を重ねて持つ
    for prev, cur in zip(chunks, chunks[1:], strict=False):
        prev_body = prev.text.split("\n", 1)[1]
        cur_body = cur.text.split("\n", 1)[1]
        tail = prev_body[-15:]
        assert tail in cur_body[: SMALL.overlap_chars + 20], (prev_body, cur_body)
    # 全文が欠けずに入っている
    joined = "\n".join(c.text for c in chunks)
    for s in lines:
        assert s in joined


def test_very_long_line_is_hard_split() -> None:
    line = "あ" * 500 + "い" * 500
    chunks = chunk_units([Unit("text", [line], location="本文")], SMALL)
    assert all(len(c.text) <= SMALL.max_chars for c in chunks)
    assert "".join(c.text for c in chunks).count("い") >= 500


def test_chunks_never_cross_units() -> None:
    units = [
        Unit("section", ["Aの内容。"], location="A", context="A"),
        Unit("section", ["Bの内容。"], location="B", context="B"),
        Unit("page", ["Cの内容。"], location="p.3"),
    ]
    chunks = chunk_units(units, SMALL)
    assert [c.location for c in chunks] == ["A", "B", "p.3"]
    assert "Bの内容" not in chunks[0].text and "Aの内容" not in chunks[1].text


def test_docx_headings_and_tables(tmp_path: Path) -> None:
    p = write_docx(
        tmp_path / "規程.docx",
        [
            ("h1", "第2章 支払"),
            ("p", "支払に関する総則。"),
            ("h2", "第5条 支払期日"),
            ("p", "傭船料は毎月末日までに支払う。"),
            ("table", [["項目", "金額"], *[[f"費目{i}", f"{i * 100}"] for i in range(1, 30)]]),
            ("h1", "第3章 解約"),
            ("p", "解約の手続き。"),
        ],
    )
    units = extract_units(p)
    chunks = chunk_units(units, SMALL)
    by_loc = {c.location: c.text for c in chunks}
    assert "第2章 支払" in by_loc and "支払に関する総則" in by_loc["第2章 支払"]
    assert "第2章 支払 > 第5条 支払期日" in by_loc
    assert "傭船料" in by_loc["第2章 支払 > 第5条 支払期日"]
    assert "第3章 解約" in by_loc and "傭船料" not in by_loc["第3章 解約"]
    tables = [c for c in chunks if c.kind == "table"]
    assert len(tables) >= 2  # 表は行で分割される
    for t in tables:
        assert t.location.startswith("第2章 支払 > 第5条 支払期日 / 表1（行")
        assert "項目 | 金額" in t.text  # 見出し行を繰り返す
        assert "解約" not in t.text and "総則" not in t.text
    assert "（行2-" in tables[0].location  # 1行目は見出し行なので本文は2行目から


def test_pdf_pages_and_article_location(tmp_path: Path) -> None:
    p = write_pdf(
        tmp_path / "契約.pdf",
        [
            ["傭船契約書", "第1条 目的", "本契約は船舶の傭船について定める。"],
            ["第5条 支払期日", "傭船料は毎月末日に支払う。"],
            ["続き: 遅延した場合は利息を付す。"],
        ],
    )
    chunks = chunk_units(extract_units(p), SMALL)
    locs = [c.location for c in chunks]
    assert locs[0].startswith("p.1")
    assert locs[1] == "p.2 / 第5条 支払期日"
    assert locs[2] == "p.3 / 第5条 支払期日"  # 前のページの条が続いている
    assert "傭船料は毎月末日に支払う" in chunks[1].text
    assert "遅延" not in chunks[1].text  # ページをまたがない


def test_xlsx_rows_with_header(tmp_path: Path) -> None:
    rows: list[list[object]] = [["社員番号", "氏名", "部署"]]
    rows += [[1000 + i, f"社員{i}", "営業部" if i % 2 else "総務部"] for i in range(60)]
    p = write_xlsx(tmp_path / "名簿.xlsx", {"名簿": rows, "空": []})
    chunks = chunk_units(extract_units(p), SMALL)
    assert len(chunks) > 2
    for c in chunks:
        assert c.location.startswith("名簿!")
        assert "社員番号 | 氏名 | 部署" in c.text
    assert chunks[0].location.startswith("名簿!2:")
    last = int(chunks[-1].location.split(":")[1])
    assert last == 61


def test_markdown_headings_and_table(tmp_path: Path) -> None:
    p = write_text(
        tmp_path / "手順.md",
        "# 経費精算\n\n申請はシステムから行う。\n\n## 締め切り\n\n毎月5日まで。\n\n"
        "| 費目 | 上限 |\n| --- | --- |\n| 交通費 | 5000 |\n| 宿泊費 | 12000 |\n\n## その他\n\n問い合わせは経理へ。\n",
    )
    chunks = chunk_units(extract_units(p), SMALL)
    kinds = [(c.kind, c.location.split(" / ")[0]) for c in chunks]
    assert ("section", "経費精算") in kinds
    assert ("section", "経費精算 > 締め切り") in kinds
    table = next(c for c in chunks if c.kind == "table")
    assert "| 費目 | 上限 |" in table.text and "宿泊費" in table.text
    assert "問い合わせ" not in table.text
    assert table.location.startswith("経費精算 > 締め切り / 表1")


def test_csv_cp932_and_txt(tmp_path: Path) -> None:
    csv = write_text(
        tmp_path / "取引先.csv",
        "会社名,電話\n" + "".join(f"会社{i},03-0000-{i:04d}\n" for i in range(5)),
        "cp932",
    )
    chunks = chunk_units(extract_units(csv), SMALL)
    assert chunks[0].location == "行2-6"
    assert "会社名 | 電話" in chunks[0].text and "会社3" in chunks[0].text
    txt = write_text(tmp_path / "メモ.txt", "\n".join(_sentences("メモ", 30)))
    tchunks = chunk_units(extract_units(txt), SMALL)
    assert tchunks[0].location.startswith("行1-")
    assert len(tchunks) > 1


def test_unreadable_files(tmp_path: Path) -> None:
    bad = write_text(tmp_path / "壊れ.docx", "not a zip")
    with pytest.raises(ExtractError) as e:
        extract_units(bad)
    assert e.value.code == "CORRUPT_FILE"
    with pytest.raises(ExtractError) as e2:
        extract_units(write_text(tmp_path / "x.bin", "x"))
    assert e2.value.code == "UNSUPPORTED_FORMAT"
