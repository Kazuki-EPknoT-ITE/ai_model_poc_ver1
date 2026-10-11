"""Gateway の試験の共通部品。

DB は既定で SQLite（aiosqlite）。環境変数 LLA_TEST_PG_URL（例:
postgresql+asyncpg://llmgw:pw@localhost:5432/llmgw_test）があれば PostgreSQL でも同じ試験を走らせる。
**PostgreSQL の試験は public スキーマを作り直す**ので、試験専用の DB を指定すること。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

PG_URL = os.environ.get("LLA_TEST_PG_URL")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["sqlite", "postgresql"])
def db_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    if request.param == "sqlite":
        yield f"sqlite+aiosqlite:///{(tmp_path / 'gw.db').as_posix()}"
    else:
        if not PG_URL:
            pytest.skip("LLA_TEST_PG_URL が未設定のため PostgreSQL の試験は省略")
        yield PG_URL
