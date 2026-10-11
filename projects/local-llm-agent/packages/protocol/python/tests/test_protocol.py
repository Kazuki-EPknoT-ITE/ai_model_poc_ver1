"""試験仕様 S（Python 側）: 生成物の読み込み・検証・pydantic モデル。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import lla_protocol as p
from lla_protocol.generated import models

EXAMPLES = Path(__file__).resolve().parents[2] / "schemas" / "examples"


def test_catalog_has_47_tools() -> None:
    tools = p.load_tools()
    assert len(tools) == 47
    assert "batch.run" in tools
    assert p.load_bundle()["$id"].endswith("/1.0/protocol.json")


@pytest.mark.parametrize("name", sorted(p.load_tools()))
def test_tool_examples_validate(name: str) -> None:
    for example in p.tool(name)["examples"]:
        p.validate_tool_args(name, example)


def test_unknown_tool_raises() -> None:
    with pytest.raises(KeyError):
        p.tool("shell.exec")


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda x: x.name)
def test_definition_examples(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    for v in doc["valid"]:
        p.validate(doc["def"], v)
    for v in doc["invalid"]:
        assert not p.is_valid(doc["def"], v), v


def test_invalid_args_raise() -> None:
    with pytest.raises(p.ValidationError):
        p.validate_tool_args("web.fetch", {"url": "file:///etc/passwd"})


def test_generated_models_parse_action() -> None:
    final = models.Final.model_validate(
        {"type": "final", "summary": "完了", "outputs": [], "failures": [], "needs_review": []}
    )
    assert final.summary == "完了"
    err = models.ToolError.model_validate({"code": "TIMEOUT", "kind": "transient", "message": "x"})
    assert err.code.value == "TIMEOUT"
