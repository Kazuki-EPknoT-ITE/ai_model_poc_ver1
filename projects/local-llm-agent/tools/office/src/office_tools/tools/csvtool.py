"""csv.read / csv.write（protocol §3.3、XLS-07）。"""

from __future__ import annotations

import codecs
import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from charset_normalizer import from_bytes

from office_tools.envelope import Item, Outcome, paginate
from office_tools.errors import invalid, locked
from office_tools.fsutil import atomic_write, ensure_absent, existing_file, resolve
from office_tools.registry import ToolContext, tool
from office_tools.scalars import to_text

_SJIS_FAMILY = {"cp932", "shift_jis", "shift_jis_2004", "shift_jisx0213", "sjis", "ms932", "mskanji"}
DELIMITERS = [",", "\t", ";", "|"]


def detect_encoding(raw: bytes) -> str:
    """UTF-8（BOM 付き含む）/ CP932 を判定する。それ以外は charset-normalizer の推定。"""
    if raw.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    best = from_bytes(raw[:500_000]).best()
    enc = (best.encoding if best else "").lower().replace("-", "_")
    if not enc or enc in _SJIS_FAMILY:
        return "cp932"
    try:
        raw.decode("cp932")
        # 日本の業務 CSV では CP932 の可能性が高い。推定がそれ以外でも CP932 で読めれば CP932 とする
        if enc not in ("euc_jp", "iso2022_jp", "utf_16", "utf_16_le", "utf_16_be"):
            return "cp932"
    except UnicodeDecodeError:
        pass
    return codecs.lookup(enc).name


def detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:50])
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample else ""
        counts = {d: first.count(d) for d in DELIMITERS}
        best = max(counts, key=lambda d: counts[d])
        return best if counts[best] > 0 else ","


@dataclass
class CsvTable:
    encoding: str
    delimiter: str
    rows: list[list[str]]
    replaced_chars: bool


def read_csv_file(path: str, real: Path, encoding: str = "auto", delimiter: str = "auto") -> CsvTable:
    try:
        raw = real.read_bytes()
    except PermissionError as e:
        raise locked(path) from e
    enc = detect_encoding(raw) if encoding == "auto" else encoding
    text = raw.decode(enc, errors="replace")
    replaced = "�" in text
    if text.startswith("﻿"):
        text = text[1:]
    delim = detect_delimiter(text) if delimiter == "auto" else delimiter
    rows = [row for row in csv.reader(io.StringIO(text, newline=""), delimiter=delim)]
    return CsvTable(enc, delim, rows, replaced)


@tool("csv.read")
def csv_read(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    path = args["path"]
    real = existing_file(path)
    table = read_csv_file(path, real, args.get("encoding", "auto"), args.get("delimiter", "auto"))
    header = table.rows[0] if table.rows else []
    body = table.rows[1:]
    offset = int(args.get("offset", 0))
    max_rows = int(args.get("max_rows", 2000))
    base: dict[str, Any] = {
        "encoding": table.encoding,
        "delimiter": table.delimiter,
        "header": header,
        "total_rows": len(body),
        "offset": offset,
    }
    warnings = []
    if table.replaced_chars:
        warnings.append(
            f"{table.encoding} として読めない文字があり、� に置き換えました。encoding を指定し直してください"
        )
    items = [Item({"rows": row}) for row in body[offset:]]
    return paginate(ctx.store, "csv.read", base, ("rows",), items, max_items=max_rows, warnings=warnings)


def write_csv_bytes(header: list[str] | None, rows: list[list[Any]], encoding: str) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    if header:
        w.writerow(header)
    for row in rows:
        w.writerow([to_text(v) for v in row])
    text = buf.getvalue()
    try:
        return text.encode(encoding)
    except UnicodeEncodeError as e:
        bad = text[e.start : e.end]
        line = text.count("\n", 0, e.start) + 1
        raise invalid(
            f"{line} 行目の文字「{bad}」は {encoding} で書けません。encoding を utf-8-sig にするか、文字を置き換えてください",
            line=line,
        ) from e


@tool("csv.write")
def csv_write(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    path = args["path"]
    real = resolve(path)
    ensure_absent(path, real, bool(args.get("overwrite", False)))
    data = write_csv_bytes(args.get("header"), args["rows"], args.get("encoding", "utf-8-sig"))
    artifact = atomic_write(path, real, lambda tmp: tmp.write_bytes(data))
    return Outcome(data={"artifact": artifact}, artifacts=[artifact])
