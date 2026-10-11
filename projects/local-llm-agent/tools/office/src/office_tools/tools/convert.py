"""convert.*（protocol §3.5、DOC-08・09）。LibreOffice ヘッドレスで変換する（CSV → xlsx は自前）。"""

from __future__ import annotations

import datetime as dt
import shutil
import tempfile
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from office_tools import libreoffice
from office_tools.envelope import Outcome
from office_tools.errors import failed, invalid, unsupported
from office_tools.fsutil import atomic_write, existing_file, ext, normalize_arg, resolve
from office_tools.registry import ToolContext, tool
from office_tools.tools.csvtool import read_csv_file
from office_tools.tools.data import _from_text

SOURCES = {
    "docx": {".doc", ".rtf", ".odt", ".txt", ".wpd", ".docm", ".dotx", ".dot"},
    "xlsx": {".xls", ".ods", ".csv", ".xlsm", ".xlsb", ".xlt"},
    "pdf": {
        ".docx",
        ".doc",
        ".rtf",
        ".odt",
        ".txt",
        ".xlsx",
        ".xlsm",
        ".xls",
        ".ods",
        ".csv",
        ".pptx",
        ".ppt",
        ".odp",
    },
}


def _dst(args: dict[str, Any], target: str) -> str:
    if args.get("dst"):
        dst = str(args["dst"])
        if ext(Path(dst)) != f".{target}":
            raise invalid(f"dst の拡張子は .{target} にしてください")
        return dst
    src = normalize_arg(str(args["src"]))
    stem, dot, _ = src.rpartition(".")
    return f"{stem if dot else src}.{target}"


def _csv_to_xlsx(src: str, real: Path, out: Path) -> None:
    table = read_csv_file(src, real)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Sheet1"
    for i, row in enumerate(table.rows, start=1):
        for j, v in enumerate(row, start=1):
            value = _from_text(v) if i > 1 else v
            cell = ws.cell(row=i, column=j, value=value)
            if isinstance(value, dt.date):
                cell.number_format = "yyyy-mm-dd"
    wb.save(out)


def _convert(args: dict[str, Any], target: str) -> Outcome:
    src = args["src"]
    real_src = existing_file(src)
    if ext(real_src) not in SOURCES[target]:
        raise unsupported(src, f"{ext(real_src) or '拡張子なし'} は .{target} に変換できません")
    dst = _dst(args, target)
    real_dst = resolve(dst)
    if real_dst.resolve() == real_src.resolve():
        raise invalid("src と dst が同じです")
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="lla-convert-") as td:
        work = Path(td)
        if target == "xlsx" and ext(real_src) == ".csv":
            produced = work / "out.xlsx"
            _csv_to_xlsx(src, real_src, produced)
        else:
            if libreoffice.find_soffice() is None:
                raise libreoffice_missing()
            staged = work / f"input{ext(real_src)}"
            shutil.copyfile(real_src, staged)
            out_dir = work / "out"
            out_dir.mkdir()
            produced = libreoffice.convert(staged, target, out_dir)
        artifact = atomic_write(dst, real_dst, lambda tmp: shutil.copyfile(produced, tmp))
    if artifact["op"] == "overwrite":
        warnings.append(f"既存の {dst} を上書きしました")
    return Outcome(data={"artifact": artifact}, artifacts=[artifact], warnings=warnings)


def libreoffice_missing() -> Exception:
    return failed("変換エンジン(LibreOffice)が見つかりません。管理者に LibreOffice の導入を依頼してください")


@tool("convert.to_docx")
def convert_to_docx(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    return _convert(args, "docx")


@tool("convert.to_xlsx")
def convert_to_xlsx(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    return _convert(args, "xlsx")


@tool("convert.to_pdf")
def convert_to_pdf(ctx: ToolContext, args: dict[str, Any]) -> Outcome:
    return _convert(args, "pdf")
