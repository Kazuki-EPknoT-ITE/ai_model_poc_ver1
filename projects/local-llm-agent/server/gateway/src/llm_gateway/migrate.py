"""Alembic のマイグレーションをプログラムから実行する（DDL の正本は server/gateway/migrations/）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine


def migrations_dir() -> Path:
    """ソースのまま（editable）なら server/gateway/migrations、wheel なら同梱の _migrations。"""
    here = Path(__file__).resolve().parent
    for cand in (here / "_migrations", here.parents[1] / "migrations"):
        if (cand / "env.py").exists():
            return cand
    raise FileNotFoundError("migrations が見つかりません")


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir()))
    return cfg


def _upgrade(sync_conn: Any, revision: str) -> None:
    cfg = alembic_config()
    cfg.attributes["connection"] = sync_conn
    command.upgrade(cfg, revision)


async def upgrade(engine: AsyncEngine, revision: str = "head") -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_upgrade, revision)
