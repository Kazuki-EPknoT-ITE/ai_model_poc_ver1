"""pdf.*（T-14）と OCR の依頼（sampling）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from office_fixtures import make_pdf
from office_testkit import FakeSampler, Runner

from office_tools.registry import SamplingRequest


@pytest.fixture
def pdf(ws: Path) -> Path:
    return make_pdf(ws / "契約書.pdf")


def test_info_detects_scanned_pages(run: Runner, pdf: Path) -> None:
    d = run("pdf.info", {"path": "契約書.pdf"})["data"]
    assert d["pages"] == 4 and d["encrypted"] is False
    assert d["text_pages"] == [1, 2, 3] and d["scanned_pages"] == [4]
    assert d["title"] == "Test Document"


def test_read_pages_untrusted(run: Runner, pdf: Path) -> None:
    env = run("pdf.read_pages", {"path": "契約書.pdf", "pages": "1,4"})
    pages = env["data"]["pages"]
    assert pages[0] == {"page": 1, "chars": pages[0]["chars"], "has_text": True, "text_ref": "契約書.pdf#p.1"}
    assert pages[1]["has_text"] is False
    assert env["untrusted"][0]["ref"] == "契約書.pdf#p.1"
    assert "Hire: USD 12,500 per day" in env["untrusted"][0]["text"]
    assert "Hire" not in json.dumps(env["data"])  # 本文は data に置かない
    assert any("pdf.ocr" in w for w in env["warnings"])


def test_read_pages_two_columns_reading_order(run: Runner, pdf: Path) -> None:
    text = run("pdf.read_pages", {"path": "契約書.pdf", "pages": "2"})["untrusted"][0]["text"]
    lines = text.splitlines()
    assert lines[0] == "Left column line 1"
    assert lines.index("Left column line 12") < lines.index("Right column line 1")
    raw = run("pdf.read_pages", {"path": "契約書.pdf", "pages": "2", "layout": "raw"})["untrusted"][0]["text"]
    assert "Right column line 1" in raw


def test_read_pages_errors(run: Runner, pdf: Path, ws: Path) -> None:
    assert run.error("pdf.read_pages", {"path": "契約書.pdf", "pages": "5"})["code"] == "INVALID_ARGS"
    assert run.error("pdf.read_pages", {"path": "契約書.pdf", "pages": "3-1"})["code"] == "INVALID_ARGS"
    (ws / "壊れ.pdf").write_bytes(b"%PDF-1.4 broken")
    assert run.error("pdf.info", {"path": "壊れ.pdf"})["code"] == "CORRUPT_FILE"
    (ws / "a.txt").write_text("x", encoding="utf-8")
    assert run.error("pdf.info", {"path": "a.txt"})["code"] == "UNSUPPORTED_FORMAT"


def test_extract_tables(run: Runner, pdf: Path) -> None:
    tables = run("pdf.extract_tables", {"path": "契約書.pdf", "pages": "1-3"})["data"]["tables"]
    assert len(tables) == 1
    t = tables[0]
    assert t["page"] == 3 and t["header"] == ["Item", "Qty", "Amount"]
    assert t["rows"] == [["Fuel", "2", "1000"], ["Port", "1", "300"]]
    assert len(t["bbox"]) == 4


def test_password_protected(run: Runner, ws: Path) -> None:
    make_pdf(ws / "鍵.pdf", encrypt="secret")
    err = run.error("pdf.info", {"path": "鍵.pdf"})
    assert err["code"] == "PASSWORD_REQUIRED" and err["kind"] == "deferred"
    err = run.error("pdf.info", {"path": "鍵.pdf", "password_ref": "pw-1"})
    assert err["code"] == "PASSWORD_REQUIRED"  # 参照が Runtime から渡されていない
    run.passwords = {"pw-1": "wrong"}
    assert run.error("pdf.info", {"path": "鍵.pdf", "password_ref": "pw-1"})["code"] == "PASSWORD_REQUIRED"
    run.passwords = {"pw-1": "secret"}
    d = run("pdf.info", {"path": "鍵.pdf", "password_ref": "pw-1"})["data"]
    assert d["encrypted"] is True and d["pages"] == 4
    env = run("pdf.read_pages", {"path": "鍵.pdf", "pages": "1", "password_ref": "pw-1"})
    assert "Charter Party" in env["untrusted"][0]["text"]
    assert "secret" not in json.dumps(env)


def test_ocr_uses_sampling(run: Runner, pdf: Path) -> None:
    def reply(req: SamplingRequest) -> str:
        assert req.image_png is not None and req.image_png.startswith(b"\x89PNG")
        return json.dumps(
            {"text": f"スキャン {req.metadata['page']} ページ目", "confidence": 0.93}, ensure_ascii=False
        )

    run.sampler = FakeSampler({"ocr": reply})
    env = run("pdf.ocr", {"path": "契約書.pdf", "pages": "4", "engine": "ocr"})
    assert env["data"]["pages"][0]["confidence"] == 0.93
    assert env["untrusted"] == [{"source": "ocr", "ref": "契約書.pdf#p.4", "text": "スキャン 4 ページ目"}]
    req = run.sampler.requests[0]
    assert req.purpose == "ocr" and req.metadata["engine"] == "ocr"
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(req.image_png or b""))
    assert img.size[0] == pytest.approx(595 * 200 / 72, abs=2)  # 200dpi


def test_ocr_plain_text_reply_and_no_sampler(run: Runner, pdf: Path) -> None:
    run.sampler = FakeSampler({"ocr": "ただのテキスト"})
    env = run("pdf.ocr", {"path": "契約書.pdf", "pages": "4"})
    assert env["data"]["pages"][0]["confidence"] is None
    run.sampler = None
    err = run.error("pdf.ocr", {"path": "契約書.pdf", "pages": "4"})
    assert err["code"] == "TOOL_FAILED"
