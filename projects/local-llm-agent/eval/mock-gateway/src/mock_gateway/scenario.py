"""シナリオファイル（YAML）の読み込みと検証（local_llm_test_spec.md §2.1）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import lla_protocol

Purpose = Literal["agent", "invoice_extract", "compact"]
FaultKind = Literal[
    "http_status",
    "latency",
    "drop_connection",
    "hang",
    "queue",
    "raw",
    "restart",
    "dlp_block",
    "net_disabled",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Match(_Strict):
    """すべて省略可。指定した条件をすべて満たすと一致する。"""

    step_index: int | None = None  # 同じ purpose の中で何番目の新規ジョブか（0 始まり）
    last_user: str | None = None  # 利用者のメッセージ（ツール結果を除く）に対する正規表現
    last_tool_result_contains: str | None = None  # 直近のツール結果に含まれる文字列
    purpose: Purpose = "agent"

    @field_validator("last_user")
    @classmethod
    def _regex(cls, v: str | None) -> str | None:
        if v is not None:
            re.compile(v)
        return v


class Step(_Strict):
    match: Match = Field(default_factory=Match)
    stream: bool = True
    latency_ms: int = Field(default=50, ge=0)
    tokens_per_sec: float = Field(default=200.0, gt=0)
    action: dict[str, Any] | None = None
    action_from_fixture: str | None = None  # "{item_basename}" を置換して読む JSON
    raw: str | None = None  # スキーマに合わない出力をそのまま返す（AGT-05 の試験用）
    repeat: bool | int = False  # true: 何度でも使う / 整数: その回数まで使う

    @model_validator(mode="after")
    def _one_output(self) -> Step:
        outputs = [x for x in (self.action, self.action_from_fixture, self.raw) if x is not None]
        if len(outputs) != 1:
            raise ValueError("action / action_from_fixture / raw のどれか1つを指定してください")
        if self.action is not None:
            try:
                lla_protocol.validate("Action", self.action)
            except lla_protocol.ValidationError as e:
                raise ValueError(f"action がプロトコルの Action スキーマに合いません: {e.message}") from e
        return self


class FaultAt(_Strict):
    step_index: int | None = None
    after_ms: int | None = Field(default=None, ge=0)
    purpose: Purpose = "agent"


class Fault(_Strict):
    at: FaultAt = Field(default_factory=FaultAt)
    kind: FaultKind
    times: int = Field(default=1, ge=1)
    status: int | None = None  # http_status
    latency_ms: int | None = None  # latency
    tokens_per_sec: float | None = None  # latency
    after_events: int = Field(default=3, ge=1)  # drop_connection / restart
    positions: list[int] = Field(default_factory=lambda: [3, 2, 1])  # queue
    interval_ms: int = Field(default=200, ge=0)  # queue
    output: str | None = None  # raw

    @model_validator(mode="after")
    def _params(self) -> Fault:
        if self.kind == "http_status" and self.status not in (429, 500, 502, 503, 504):
            raise ValueError("http_status には status（429/500/502/503/504）が必要です")
        if self.kind == "raw" and self.output is None:
            raise ValueError("raw には output が必要です")
        return self


class User(_Strict):
    login: str
    password: str
    display_name: str = "開発ユーザー"
    role: Literal["user", "admin"] = "user"
    department: str = "dev"


class Scenario(_Strict):
    name: str
    protocol_version: str = lla_protocol.PROTOCOL_VERSION
    config: dict[str, Any] = Field(default_factory=dict)
    users: list[User] = Field(default_factory=lambda: [User(login="dev", password="dev")])  # noqa: S106
    steps: list[Step] = Field(default_factory=list)
    faults: list[Fault] = Field(default_factory=list)
    fixtures: dict[str, Any] = Field(default_factory=dict)  # rag / web_search / web_fetch / ocr の応答
    fallback: Step | None = None
    base_dir: Path = Path(".")

    @field_validator("protocol_version")
    @classmethod
    def _major(cls, v: str) -> str:
        if v.split(".")[0] != lla_protocol.PROTOCOL_VERSION.split(".")[0]:
            raise ValueError(
                f"protocol_version {v} はこのモックの {lla_protocol.PROTOCOL_VERSION} と合いません"
            )
        return v


def load_scenario(path: str | Path) -> Scenario:
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    data["base_dir"] = p.resolve().parent
    return Scenario.model_validate(data)
