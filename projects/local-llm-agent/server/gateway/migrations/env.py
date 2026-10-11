"""Alembic の環境。接続は llm_gateway.migrate から config.attributes["connection"] で渡す。

単体で使う場合（`alembic -c server/gateway/alembic.ini upgrade head`）は、環境変数
LLMGW_DATABASE_URL の DB に接続する。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from llm_gateway.db import Base

target_metadata = Base.metadata


def _run(connection: Any) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


def run_migrations_online() -> None:
    conn = context.config.attributes.get("connection")
    if conn is not None:
        _run(conn)
        return
    url = os.environ.get("LLMGW_DATABASE_URL")
    if not url:
        raise RuntimeError("LLMGW_DATABASE_URL を設定してください")
    asyncio.run(_run_async(url))


if context.is_offline_mode():
    raise RuntimeError("オフラインモード（SQL の書き出し）には対応していません")
run_migrations_online()
