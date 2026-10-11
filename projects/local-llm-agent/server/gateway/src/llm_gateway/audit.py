"""監査ログ（SEC-05, NET-11）。本文は入れない。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import AuditEvent, Database
from .util import Clock


class ClientAuditRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=128)
    at: datetime | None = None
    action: str = Field(min_length=1, max_length=128)
    target: str | None = Field(default=None, max_length=4096)
    result: str = Field(min_length=1, max_length=256)
    detail: dict[str, Any] | None = None


class AuditBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    records: list[ClientAuditRecord] = Field(max_length=1000)


class AuditLog:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    async def record(
        self,
        *,
        source: str,
        action: str,
        result: str,
        user_id: str | None = None,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        async with self.db.tx() as s:
            s.add(
                AuditEvent(
                    at=self.clock.now(),
                    user_id=user_id,
                    source=source,
                    action=action,
                    target=target,
                    result=result,
                    detail=detail,
                )
            )

    async def ingest(self, user_id: str, records: list[ClientAuditRecord]) -> int:
        """クライアントの記録を受け取る。`id` で重複を除く（再送に備える）。追加した件数を返す。"""
        uniq: dict[str, ClientAuditRecord] = {}
        for r in records:
            uniq.setdefault(f"{user_id}:{r.id}", r)
        if not uniq:
            return 0
        async with self.db.tx() as s:
            q = select(AuditEvent.client_id).where(AuditEvent.client_id.in_(list(uniq)))
            seen = set((await s.execute(q)).scalars())
        new = [(cid, r) for cid, r in uniq.items() if cid not in seen]
        try:
            async with self.db.tx() as s:
                s.add_all(self._row(user_id, cid, r) for cid, r in new)
            return len(new)
        except IntegrityError:
            pass  # 同時に届いた再送と重なった → 1件ずつ入れ直す
        added = 0
        for cid, r in new:
            try:
                async with self.db.tx() as s:
                    s.add(self._row(user_id, cid, r))
                added += 1
            except IntegrityError:
                continue
        return added

    def _row(self, user_id: str, cid: str, r: ClientAuditRecord) -> AuditEvent:
        return AuditEvent(
            client_id=cid,
            at=r.at or self.clock.now(),
            user_id=user_id,
            source="client",
            action=r.action,
            target=r.target,
            result=r.result,
            detail=r.detail,
        )
