"""local-llm-agent のプロトコル（JSON Schema・ツール一覧・検証）。

正本は packages/protocol/schemas/src/*.yaml。data/ 以下は生成物。
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

PROTOCOL_VERSION = "1.0"

__all__ = [
    "PROTOCOL_VERSION",
    "ValidationError",
    "load_bundle",
    "load_tools",
    "tool",
    "validate",
    "validate_tool_args",
    "is_valid",
]


def _read_json(name: str) -> Any:
    return json.loads(resources.files("lla_protocol").joinpath("data", name).read_text(encoding="utf-8"))


@cache
def load_bundle() -> dict[str, Any]:
    """$defs をすべて含むプロトコルのスキーマ。"""
    bundle: dict[str, Any] = _read_json("protocol.schema.json")
    return bundle


@cache
def load_tools() -> dict[str, dict[str, Any]]:
    """ツール名 → ToolSpec（args_schema/result_schema は $ref 展開済み）。"""
    doc = _read_json("tools.json")
    return {t["name"]: t for t in doc["tools"]}


def tool(name: str) -> dict[str, Any]:
    try:
        return load_tools()[name]
    except KeyError:
        raise KeyError(f"unknown tool: {name}") from None


@cache
def _registry() -> Registry:
    bundle = load_bundle()
    return cast(Registry, Registry().with_resource(bundle["$id"], Resource.from_contents(bundle)))


@cache
def _validator(def_name: str) -> Draft202012Validator:
    bundle = load_bundle()
    if def_name not in bundle["$defs"]:
        raise KeyError(f"unknown definition: {def_name}")
    schema = {"$ref": f"{bundle['$id']}#/$defs/{def_name}"}
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def validate(def_name: str, instance: Any) -> None:
    """$defs の名前（例: "Action", "ToolResult"）で検証する。不正なら ValidationError。"""
    _validator(def_name).validate(instance)


def is_valid(def_name: str, instance: Any) -> bool:
    return bool(_validator(def_name).is_valid(instance))


@cache
def _tool_validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(tool(name)["args_schema"], format_checker=FormatChecker())


def validate_tool_args(name: str, args: Any) -> None:
    _tool_validator(name).validate(args)
