"""封筒・エラー分類・continuation・tools/list の注釈（T-10）。"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from office_fixtures import make_docx
from office_testkit import Runner

import lla_protocol
from office_tools.envelope import (
    TOKEN_BUDGET,
    ContinuationStore,
    Item,
    Outcome,
    dumps,
    failure,
    paginate,
    resume,
    success,
)
from office_tools.errors import ERROR_KINDS, ToolFailure
from office_tools.fsutil import atomic_write, resolve
from office_tools.registry import NAMESPACES, office_specs
from office_tools.server import list_tool_defs


def test_success_envelope_tokens_estimate() -> None:
    env = success(Outcome(data={"x": "あ" * 300}, warnings=["w"]))
    lla_protocol.validate("ToolResult", env)
    assert env["tokens_estimate"] == math.ceil(len(dumps({**env, "tokens_estimate": 0})) / 1.5)


def test_failure_envelope_all_codes_are_protocol_codes() -> None:
    for code, kind in ERROR_KINDS.items():
        env = failure(ToolFailure(code, "テスト"))
        lla_protocol.validate("ToolResult", env)
        assert env["error"]["kind"] == kind
        if kind == "transient" and code in ("FILE_LOCKED", "TIMEOUT"):
            assert env["error"]["retry_after_ms"] > 0


def test_unknown_error_code_rejected() -> None:
    with pytest.raises(ValueError):
        ToolFailure("NOPE", "x")


def test_unknown_tool(run: Runner) -> None:
    err = run.error("fs.list", {"path": "."})
    assert err["code"] == "UNKNOWN_TOOL"
    err = run.error("docx.nothing", {})
    assert err["code"] == "UNKNOWN_TOOL"


def test_invalid_args_are_reported_before_processing(run: Runner) -> None:
    err = run.error("xlsx.read_range", {"path": "a.xlsx"})
    assert err["code"] == "INVALID_ARGS" and err["kind"] == "permanent"
    err = run.error(
        "docx.edit", {"path": "a.docx", "ops": [{"op": "replace_text", "block_id": "x:1", "text": "a"}]}
    )
    assert err["code"] == "INVALID_ARGS"


def test_not_found(run: Runner) -> None:
    err = run.error("docx.read", {"path": "無い.docx"})
    assert err["code"] == "NOT_FOUND"
    assert "fs.list" in err["message"]


def test_paginate_and_resume() -> None:
    store = ContinuationStore()
    items = [
        Item({"rows": ["x" * 100, i]}, [{"source": "file", "ref": f"r{i}", "text": "y" * 200}])
        for i in range(300)
    ]
    first = paginate(store, "csv.read", {"total": 300}, ("rows",), items, max_items=1000)
    assert first.continuation and first.data["remaining"] > 0
    got = len(first.data["rows"])
    env = success(first)
    assert env["tokens_estimate"] <= TOKEN_BUDGET * 1.1
    token = first.continuation
    while token:
        page = resume(store, "csv.read", token)
        got += len(page.data["rows"])
        assert success(page)["tokens_estimate"] <= TOKEN_BUDGET * 1.1
        token = page.continuation
    assert got == 300
    with pytest.raises(ToolFailure) as e:
        resume(store, "csv.read", first.continuation)
    assert e.value.code == "INVALID_ARGS"


def test_continuation_for_other_tool_rejected() -> None:
    store = ContinuationStore()
    items = [Item({"rows": ["x" * 3000]}) for _ in range(10)]
    out = paginate(store, "csv.read", {}, ("rows",), items, max_items=100)
    assert out.continuation
    with pytest.raises(ToolFailure):
        resume(store, "docx.read", out.continuation)


def test_single_item_too_large() -> None:
    store = ContinuationStore()
    with pytest.raises(ToolFailure) as e:
        paginate(store, "csv.read", {}, ("rows",), [Item({"rows": ["x" * 100_000]})], max_items=10)
    assert e.value.code == "RESULT_TOO_LARGE"


def test_continuation_via_tool_call(run: Runner, ws: Path) -> None:
    import docx

    d = docx.Document()
    for i in range(400):
        d.add_paragraph(f"第{i}項 " + "本文" * 20)
    d.save(str(ws / "big.docx"))
    env = run("docx.read", {"path": "big.docx", "max_blocks": 500})
    seen = [b["id"] for b in env["data"]["blocks"]]
    assert env["continuation"]
    while "continuation" in env:
        env = run("docx.read", {"continuation": env["continuation"]})
        seen += [b["id"] for b in env["data"]["blocks"]]
    assert seen == [f"p:{i}" for i in range(1, 401)]
    err = run.error("docx.read", {"continuation": "c-expired"})
    assert err["code"] == "INVALID_ARGS"


def test_store_capacity() -> None:
    store = ContinuationStore(capacity=2)
    items = [Item({"rows": ["x" * 3000]}) for _ in range(10)]
    tokens = [paginate(store, "csv.read", {}, ("rows",), items, max_items=100).continuation for _ in range(3)]
    assert len(store) == 2
    with pytest.raises(ToolFailure):
        resume(store, "csv.read", str(tokens[0]))


def test_atomic_write_file_locked(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    target = resolve("locked.txt")
    target.write_text("old", encoding="utf-8")

    def deny(src: object, dst: object) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr(os, "replace", deny)
    with pytest.raises(ToolFailure) as e:
        atomic_write("locked.txt", target, lambda tmp: tmp.write_text("new", encoding="utf-8"))
    assert e.value.code == "FILE_LOCKED" and e.value.kind == "transient"
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in ws.iterdir()] == ["locked.txt"]  # 一時ファイルは残らない


def test_relative_paths_use_workspace(ws: Path) -> None:
    assert resolve("a/b.docx") == ws / "a" / "b.docx"
    assert resolve("a\\b.docx") == ws / "a" / "b.docx"


def test_tools_list_matches_catalog() -> None:
    specs = {s["name"]: s for s in office_specs()}
    tools = list_tool_defs()
    assert len(tools) == 32
    all_tools = lla_protocol.load_tools()
    expected = {n for n in all_tools if n.split(".")[0] in NAMESPACES}
    assert {t.name for t in tools} == expected
    for t in tools:
        spec = specs[t.name]
        assert t.inputSchema == spec["args_schema"]
        ann = t.annotations.model_dump(by_alias=True) if t.annotations else {}
        assert ann["x-agent-effect"] == spec["effect"]
        assert ann["x-agent-path-args"] == spec["path_args"]
        assert ann["x-agent-idempotent"] == spec["idempotent"]
        assert ann["x-agent-untrusted-output"] == spec["untrusted_output"]
        assert ann["x-agent-paginated"] == bool(spec.get("paginated", False))
        assert ann["readOnlyHint"] == (spec["effect"] == "read")


def test_paginated_tools_accept_continuation_only() -> None:
    for spec in office_specs():
        if spec.get("paginated"):
            lla_protocol.validate_tool_args(spec["name"], {"continuation": "c-x"})


def test_docx_fixture_builds(ws: Path) -> None:
    assert make_docx(ws / "f.docx").exists()
