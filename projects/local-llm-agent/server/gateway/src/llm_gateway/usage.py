"""利用状況の集計（usage_daily）。方言に依存しないよう「読んでから書く」を1プロセス内の鎖で直列化する。"""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import func, select

from .db import Database, UsageDaily


class UsageRecorder:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = asyncio.Lock()

    async def add(
        self,
        user_id: str,
        day: date,
        *,
        jobs: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        web_searches: int = 0,
        web_fetches: int = 0,
    ) -> None:
        async with self._lock, self.db.tx() as s:
            row = await s.get(UsageDaily, (user_id, day))
            if row is None:
                row = UsageDaily(
                    user_id=user_id,
                    day=day,
                    jobs=0,
                    input_tokens=0,
                    output_tokens=0,
                    web_searches=0,
                    web_fetches=0,
                )
                s.add(row)
            row.jobs += jobs
            row.input_tokens += input_tokens
            row.output_tokens += output_tokens
            row.web_searches += web_searches
            row.web_fetches += web_fetches

    async def searches_this_month(self, user_id: str, today: date) -> int:
        first = today.replace(day=1)
        async with self.db.tx() as s:
            q = select(func.coalesce(func.sum(UsageDaily.web_searches), 0)).where(
                UsageDaily.user_id == user_id, UsageDaily.day >= first, UsageDaily.day <= today
            )
            return int((await s.execute(q)).scalar_one())
