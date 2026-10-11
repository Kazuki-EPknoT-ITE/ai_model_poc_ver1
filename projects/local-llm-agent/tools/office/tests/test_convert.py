"""convert.*（T-18）。LibreOffice が必要なものは導入されていなければスキップする。"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REAL_SOFFICE, Runner, needs_libreoffice
from office_fixtures import make_docx, make_xlsx
from openpyxl import load_workbook

from office_tools import libreoffice


def test_csv_to_xlsx_without_libreoffice(run: Runner, ws: Path) -> None:
    (ws / "銀行.csv").write_bytes('口座,金額,日付\r\n0012345,"1,200",2026/10/01\r\n'.encode("cp932"))
    env = run("convert.to_xlsx", {"src": "銀行.csv"})
    assert env["data"]["artifact"]["path"] == "銀行.xlsx" and env["data"]["artifact"]["op"] == "create"
    sh = load_workbook(ws / "銀行.xlsx").active
    assert sh is not None
    assert [c.value for c in sh[1]] == ["口座", "金額", "日付"]
    assert (
        sh["A2"].value == "0012345" and sh["B2"].value == 1200 and sh["C2"].value == dt.datetime(2026, 10, 1)
    )
    env = run("convert.to_xlsx", {"src": "銀行.csv"})
    assert env["data"]["artifact"]["op"] == "overwrite" and env["warnings"]


def test_convert_requires_libreoffice(run: Runner, ws: Path) -> None:
    make_docx(ws / "報告書.docx")
    err = run.error("convert.to_pdf", {"src": "報告書.docx"})
    assert err["code"] == "TOOL_FAILED" and err["kind"] == "permanent" and "LibreOffice" in err["message"]
    (ws / "古い.doc").write_bytes(b"\xd0\xcf\x11\xe0")
    assert run.error("convert.to_docx", {"src": "古い.doc"})["code"] == "TOOL_FAILED"


def test_convert_argument_errors(run: Runner, ws: Path) -> None:
    make_docx(ws / "報告書.docx")
    assert run.error("convert.to_xlsx", {"src": "報告書.docx"})["code"] == "UNSUPPORTED_FORMAT"
    assert run.error("convert.to_pdf", {"src": "報告書.docx", "dst": "out.docx"})["code"] == "INVALID_ARGS"
    assert run.error("convert.to_pdf", {"src": "無い.docx"})["code"] == "NOT_FOUND"


def test_libreoffice_env_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lo = libreoffice
    fake = tmp_path / "soffice.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("LIBREOFFICE_PATH", str(fake))
    assert lo.find_soffice() == fake
    monkeypatch.setenv("LIBREOFFICE_PATH", str(tmp_path / "none.exe"))
    assert lo.find_soffice() is None


@needs_libreoffice
@pytest.mark.parametrize(
    ("src", "tool", "suffix"),
    [("報告書.docx", "convert.to_pdf", ".pdf"), ("送金.xlsx", "convert.to_pdf", ".pdf")],
)
def test_convert_with_libreoffice(
    run: Runner, ws: Path, monkeypatch: pytest.MonkeyPatch, src: str, tool: str, suffix: str
) -> None:
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: REAL_SOFFICE)
    make_docx(ws / "報告書.docx")
    make_xlsx(ws / "送金.xlsx")
    env = run(tool, {"src": src})
    assert Path(ws / env["data"]["artifact"]["path"]).suffix == suffix


class FakeSoffice:
    """subprocess.run の代わり。--convert-to の出力として入力をそのまま複写する。"""

    def __init__(self, returncode: int = 0, timeout: bool = False) -> None:
        self.returncode = returncode
        self.timeout = timeout
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(cmd)
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd, 1)
        profile = next(a for a in cmd if a.startswith("-env:UserInstallation="))
        from urllib.parse import unquote, urlparse

        prof_path = Path(unquote(urlparse(profile.split("=", 1)[1]).path).lstrip("/"))
        xcu = (prof_path / "user" / "registrymodifications.xcu").read_text(encoding="utf-8")
        assert "OOXMLRecalcMode" in xcu
        src = Path(cmd[-1])
        out = Path(cmd[cmd.index("--outdir") + 1]) / f"{src.stem}.{cmd[cmd.index('--convert-to') + 1]}"
        if self.returncode == 0:
            shutil.copyfile(src, out)
        return subprocess.CompletedProcess(cmd, self.returncode, b"", b"")


@pytest.fixture
def fake_lo(ws: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSoffice:
    fake = FakeSoffice()
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: ws / "soffice.exe")
    monkeypatch.setattr(libreoffice.subprocess, "run", fake)
    return fake


def test_convert_pipeline_with_fake_soffice(run: Runner, ws: Path, fake_lo: FakeSoffice) -> None:
    make_docx(ws / "報告書.docx")
    env = run("convert.to_pdf", {"src": "報告書.docx", "dst": "出力/報告書.pdf"})
    assert env["data"]["artifact"]["path"] == "出力/報告書.pdf" and (ws / "出力" / "報告書.pdf").exists()
    cmd = fake_lo.calls[0]
    assert (
        cmd[0].endswith("soffice.exe") and "--headless" in cmd and cmd[cmd.index("--convert-to") + 1] == "pdf"
    )
    assert Path(cmd[-1]).name == "input.docx"  # 日本語のファイル名は一時的な名前にして渡す


def test_convert_failures_with_fake_soffice(run: Runner, ws: Path, fake_lo: FakeSoffice) -> None:
    make_docx(ws / "報告書.docx")
    fake_lo.returncode = 1
    assert run.error("convert.to_pdf", {"src": "報告書.docx"})["code"] == "TOOL_FAILED"
    fake_lo.timeout = True
    err = run.error("convert.to_pdf", {"src": "報告書.docx"})
    assert err["code"] == "TIMEOUT" and err["kind"] == "transient"


def test_recalc_pipeline_with_fake_soffice(run: Runner, ws: Path, fake_lo: FakeSoffice) -> None:
    make_xlsx(ws / "送金.xlsx")
    env = run("xlsx.recalc", {"path": "送金.xlsx"})
    assert env["data"] == {"ok": True, "errors": []}
    assert fake_lo.calls and fake_lo.calls[0][fake_lo.calls[0].index("--convert-to") + 1] == "xlsx"
