"""推論ジョブ: 永続キュー・公平スケジューリング・暗号化ペイロード・SSE（RUN-09/10, SEC-06, ADR-007）。

- 状態は jobs テーブルが正本。依頼本文・応答・イベントの中身は AES-256-GCM で暗号化して置く。
- 1つのジョブの状態遷移とイベントの追記は、そのジョブの鎖（asyncio.Lock）の中で行う。
  イベントの seq が書き込み順と一致し、取り消しと完了の競合も起きない。
- スケジューラは1プロセスに1つ（複数プロセスで同じ DB を使う構成には対応しない）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import deque
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Principal
from .config import ModelSettings, Settings
from .crypto import CryptoError, PayloadCipher
from .db import Database, Job, JobEvent, JobPayload, User
from .engines import Delta, EngineRequest, EngineRequestError, EngineUnavailable, Final, InferenceEngine
from .metrics import Metrics
from .usage import UsageRecorder
from .util import ApiError, Clock, body_hash, estimate_tokens, new_ulid

log = logging.getLogger("llm_gateway.jobs")

PRIORITY = {"interactive": 0, "normal": 1, "low": 2}
LOW = 2
TERMINAL = ("done", "failed", "cancelled")


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ResponseFormat(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["json_schema", "json_object", "text"]
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")


class StepRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str
    messages: list[Message] = Field(min_length=1)
    response_format: ResponseFormat | None = None
    max_tokens: int = Field(default=2048, ge=1, le=65536)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    stream: bool = True
    priority_hint: Literal["interactive", "normal", "low"] | None = None
    purpose: Literal["agent", "invoice_extract", "compact"] = "agent"


@dataclass
class QueuedRow:
    id: str
    user_id: str
    model: str
    priority: int
    enqueued_at: Any


def simulate_order(rows: list[QueuedRow], rr_last: dict[str, int]) -> list[QueuedRow]:
    """スケジューラが取り出す順番（上限は無視）。利用者間はラウンドロビン、low は最後。"""
    order: list[QueuedRow] = []
    rr = dict(rr_last)
    tick = max(rr.values(), default=0)
    for low in (False, True):
        per_user: dict[str, deque[QueuedRow]] = {}
        for r in sorted(rows, key=lambda r: (r.priority, r.enqueued_at, r.id)):
            if (r.priority >= LOW) == low:
                per_user.setdefault(r.user_id, deque()).append(r)
        while per_user:
            user = min(per_user, key=lambda u: (rr.get(u, 0), per_user[u][0].enqueued_at, per_user[u][0].id))
            order.append(per_user[user].popleft())
            tick += 1
            rr[user] = tick
            if not per_user[user]:
                del per_user[user]
    return order


async def _shielded[T](coro: Coroutine[Any, Any, T]) -> T:
    """DB の書き込みを取り消し（停止・DELETE）から守る。途中で止めると接続がプールに戻らないため。"""
    return await asyncio.shield(coro)


class _Hub:
    """ジョブごとの「新しいイベントがある」通知。"""

    def __init__(self) -> None:
        self._ev: dict[str, asyncio.Event] = {}

    def watch(self, job_id: str) -> asyncio.Event:
        return self._ev.setdefault(job_id, asyncio.Event())

    def notify(self, job_id: str) -> None:
        ev = self._ev.pop(job_id, None)
        if ev is not None:
            ev.set()


class JobService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        cipher: PayloadCipher,
        engines: dict[str, InferenceEngine],
        clock: Clock,
        metrics: Metrics,
        usage: UsageRecorder,
    ) -> None:
        self.settings = settings
        self.db = db
        self.cipher = cipher
        self.engines = engines
        self.models: dict[str, ModelSettings] = {m.id: m for m in settings.models}
        self.clock = clock
        self.metrics = metrics
        self.usage = usage
        self.hub = _Hub()
        self._locks: dict[str, asyncio.Lock] = {}
        self._seq: dict[str, int] = {}
        self._tasks: dict[str, tuple[asyncio.Task[None], str, str]] = {}  # job_id → (task, user, model)
        self._rr_last: dict[str, int] = {}
        self._rr_tick = 0
        self._last_pos: dict[str, int] = {}
        self._durations: deque[float] = deque(maxlen=settings.scheduler.eta_window)
        self._down: dict[str, float] = {}  # model → 次にヘルスチェックする時刻（loop.time）
        self._wake = asyncio.Event()
        self._paused = False
        self._stopping = False
        self._loops: list[asyncio.Task[None]] = []
        self.queue_depth: dict[str, int] = {}

    # ------------------------------------------------------------------ 起動・停止

    async def start(self, *, background: bool = True) -> None:
        await self.recover()
        if background:
            self._loops = [
                asyncio.create_task(self._dispatch_loop(), name="llmgw-dispatch"),
                asyncio.create_task(self._cleanup_loop(), name="llmgw-cleanup"),
            ]

    async def stop(self) -> None:
        """プロセスの停止。実行中のジョブは running のまま残り、次の起動で queued に戻る（RUN-09）。"""
        self._stopping = True
        self._wake.set()
        tasks = [t for t, _, _ in self._tasks.values()] + self._loops
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._loops.clear()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    async def recover(self) -> None:
        """起動時: running を queued に戻し（順序は enqueued_at のまま）、requeued を記録する。"""
        async with self.db.tx() as s:
            ids = list((await s.execute(select(Job.id).where(Job.state == "running"))).scalars())
            rows = (
                await s.execute(
                    select(Job.started_at, Job.finished_at)
                    .where(Job.state == "done", Job.started_at.is_not(None), Job.finished_at.is_not(None))
                    .order_by(Job.finished_at.desc())
                    .limit(self.settings.scheduler.eta_window)
                )
            ).all()
        for st, fin in reversed(rows):
            if st is not None and fin is not None:
                self._durations.append((fin - st).total_seconds())
        for jid in ids:
            async with self._lock(jid), self.db.tx() as s:
                job = await s.get(Job, jid)
                if job is None or job.state != "running":
                    continue
                job.state = "queued"
                job.started_at = None
                await self._append(s, jid, "requeued", {"reason": "gateway_restart"})
            self.hub.notify(jid)
        if ids:
            log.info("recovered %d running jobs to queued", len(ids))
            self.metrics.inc(
                "llmgw_requeues_total", "running から queued に戻した回数", len(ids), reason="restart"
            )

    # ------------------------------------------------------------------ 共通

    def _lock(self, job_id: str) -> asyncio.Lock:
        lk = self._locks.get(job_id)
        if lk is None:
            lk = self._locks[job_id] = asyncio.Lock()
        return lk

    async def _append(self, s: AsyncSession, job_id: str, kind: str, data: dict[str, Any]) -> int:
        """イベントを1件追記する（呼び出し側がジョブの鎖を持っていること）。"""
        if job_id not in self._seq:
            mx = (await s.execute(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id))).scalar()
            self._seq[job_id] = int(mx or 0)
        self._seq[job_id] += 1
        seq = self._seq[job_id]
        raw = json.dumps(data, ensure_ascii=False).encode()
        s.add(
            JobEvent(job_id=job_id, seq=seq, kind=kind, data_ct=self.cipher.encrypt(job_id, f"ev:{seq}", raw))
        )
        return seq

    def _expired(self, job: Job) -> bool:
        return job.result_expires_at is not None and job.result_expires_at <= self.clock.now()

    async def _owned(self, s: AsyncSession, p: Principal, job_id: str) -> Job:
        job = await s.get(Job, job_id)
        if job is None or job.user_id != p.user_id or self._expired(job):
            raise ApiError(404, "NOT_FOUND", "ジョブがありません（保持期限切れを含む）")
        return job

    def _eta(self, position: int) -> int:
        avg = (
            sum(self._durations) / len(self._durations)
            if self._durations
            else self.settings.scheduler.default_job_seconds
        )
        return math.ceil(avg * position / max(1, self.settings.scheduler.max_concurrent))

    # ------------------------------------------------------------------ 投入

    async def submit(
        self, p: Principal, key: str, raw_body: dict[str, Any], req: StepRequest
    ) -> tuple[dict[str, Any], bool]:
        if not key or len(key) > 512:
            raise ApiError(400, "INVALID_REQUEST", "Idempotency-Key ヘッダーが必要です")
        h = body_hash(raw_body)
        sch = self.settings.scheduler
        now = self.clock.now()

        model = self.models.get(req.model)
        if model is None:
            raise ApiError(400, "INVALID_REQUEST", f"モデル {req.model} は使えません")
        if req.response_format is not None and req.response_format.schema_ is not None:
            size = len(json.dumps(req.response_format.schema_, ensure_ascii=False).encode())
            if size > self.settings.jobs.max_schema_bytes:
                raise ApiError(
                    400, "INVALID_REQUEST", "response_format のスキーマが大きすぎます（64KB まで）"
                )
        messages = [m.model_dump() for m in req.messages]
        input_tokens = estimate_tokens(messages, self.settings.jobs.chars_per_token)

        async with self.db.tx() as s:
            existing = (await s.execute(select(Job).where(Job.idempotency_key == key))).scalar_one_or_none()
            if existing is not None:
                if existing.user_id != p.user_id:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT", "同じ Idempotency-Key で中身の異なる依頼です")
                if not self._expired(existing):
                    if existing.body_hash != h:
                        raise ApiError(
                            409, "IDEMPOTENCY_CONFLICT", "同じ Idempotency-Key で中身の異なる依頼です"
                        )
                    pos = self._last_pos.get(existing.id, 0) if existing.state == "queued" else 0
                    return {
                        "job_id": existing.id,
                        "state": existing.state,
                        "position": pos,
                        "eta_seconds": self._eta(pos),
                    }, False
                # 保持期限切れ: キーを空けて新しいジョブにする（モックと同じ動き）
                existing.idempotency_key = f"{key}#expired:{existing.id}"
                await s.flush()

            if input_tokens > model.context_budget:
                raise ApiError(
                    413, "CONTEXT_TOO_LARGE", "文脈予算を超えています。圧縮してから再投入してください"
                )
            queued_mine = (
                await s.execute(
                    select(func.count()).where(Job.user_id == p.user_id, Job.state.in_(("queued", "running")))
                )
            ).scalar_one()
            if queued_mine >= sch.max_queued_per_user:
                raise ApiError(
                    429, "QUEUE_LIMIT", "待ち件数の上限に達しています", retryable=True, retry_after_ms=3000
                )

            hint = req.priority_hint or (
                "interactive" if input_tokens <= sch.interactive_max_input_tokens else "normal"
            )
            priority = PRIORITY[hint]
            if priority == 0 and input_tokens > sch.interactive_max_input_tokens:
                priority = 1  # 長い依頼は interactive として扱わない（短い依頼を待たせない。RUN-10）

            job_id = new_ulid()
            job = Job(
                id=job_id,
                idempotency_key=key,
                body_hash=h,
                user_id=p.user_id,
                model=model.id,
                lora=model.lora,
                purpose=req.purpose,
                priority=priority,
                input_tokens=input_tokens,
                state="queued",
                enqueued_at=now,
                requeue_count=0,
            )
            s.add(job)
            payload = {
                "messages": messages,
                "response_format": req.response_format.model_dump(by_alias=True, exclude_none=True)
                if req.response_format
                else None,
                "max_tokens": req.max_tokens,
                "temperature": req.temperature,
                "stream": req.stream,
            }
            sealed = self.cipher.seal(job_id, "request", json.dumps(payload, ensure_ascii=False).encode())
            s.add(JobPayload(job_id=job_id, key_id=sealed.key_id, nonce=sealed.nonce, request_ct=sealed.blob))
            await s.flush()
            order = simulate_order(await self._queued_rows(s), self._rr_last)
            position = next((i for i, r in enumerate(order) if r.id == job_id), 0)
            eta = self._eta(position)
            self._seq[job_id] = 0
            await self._append(s, job_id, "queue", {"position": position, "eta_seconds": eta})
        self._last_pos[job_id] = position
        self.metrics.inc(
            "llmgw_jobs_submitted_total", "投入されたジョブ数", model=model.id, purpose=req.purpose
        )
        log.info(
            "job queued job_id=%s user_id=%s model=%s priority=%d input_tokens=%d",
            job_id,
            p.user_id,
            model.id,
            priority,
            input_tokens,
        )
        self.wake()
        return {"job_id": job_id, "state": "queued", "position": position, "eta_seconds": eta}, True

    async def _queued_rows(self, s: AsyncSession) -> list[QueuedRow]:
        rows = (
            await s.execute(
                select(Job.id, Job.user_id, Job.model, Job.priority, Job.enqueued_at)
                .where(Job.state == "queued")
                .order_by(Job.priority, Job.enqueued_at, Job.id)
                .limit(2000)
            )
        ).all()
        return [QueuedRow(r[0], r[1], r[2], int(r[3]), r[4]) for r in rows]

    # ------------------------------------------------------------------ 参照・取り消し

    async def get(self, p: Principal, job_id: str) -> dict[str, Any]:
        async with self.db.tx() as s:
            job = await self._owned(s, p, job_id)
            out: dict[str, Any] = {"job_id": job.id, "state": job.state}
            if job.state == "queued":
                out["position"] = self._last_pos.get(job.id, 0)
            elif job.state == "done":
                payload = await s.get(JobPayload, job.id)
                if payload is not None and payload.response_ct is not None:
                    resp = json.loads(self.cipher.decrypt(job.id, "response", payload.response_ct))
                    out["output"] = resp["output"]
                    out["usage"] = resp["usage"]
            elif job.error is not None:
                out["error"] = {
                    "code": job.error,
                    "retryable": job.error in ("ENGINE_UNAVAILABLE", "PAYLOAD_LOST"),
                }
            return out

    async def cancel(self, p: Principal, job_id: str) -> None:
        async with self.db.tx() as s:
            await self._owned(s, p, job_id)
        async with self._lock(job_id):
            async with self.db.tx() as s:
                job = await s.get(Job, job_id)
                if job is None or job.state in TERMINAL:
                    return
                await self._terminate(s, job, "cancelled", "CANCELLED", retryable=False)
        self.hub.notify(job_id)
        entry = self._tasks.get(job_id)
        if entry is not None:
            entry[0].cancel()  # 実行中ならエンジンへの依頼を切る（HTTP の切断で vLLM が中止する）
        self.metrics.inc("llmgw_jobs_total", "終了したジョブ数", state="cancelled")
        log.info("job cancelled job_id=%s", job_id)
        self.wake()

    async def _terminate(self, s: AsyncSession, job: Job, state: str, code: str, *, retryable: bool) -> None:
        now = self.clock.now()
        job.state = state
        job.error = code
        job.finished_at = now
        job.result_expires_at = now + timedelta(seconds=self.settings.jobs.result_ttl_s)
        await s.execute(update(JobPayload).where(JobPayload.job_id == job.id).values(request_ct=None))
        await self._append(s, job.id, "error", {"code": code, "retryable": retryable})
        self._last_pos.pop(job.id, None)

    # ------------------------------------------------------------------ SSE

    async def open_events(self, p: Principal, job_id: str) -> None:
        """SSE を始める前の確認（404 はストリームの前に返す）。"""
        async with self.db.tx() as s:
            await self._owned(s, p, job_id)

    async def _read_events(self, job_id: str, last: int) -> list[tuple[int, str, bytes | None]] | None:
        async with self.db.tx() as s:
            job = await s.get(Job, job_id)
            if job is None or self._expired(job):
                return None
            rows = (
                await s.execute(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.seq > last)
                    .order_by(JobEvent.seq)
                )
            ).scalars()
            return [(r.seq, r.kind, r.data_ct) for r in rows]

    async def events(self, job_id: str, last: int) -> AsyncIterator[bytes]:
        ping = self.settings.jobs.ping_interval_s
        while True:
            signal = self.hub.watch(job_id)
            # 切断（取り消し）で DB の操作が途中で止まると接続が戻らないため、読み出しは shield する
            batch = await asyncio.shield(self._read_events(job_id, last))
            if batch is None:
                return
            for seq, kind, ct in batch:
                data = self.cipher.decrypt(job_id, f"ev:{seq}", ct) if ct is not None else b"{}"
                yield f"id: {seq}\nevent: {kind}\ndata: ".encode() + data + b"\n\n"
                last = seq
                if kind in ("done", "error"):
                    return
            try:
                await asyncio.wait_for(signal.wait(), ping)
            except TimeoutError:
                yield b": ping\n\n"

    # ------------------------------------------------------------------ スケジューラ

    async def _dispatch_loop(self) -> None:
        tick = self.settings.scheduler.tick_s
        while not self._stopping:
            try:
                await asyncio.wait_for(self._wake.wait(), tick)
            except TimeoutError:
                pass
            self._wake.clear()
            if self._stopping:
                return
            try:
                if not self._paused:
                    await self.dispatch_round()
                await self.refresh_positions()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("dispatch failed")

    async def _model_ready(self, model: str) -> bool:
        until = self._down.get(model)
        if until is None:
            return True
        loop = asyncio.get_running_loop()
        if loop.time() < until:
            return False
        if await self.engines[model].health():
            self._down.pop(model, None)
            log.info("engine back model=%s", model)
            return True
        self._down[model] = loop.time() + self.settings.scheduler.engine_retry_s
        return False

    async def dispatch_round(self) -> int:
        """空きがある限りジョブを取り出して実行する。取り出した件数を返す。"""
        sch = self.settings.scheduler
        started = 0
        while len(self._tasks) < sch.max_concurrent and not self._stopping and not self._paused:
            async with self.db.tx() as s:
                rows = [r for r in await self._queued_rows(s) if r.id not in self._tasks]
                users = {r.user_id for r in rows}
                caps: dict[str, int] = {}
                if users:
                    q = select(User.id, User.max_concurrent).where(User.id.in_(users))
                    caps = {uid: int(mc) for uid, mc in (await s.execute(q)).all()}
            self.queue_depth = {}
            for r in rows:
                self.queue_depth[r.model] = self.queue_depth.get(r.model, 0) + 1
            if not rows:
                return started
            running_by_user: dict[str, int] = {}
            for _, u, _ in self._tasks.values():
                running_by_user[u] = running_by_user.get(u, 0) + 1
            ready = {m: await self._model_ready(m) for m in {r.model for r in rows}}
            eligible = [
                r
                for r in rows
                if ready.get(r.model, False) and running_by_user.get(r.user_id, 0) < caps.get(r.user_id, 0)
            ]
            pool = [r for r in eligible if r.priority < LOW]
            if not pool and not any(r.priority < LOW for r in rows):
                pool = [r for r in eligible if r.priority >= LOW]  # low はほかに待ちがないときだけ
            if not pool:
                return started
            heads: dict[str, QueuedRow] = {}
            for r in pool:  # rows は (priority, enqueued_at, id) 順
                heads.setdefault(r.user_id, r)
            user = min(heads, key=lambda u: (self._rr_last.get(u, 0), heads[u].enqueued_at, heads[u].id))
            if self._paused:
                return started
            if await self._start(heads[user]):
                started += 1
                self._rr_tick += 1
                self._rr_last[user] = self._rr_tick
        return started

    async def _start(self, row: QueuedRow) -> bool:
        async with self._lock(row.id):
            async with self.db.tx() as s:
                job = await s.get(Job, row.id)
                if job is None or job.state != "queued":
                    return False
                job.state = "running"
                job.started_at = self.clock.now()
                await self._append(s, row.id, "started", {"model": row.model})
            self._last_pos.pop(row.id, None)
            task = asyncio.create_task(self._run(row.id, row.model), name=f"llmgw-job-{row.id}")
            self._tasks[row.id] = (task, row.user_id, row.model)
        self.hub.notify(row.id)
        log.info("job started job_id=%s model=%s", row.id, row.model)
        return True

    async def refresh_positions(self) -> None:
        async with self.db.tx() as s:
            order = simulate_order(await self._queued_rows(s), self._rr_last)
        for pos, r in enumerate(order):
            if self._last_pos.get(r.id) == pos:
                continue
            async with self._lock(r.id):
                async with self.db.tx() as s:
                    job = await s.get(Job, r.id)
                    if job is None or job.state != "queued":
                        continue
                    await self._append(s, r.id, "queue", {"position": pos, "eta_seconds": self._eta(pos)})
                self._last_pos[r.id] = pos
            self.hub.notify(r.id)

    # ------------------------------------------------------------------ 実行

    async def _load_request(self, job_id: str, model: str) -> EngineRequest | None:
        async with self.db.tx() as s:
            job = await s.get(Job, job_id)
            payload = await s.get(JobPayload, job_id)
        if job is None or payload is None or payload.request_ct is None:
            return None
        body = json.loads(self.cipher.decrypt(job_id, "request", payload.request_ct))
        return EngineRequest(
            job_id=job_id,
            model=model,
            messages=body["messages"],
            response_format=body.get("response_format"),
            max_tokens=int(body["max_tokens"]),
            temperature=float(body["temperature"]),
            purpose=job.purpose,
            stream=bool(body.get("stream", True)),
        )

    async def _run(self, job_id: str, model: str) -> None:
        flush_s = self.settings.jobs.token_flush_ms / 1000
        loop = asyncio.get_running_loop()
        try:
            try:
                req = await _shielded(self._load_request(job_id, model))
            except CryptoError:
                await _shielded(self._fail(job_id, "PAYLOAD_LOST", retryable=True))
                return
            if req is None:
                await _shielded(self._fail(job_id, "PAYLOAD_LOST", retryable=True))
                return
            final: Final | None = None
            buf: list[str] = []
            last_flush = loop.time()
            async for ch in self.engines[model].stream(req):
                if isinstance(ch, Delta):
                    if not req.stream:
                        continue
                    buf.append(ch.text)
                    if loop.time() - last_flush >= flush_s:
                        if not await _shielded(self._emit_token(job_id, "".join(buf))):
                            return
                        buf.clear()
                        last_flush = loop.time()
                else:
                    final = ch
            if buf and not await _shielded(self._emit_token(job_id, "".join(buf))):
                return
            if final is None:
                raise EngineUnavailable("stream ended without final")
            await _shielded(self._finish(job_id, final))
        except asyncio.CancelledError:
            raise
        except EngineUnavailable as e:
            log.warning("engine unavailable model=%s job_id=%s err=%s", model, job_id, e)
            self._down[model] = loop.time() + self.settings.scheduler.engine_retry_s
            await _shielded(self._requeue(job_id, "engine_unavailable"))
        except EngineRequestError:
            log.warning("engine rejected job_id=%s", job_id)
            await _shielded(self._fail(job_id, "ENGINE_ERROR", retryable=False))
        except Exception as e:  # 想定外: 本文を出さずに型だけ記録する
            log.error("job crashed job_id=%s err=%s", job_id, type(e).__name__)
            await _shielded(self._fail(job_id, "INTERNAL", retryable=True))
        finally:
            self._tasks.pop(job_id, None)
            self.wake()

    async def _emit_token(self, job_id: str, delta: str) -> bool:
        async with self._lock(job_id):
            async with self.db.tx() as s:
                state = (await s.execute(select(Job.state).where(Job.id == job_id))).scalar_one_or_none()
                if state != "running":
                    return False
                await self._append(s, job_id, "token", {"delta": delta})
        self.hub.notify(job_id)
        return True

    async def _finish(self, job_id: str, final: Final) -> None:
        now = self.clock.now()
        async with self._lock(job_id):
            async with self.db.tx() as s:
                job = await s.get(Job, job_id)
                if job is None or job.state != "running":
                    return
                usage = {
                    "input_tokens": final.input_tokens or job.input_tokens,
                    "output_tokens": final.output_tokens,
                }
                job.state = "done"
                job.finished_at = now
                job.output_tokens = final.output_tokens
                job.result_expires_at = now + timedelta(seconds=self.settings.jobs.result_ttl_s)
                resp = json.dumps(
                    {"output": final.output, "usage": usage, "finish_reason": final.finish_reason},
                    ensure_ascii=False,
                ).encode()
                await s.execute(
                    update(JobPayload)
                    .where(JobPayload.job_id == job_id)
                    .values(request_ct=None, response_ct=self.cipher.encrypt(job_id, "response", resp))
                )
                await self._append(
                    s,
                    job_id,
                    "done",
                    {"output": final.output, "usage": usage, "finish_reason": final.finish_reason},
                )
                started = job.started_at or now
                user_id, model, purpose = job.user_id, job.model, job.purpose
        self.hub.notify(job_id)
        secs = max(0.0, (now - started).total_seconds())
        self._durations.append(secs)
        self.metrics.inc("llmgw_jobs_total", "終了したジョブ数", state="done")
        self.metrics.observe_job(secs, model=model)
        await self.usage.add(
            user_id,
            now.date(),
            jobs=1,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
        )
        log.info(
            "job done job_id=%s model=%s purpose=%s seconds=%.2f output_tokens=%d",
            job_id,
            model,
            purpose,
            secs,
            final.output_tokens,
        )

    async def _fail(self, job_id: str, code: str, *, retryable: bool) -> None:
        async with self._lock(job_id):
            async with self.db.tx() as s:
                job = await s.get(Job, job_id)
                if job is None or job.state in TERMINAL:
                    return
                await self._terminate(s, job, "failed", code, retryable=retryable)
        self.hub.notify(job_id)
        self.metrics.inc("llmgw_jobs_total", "終了したジョブ数", state="failed")

    async def _requeue(self, job_id: str, reason: str) -> None:
        give_up = False
        async with self._lock(job_id):
            async with self.db.tx() as s:
                job = await s.get(Job, job_id)
                if job is None or job.state != "running":
                    return
                job.requeue_count += 1
                if job.requeue_count > self.settings.scheduler.max_requeues:
                    await self._terminate(s, job, "failed", "ENGINE_UNAVAILABLE", retryable=True)
                    give_up = True
                else:
                    job.state = "queued"
                    job.started_at = None
                    await self._append(s, job_id, "requeued", {"reason": reason})
        self.hub.notify(job_id)
        self.metrics.inc("llmgw_requeues_total", "running から queued に戻した回数", reason=reason)
        if give_up:
            self.metrics.inc("llmgw_jobs_total", "終了したジョブ数", state="failed")

    # ------------------------------------------------------------------ 掃除（R4: 完了＋10分で削除）

    async def _cleanup_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self.settings.jobs.cleanup_interval_s)
            try:
                await self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("cleanup failed")

    async def cleanup_once(self) -> int:
        now = self.clock.now()
        async with self.db.tx() as s:
            ids = list(
                (
                    await s.execute(
                        select(Job.id).where(Job.result_expires_at.is_not(None), Job.result_expires_at <= now)
                    )
                ).scalars()
            )
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                await s.execute(delete(JobEvent).where(JobEvent.job_id.in_(chunk)))
                await s.execute(delete(JobPayload).where(JobPayload.job_id.in_(chunk)))
        for jid in ids:
            self._seq.pop(jid, None)
            self._locks.pop(jid, None)
            self._last_pos.pop(jid, None)
            self.hub.notify(jid)
        return len(ids)

    # ------------------------------------------------------------------ 参照（管理・メトリクス）

    @property
    def running_count(self) -> int:
        return len(self._tasks)

    def engine_status(self) -> dict[str, str]:
        return {m: ("unavailable" if m in self._down else "ok") for m in self.models}
