"""抽出結果の検算と確信度（確信度はモデルに自己申告させず、ツール側で計算する）。"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from office_tools.invoice.schema import (
    BANK_FIELDS,
    DATE_FIELDS,
    ISO_4217,
    LINE_FIELDS,
    LINE_NUMBERS,
    NUMBER_FIELDS,
    TEXT_FIELDS,
    ZERO_DECIMAL,
)
from office_tools.scalars import to_json

REVIEW_THRESHOLD = 0.8
NOT_FOUND_FACTOR = 0.6
CHECK_FAILED_FACTOR = 0.5
UNKNOWN_OCR_CONFIDENCE = 0.7

_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def to_number(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    s = unicodedata.normalize("NFKC", str(v)).strip()
    s = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    if not s or s in ("-", ".", "-."):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def to_date(v: Any) -> dt.date | None:
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v.strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def norm_text(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold()


def date_variants(d: dt.date) -> list[str]:
    y, m, dd = d.year, d.month, d.day
    mon = _MONTHS[m - 1]
    out = [
        f"{y}-{m:02d}-{dd:02d}",
        f"{y}/{m:02d}/{dd:02d}",
        f"{y}/{m}/{dd}",
        f"{y}.{m:02d}.{dd:02d}",
        f"{y}.{m}.{dd}",
        f"{y}年{m}月{dd}日",
        f"{y}年{m:02d}月{dd:02d}日",
        f"{mon} {dd}, {y}",
        f"{mon[:3]} {dd}, {y}",
        f"{dd} {mon} {y}",
        f"{dd} {mon[:3]} {y}",
        f"{dd:02d} {mon[:3]} {y}",
        f"{dd:02d}-{mon[:3]}-{y}",
        f"{m:02d}/{dd:02d}/{y}",
        f"{dd:02d}/{m:02d}/{y}",
    ]
    if y >= 2019:
        out.append(f"令和{y - 2018}年{m}月{dd}日")
    return out


def number_variants(d: Decimal) -> list[str]:
    q = d.normalize()
    plain = format(q, "f")
    out = {plain}
    if "." not in plain:
        out.add(plain + ".00")
    else:
        whole, frac = plain.split(".")
        out.add(f"{whole}.{frac.ljust(2, '0')}")
    with_commas = set()
    for v in out:
        whole, _, frac = v.partition(".")
        neg = whole.startswith("-")
        digits = whole.lstrip("-")
        grouped = f"{int(digits):,}" if digits else digits
        with_commas.add(("-" if neg else "") + grouped + ("." + frac if frac else ""))
    return sorted(out | with_commas, key=len, reverse=True)


@dataclass
class PageText:
    text: dict[int, str]
    confidence: dict[int, float]
    ocr_pages: set[int] = field(default_factory=set)

    def normalized(self, page: int) -> str:
        return norm_text(self.text.get(page, ""))


def find_in_text(pages: PageText, page: int | None, variants: list[str]) -> tuple[int | None, str | None]:
    """値がテキストに含まれるかを探す。見つかったページと文字列を返す。"""
    order = ([page] if page in pages.text else []) + [p for p in pages.text if p != page]
    for p in order:
        assert p is not None
        hay = pages.normalized(p)
        hay_nc = hay.replace(",", "")
        for v in variants:
            nv = norm_text(v)
            if nv and (nv in hay or nv.replace(",", "") in hay_nc):
                return p, v
    return None, None


@dataclass
class FieldResult:
    key: str
    value: Any
    page: int | None
    confidence: float
    found_text: str | None = None
    bbox: list[float] | None = None


def _variants(kind: str, value: Any) -> list[str]:
    if kind == "date":
        d = to_date(value)
        return date_variants(d) if d else [str(value)]
    if kind == "number":
        n = to_number(value)
        return number_variants(n) if n is not None else [str(value)]
    return [str(value)]


def evaluate(
    raw: dict[str, Any], pages: PageText
) -> tuple[dict[str, FieldResult], list[dict[str, Any]], bool]:
    """LLM の出力 → (fields, checks, needs_review)。"""
    fields: dict[str, FieldResult] = {}

    def add(key: str, kind: str, value: Any, page: Any) -> None:
        pg = page if isinstance(page, int) and not isinstance(page, bool) and page in pages.text else None
        if value is None or (isinstance(value, str) and not value.strip()):
            fields[key] = FieldResult(key, None, pg, 1.0)
            return
        if kind == "date":
            d = to_date(value)
            out_value: Any = {"date": d.isoformat()} if d else str(value)
        elif kind == "number":
            n = to_number(value)
            out_value = to_json(n) if n is not None else str(value)
        else:
            out_value = str(value).strip()
        found_page, found = find_in_text(pages, pg, _variants(kind, value))
        base_page = found_page or pg
        conf = (
            pages.confidence.get(base_page, 1.0) if base_page else min(pages.confidence.values(), default=1.0)
        )
        if found is None:
            conf *= NOT_FOUND_FACTOR
        fields[key] = FieldResult(key, out_value, base_page, round(conf, 3), found)

    for name in DATE_FIELDS:
        f = raw.get(name) or {}
        add(name, "date", f.get("value"), f.get("page"))
    for name in TEXT_FIELDS:
        f = raw.get(name) or {}
        value = f.get("value")
        if name == "currency" and isinstance(value, str):
            value = value.strip().upper()
        add(name, "text", value, f.get("page"))
    for name in NUMBER_FIELDS:
        f = raw.get(name) or {}
        add(name, "number", f.get("value"), f.get("page"))
    bank = raw.get("bank") or {}
    for name in BANK_FIELDS:
        f = bank.get(name) or {}
        add(f"bank.{name}", "text", f.get("value"), f.get("page"))
    lines = raw.get("lines") or []
    for i, line in enumerate(lines):
        for name in LINE_FIELDS:
            add(
                f"lines.{i}.{name}",
                "number" if name in LINE_NUMBERS else "text",
                line.get(name),
                line.get("page"),
            )

    checks: list[dict[str, Any]] = []
    blame: dict[str, list[str]] = {}

    def check(name: str, ok: bool, detail: str, related: list[str]) -> None:
        entry: dict[str, Any] = {"name": name, "ok": ok}
        if detail:
            entry["detail"] = detail
        checks.append(entry)
        if not ok:
            for k in related:
                blame.setdefault(k, []).append(name)

    def num(key: str) -> Decimal | None:
        f = fields.get(key)
        return to_number(f.value) if f and f.value is not None and not isinstance(f.value, dict) else None

    required = ["issue_date", "issuer_name", "currency", "total"]
    missing = [k for k in required if fields[k].value is None]
    check("required", not missing, ("未取得: " + ", ".join(missing)) if missing else "", [])

    currency = fields["currency"].value
    cur_ok = isinstance(currency, str) and currency in ISO_4217
    check(
        "currency_code",
        cur_ok,
        "" if cur_ok else f"通貨コード {currency} は ISO 4217 にありません",
        ["currency"],
    )
    tol = Decimal(1) if isinstance(currency, str) and currency in ZERO_DECIMAL else Decimal("0.01")

    issue = to_date(
        (fields["issue_date"].value or {}).get("date")
        if isinstance(fields["issue_date"].value, dict)
        else None
    )
    due = to_date(
        (fields["due_date"].value or {}).get("date") if isinstance(fields["due_date"].value, dict) else None
    )
    problems = []
    if fields["issue_date"].value is not None and issue is None:
        problems.append("発行日が日付として不正です")
    if fields["due_date"].value is not None and due is None:
        problems.append("支払期日が日付として不正です")
    today = dt.date.today()
    if issue and not (dt.date(2000, 1, 1) <= issue <= today + dt.timedelta(days=366)):
        problems.append(f"発行日 {issue} が想定の範囲外です")
    if issue and due and due < issue:
        problems.append("支払期日が発行日より前です")
    check("dates_valid", not problems, "; ".join(problems), ["issue_date", "due_date"])

    line_amounts = [num(f"lines.{i}.amount") for i in range(len(lines))]
    subtotal, tax, total = num("subtotal"), num("tax"), num("total")
    if lines and all(a is not None for a in line_amounts):
        s = sum((a for a in line_amounts if a is not None), Decimal(0))
        if subtotal is not None:
            ok = abs(s - subtotal) <= tol
            check(
                "lines_sum",
                ok,
                "" if ok else f"明細の合計 {s} が小計 {subtotal} と一致しません",
                ["subtotal"] + [f"lines.{i}.amount" for i in range(len(lines))],
            )
        elif total is not None and not tax:
            ok = abs(s - total) <= tol
            check(
                "lines_sum",
                ok,
                "" if ok else f"明細の合計 {s} が合計 {total} と一致しません",
                ["total"] + [f"lines.{i}.amount" for i in range(len(lines))],
            )
        else:
            check("lines_sum", True, "小計が無いため対象外", [])
    elif lines:
        check(
            "lines_sum", False, "金額の無い明細があります", [f"lines.{i}.amount" for i in range(len(lines))]
        )
    else:
        check("lines_sum", True, "明細なし（対象外）", [])

    bad_lines = []
    for i in range(len(lines)):
        q, u, a = num(f"lines.{i}.qty"), num(f"lines.{i}.unit_price"), num(f"lines.{i}.amount")
        if q is not None and u is not None and a is not None and abs(q * u - a) > tol:
            bad_lines.append(i)
    check(
        "line_amounts",
        not bad_lines,
        ("数量×単価が金額と合わない明細: " + ", ".join(map(str, bad_lines))) if bad_lines else "",
        [f"lines.{i}.amount" for i in bad_lines],
    )

    if subtotal is not None and total is not None:
        expected = subtotal + (tax or Decimal(0))
        ok = abs(expected - total) <= tol
        check(
            "total_eq",
            ok,
            "" if ok else f"小計＋税 {expected} が合計 {total} と一致しません",
            ["subtotal", "tax", "total"],
        )
    else:
        check("total_eq", True, "小計か合計が無いため対象外", [])

    for key in blame:
        if key in fields and fields[key].value is not None:
            fields[key].confidence = round(fields[key].confidence * CHECK_FAILED_FACTOR, 3)
    needs_review = any(not c["ok"] for c in checks) or any(
        f.value is not None and f.confidence < REVIEW_THRESHOLD for f in fields.values()
    )
    return fields, checks, needs_review
