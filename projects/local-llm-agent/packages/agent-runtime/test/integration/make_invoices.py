"""結合試験用の請求書 PDF を合成する（実データは使わない）。

使い方: uv run python make_invoices.py <出力フォルダ> <件数>
INV-00N.pdf を作る。金額は N*1000 USD（モックの fixture と一致させる）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def make(path: Path, n: int) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    _, h = A4
    c.setFont("Helvetica", 11)
    amount = f"{n * 1000:,.2f}"
    lines = [
        "INVOICE",
        f"Issuer: Example Shipping Co. {n}",
        "Bill To: Sample Marine K.K.",
        "Vessel: MV EXAMPLE STAR",
        f"Issue Date: 2026-09-0{n}   Due Date: 2026-10-31",
        "Currency: USD",
        f"Charter hire   1 x {amount} = {amount}",
        f"Subtotal: {amount}",
        "Tax: 0.00",
        f"Total: {amount}",
    ]
    y = h - 72
    for line in lines:
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    c.save()


def main() -> int:
    out = Path(sys.argv[1])
    count = int(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for n in range(1, count + 1):
        make(out / f"INV-00{n}.pdf", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
