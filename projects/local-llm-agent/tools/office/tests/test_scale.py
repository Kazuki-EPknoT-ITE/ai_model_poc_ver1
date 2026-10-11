"""対応規模（要件 6.4）と PRF-05（テキスト PDF 100 ページを 60 秒以内）の確認。"""

from __future__ import annotations

import time
from pathlib import Path

from conftest import Runner
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def test_prf05_text_pdf_100_pages_within_60s(run: Runner, ws: Path) -> None:
    c = canvas.Canvas(str(ws / "長文.pdf"), pagesize=A4)
    for page in range(1, 101):
        c.setFont("Helvetica", 9)
        y = 800
        for line in range(60):
            c.drawString(
                40, y, f"Page {page} line {line}: The charterer shall pay hire at the agreed rate per day."
            )
            y -= 12
        c.showPage()
    c.save()
    started = time.monotonic()
    env = run("pdf.read_pages", {"path": "長文.pdf", "pages": "1-100"})
    pages = len(env["data"]["pages"])
    while "continuation" in env:
        env = run("pdf.read_pages", {"continuation": env["continuation"]})
        pages += len(env["data"]["pages"])
    elapsed = time.monotonic() - started
    assert pages == 100
    assert elapsed < 60, f"{elapsed:.1f}s"


def test_xlsx_100k_rows_transform(run: Runner, ws: Path) -> None:
    wb = Workbook(write_only=True)
    sh = wb.create_sheet("明細")
    sh.append(["区分", "金額"])
    for i in range(100_000):
        sh.append([f"区分{i % 10}", (i % 1000) + 0.25])
    wb.save(ws / "大.xlsx")
    started = time.monotonic()
    env = run(
        "data.transform",
        {
            "inputs": [{"name": "m", "source": {"xlsx": "大.xlsx", "range": "明細!A1:B100001"}}],
            "steps": [
                {"op": "group", "by": ["区分"], "aggs": [{"column": "金額", "fn": "sum", "as": "合計"}]},
                {"op": "sort", "by": [{"column": "区分"}]},
            ],
            "output": {"return_only": True},
        },
    )
    elapsed = time.monotonic() - started
    assert env["data"]["rows_out"] == 10
    assert env["data"]["preview"][0] == ["区分0", 4952500]  # Decimal で誤差なく集計
    assert elapsed < 60, f"{elapsed:.1f}s"
