"""ジョブの管理・シナリオとの照合・イベント列の生成。

イベント列はジョブ作成時に「作成からの経過時間（秒）付き」で確定させる。SSE は
Last-Event-ID の次から、予定時刻まで待って送る。こうすると再接続しても同じ列が
返り、試験の再現性が保てる（local_llm_api.md §4, local_llm_test_spec.md §2）。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import lla_protocol

from .scenario import Fault, Scenario, Step

TOOL_RESULT_MARK = "[tool_result"
HANG = float("inf")


@dataclass
class Event:
    seq: int
    at: float  # ジョブ作成からの秒
    kind: str  # queue | started | token | done | error | requeued
    data: dict[str, Any]


@dataclass
class Job:
    id: str
    key: str
    body_hash: str
    user: str
    model: str
    purpose: str
    index: int  # 同じ purpose での通し番号
    created: float
    events: list[Event]
    output: str
    usage: dict[str, int]
    drop_after: list[int] = field(default_factory=list)  # 接続ごとに、何件送ったら切るか
    connections: int = 0
    cancelled: bool = False

    def state(self, now: float) -> str:
        if self.cancelled:
            return "cancelled"
        elapsed = now - self.created
        started = next((e.at for e in self.events if e.kind == "started"), HANG)
        done = next((e.at for e in self.events if e.kind == "done"), HANG)
        if elapsed >= done:
            return "done"
        if elapsed >= started:
            return "running"
        return "queued"


class GatewayError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retryable: bool = False,
        retry_after_ms: int | None = None,
        detail: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms
        self.detail = detail

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.retry_after_ms is not None:
            err["retry_after_ms"] = self.retry_after_ms
        if self.detail is not None:
            err["detail"] = self.detail
        return {"error": err}


def body_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return int(chars / 1.5)


class Engine:
    def __init__(self, scenario: Scenario, *, result_ttl_s: float = 600.0, clock: Any = time.monotonic):
        self.sc = scenario
        self.clock = clock
        self.started_at = clock()
        self.result_ttl_s = result_ttl_s
        self.jobs: dict[str, Job] = {}
        self.by_key: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.step_uses: dict[int, int] = {}
        self.fault_left = [f.times for f in scenario.faults]

    # ---- 障害 ----
    def take_fault(self, kind: str, purpose: str = "agent", index: int | None = None) -> Fault | None:
        """条件に合う障害を1回分消費して返す。"""
        elapsed_ms = (self.clock() - self.started_at) * 1000
        for i, f in enumerate(self.sc.faults):
            if f.kind != kind or self.fault_left[i] <= 0 or f.at.purpose != purpose:
                continue
            if f.at.step_index is not None and f.at.step_index != index:
                continue
            if f.at.after_ms is not None and elapsed_ms < f.at.after_ms:
                continue
            self.fault_left[i] -= 1
            return f
        return None

    # ---- 照合 ----
    def _select_step(self, purpose: str, index: int, messages: list[dict[str, Any]]) -> Step:
        user_texts = [
            str(m.get("content", ""))
            for m in reversed(messages)
            if m.get("role") == "user" and not str(m.get("content", "")).lstrip().startswith(TOOL_RESULT_MARK)
        ]
        last_tool = next(
            (
                str(m.get("content", ""))
                for m in reversed(messages)
                if TOOL_RESULT_MARK in str(m.get("content", ""))
            ),
            None,
        )
        for i, step in enumerate(self.sc.steps):
            m = step.match
            if m.purpose != purpose:
                continue
            used = self.step_uses.get(i, 0)
            limit = (
                step.repeat if isinstance(step.repeat, int) and not isinstance(step.repeat, bool) else None
            )
            if step.repeat is False and used >= 1:
                continue
            if limit is not None and used >= limit:
                continue
            if m.step_index is not None and m.step_index != index:
                continue
            if m.last_user is not None and not any(re.search(m.last_user, t) for t in user_texts):
                continue
            if m.last_tool_result_contains is not None and (
                last_tool is None or m.last_tool_result_contains not in last_tool
            ):
                continue
            self.step_uses[i] = used + 1
            return step
        if self.sc.fallback is not None:
            return self.sc.fallback
        raise GatewayError(
            500, "MOCK_NO_STEP", f"シナリオに一致するステップがありません（purpose={purpose}, index={index}）"
        )

    def _output_for(self, step: Step, messages: list[dict[str, Any]]) -> str:
        if step.raw is not None:
            return step.raw
        if step.action is not None:
            return json.dumps(step.action, ensure_ascii=False)
        assert step.action_from_fixture is not None
        last = str(messages[-1].get("content", "")) if messages else ""
        ref = re.search(r'ref="([^"]+)"', last)
        basename = Path(ref.group(1).split("#")[0]).stem if ref else "default"
        path = (self.sc.base_dir / step.action_from_fixture.replace("{item_basename}", basename)).resolve()
        if not path.is_relative_to(self.sc.base_dir.resolve()):
            raise GatewayError(500, "MOCK_BAD_FIXTURE", "fixture のパスがシナリオの外を指しています")
        if not path.exists():
            raise GatewayError(500, "MOCK_NO_FIXTURE", f"fixture がありません: {path.name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if step.match.purpose == "agent":
            lla_protocol.validate("Action", data)
        return json.dumps(data, ensure_ascii=False)

    # ---- ジョブ ----
    def submit(self, *, key: str, user: str, body: dict[str, Any]) -> tuple[Job, bool]:
        """(job, created)。同じキー・同じ中身なら既存のジョブを返す。"""
        self.expire()
        h = body_hash(body)
        if key in self.by_key and self.by_key[key] in self.jobs:
            job = self.jobs[self.by_key[key]]
            if job.body_hash != h:
                raise GatewayError(409, "IDEMPOTENCY_CONFLICT", "同じ Idempotency-Key で中身の異なる依頼です")
            return job, False

        purpose = str(body.get("purpose", "agent"))
        index = self.counters.get(purpose, 0)
        fault = self.take_fault("http_status", purpose, index)
        if fault is not None:
            assert fault.status is not None
            code = "QUEUE_LIMIT" if fault.status == 429 else "ENGINE_UNAVAILABLE"
            raise GatewayError(
                fault.status, code, "（モック）注入した HTTP エラー", retryable=True, retry_after_ms=500
            )

        messages = list(body.get("messages") or [])
        model = str(body.get("model", "local-12b"))
        models = {m["id"]: m for m in self.config()["models"]}
        if model not in models or not models[model]["available"]:
            raise GatewayError(400, "INVALID_REQUEST", f"モデル {model} は使えません")
        if estimate_tokens(messages) > int(models[model]["context_budget"]):
            raise GatewayError(
                413, "CONTEXT_TOO_LARGE", "文脈予算を超えています。圧縮してから再投入してください"
            )

        step = self._select_step(purpose, index, messages)
        self.counters[purpose] = index + 1
        output = self._output_for(step, messages)
        raw_fault = self.take_fault("raw", purpose, index)
        if raw_fault is not None:
            assert raw_fault.output is not None
            output = raw_fault.output

        job = Job(
            id=uuid.uuid4().hex,
            key=key,
            body_hash=h,
            user=user,
            model=model,
            purpose=purpose,
            index=index,
            created=self.clock(),
            events=[],
            output=output,
            usage={"input_tokens": estimate_tokens(messages), "output_tokens": max(1, len(output) // 3)},
        )
        job.events = self._timeline(job, step, purpose, index, bool(body.get("stream", True)))
        drop = self.take_fault("drop_connection", purpose, index)
        if drop is not None:
            job.drop_after = [drop.after_events] * drop.times
        self.jobs[job.id] = job
        self.by_key[key] = job.id
        return job, True

    def _timeline(self, job: Job, step: Step, purpose: str, index: int, stream: bool) -> list[Event]:
        events: list[Event] = []
        t = 0.0

        def add(kind: str, data: dict[str, Any]) -> None:
            events.append(Event(seq=len(events) + 1, at=t, kind=kind, data=data))

        q = self.take_fault("queue", purpose, index)
        if q is not None:
            for pos in q.positions:
                add("queue", {"position": pos, "eta_seconds": pos * 2})
                t += q.interval_ms / 1000
        else:
            add("queue", {"position": 0, "eta_seconds": 0})

        if self.take_fault("hang", purpose, index) is not None:
            add("started", {"model": job.model})
            events[-1].at = HANG
            return events

        lat = self.take_fault("latency", purpose, index)
        latency_ms = lat.latency_ms if lat and lat.latency_ms is not None else step.latency_ms
        tps = lat.tokens_per_sec if lat and lat.tokens_per_sec is not None else step.tokens_per_sec
        restart = self.take_fault("restart", purpose, index)

        add("started", {"model": job.model})
        t += latency_ms / 1000
        chunks = _chunks(job.output, 12) if stream else []
        if restart is not None:
            for c in chunks[: restart.after_events]:
                add("token", {"delta": c})
                t += len(c) / 3 / tps
            add("requeued", {"reason": "engine_restart"})
            t += 0.05
            add("started", {"model": job.model})
        for c in chunks:
            add("token", {"delta": c})
            t += len(c) / 3 / tps
        add("done", {"output": job.output, "usage": job.usage, "finish_reason": "stop"})
        return events

    def get(self, job_id: str) -> Job:
        self.expire()
        job = self.jobs.get(job_id)
        if job is None:
            raise GatewayError(404, "NOT_FOUND", "ジョブがありません（保持期限切れを含む）")
        return job

    def cancel(self, job_id: str) -> None:
        self.get(job_id).cancelled = True

    def expire(self) -> None:
        now = self.clock()
        for jid, job in list(self.jobs.items()):
            done_at = next((e.at for e in job.events if e.kind == "done"), HANG)
            if now - job.created - done_at > self.result_ttl_s:
                del self.jobs[jid]
                self.by_key.pop(job.key, None)

    # ---- /v1/config ----
    def config(self) -> dict[str, Any]:
        c = self.sc.config
        budget = int(c.get("context_budget", 32768))
        available = set(c.get("models", ["local-12b"]))
        return {
            "protocol_version": self.sc.protocol_version,
            "server_version": "mock-0.1.0",
            "org": c.get("org", {"id": "dev", "name": "開発用"}),
            "models": [
                {
                    "id": "local-12b",
                    "label": "高速",
                    "available": "local-12b" in available,
                    "context_budget": budget,
                },
                {
                    "id": "local-26b",
                    "label": "高精度",
                    "available": "local-26b" in available,
                    "context_budget": int(c.get("context_budget_26b", 65536)),
                },
            ],
            "escalation_target": c.get(
                "escalation_target", "local-26b" if "local-26b" in available else None
            ),
            "net": {
                "level_cap": c.get("net_level_cap", "off"),
                "monthly_search_limit": c.get("monthly_search_limit", 300),
                "used_this_month": c.get("used_this_month", 0),
            },
            "instructions": c.get("instructions", {"version": 1, "body": ""}),
            "invoice_schemas": c.get("invoice_schemas", ["invoice.v1"]),
            "limits": {"max_concurrent": c.get("max_concurrent", 2), "max_tool_result_tokens": 6000},
            "features": c.get("features", {"rag": False, "ocr": True, "web": True}),
            "macros": c.get(
                "macros",
                [
                    {
                        "name": "請求書取込",
                        "description": "請求書フォルダの PDF を読み、送金明細の Excel にまとめる",
                        "prompt": "請求書フォルダのPDFを読んで、送金明細をExcelにまとめて",
                    }
                ],
            ),
        }


def _chunks(s: str, n: int) -> list[str]:
    return [s[i : i + n] for i in range(0, len(s), n)] or [""]
