"""Gateway の DB（net_sites / dlp_terms）からポリシーを定期的に取り込む。

- `GET {gateway_url}/internal/v1/net-policy`（ヘッダー X-Relay-Secret。Gateway と共有する内部トークン）。
- 取り込んだサイト・機密語は、設定ファイルの `policy` に**足す**（設定ファイル側の社内ドメインなどはそのまま）。
- 安全側に倒す: 最初の取り込みに成功するまで、また最後の成功から `max_stale_s` を過ぎたら、
  検索・取得を 503 で断る（古い・空の辞書のまま外へ出さない）。
- 取り込みの失敗は、語や URL を含めずに種類だけログへ出す。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .config import DlpTermSettings, OrgPolicySettings, PolicySettings, PolicySyncSettings, SiteSettings
from .policy import PolicyStore

log = logging.getLogger("web_relay.policy_sync")
SECRET_HEADER = "X-Relay-Secret"  # noqa: S105 - ヘッダー名
PATH = "/internal/v1/net-policy"


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _OrgIn(_Loose):
    dlp_terms: list[DlpTermSettings] = Field(default_factory=list)


class NetPolicyIn(_Loose):
    sites: list[SiteSettings] = Field(default_factory=list)
    orgs: dict[str, _OrgIn] = Field(default_factory=dict)
    version: str = ""


def merge(base: PolicySettings, remote: NetPolicyIn) -> PolicySettings:
    """設定ファイルのポリシーに、Gateway から取り込んだサイト・機密語を足す。"""
    orgs = {k: v.model_copy(deep=True) for k, v in base.orgs.items()}
    for org_id, o in remote.orgs.items():
        cur = orgs.get(org_id) or OrgPolicySettings()
        orgs[org_id] = cur.model_copy(update={"dlp_terms": [*cur.dlp_terms, *o.dlp_terms]})
    return base.model_copy(update={"sites": [*base.sites, *remote.sites], "orgs": orgs})


class PolicySync:
    def __init__(
        self,
        base: PolicySettings,
        settings: PolicySyncSettings,
        store: PolicyStore,
        secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        assert settings.gateway_url
        self.base = base
        self.settings = settings
        self.store = store
        self.version: str | None = None
        self.last_ok: float | None = None
        self._clock = clock
        self._client = httpx.AsyncClient(
            base_url=settings.gateway_url.rstrip("/"),
            headers={SECRET_HEADER: secret},
            timeout=settings.timeout_s,
            verify=settings.ca_file or True,
            transport=transport,
            trust_env=False,  # 社内の Gateway へはプロキシを通さない
        )

    def ready(self) -> bool:
        return self.last_ok is not None and self._clock() - self.last_ok <= self.settings.max_stale_s

    async def sync_once(self) -> bool:
        try:
            res = await self._client.get(PATH)
            if res.status_code != 200:
                log.warning("policy sync: HTTP %s", res.status_code)
                return False
            remote = NetPolicyIn.model_validate(res.json())
            if remote.version != self.version or self.last_ok is None:
                self.store.load(merge(self.base, remote))
                terms = sum(len(o.dlp_terms) for o in remote.orgs.values())
                log.info(
                    "policy sync: loaded version=%s sites=%d terms=%d",
                    remote.version,
                    len(remote.sites),
                    terms,
                )
                self.version = remote.version
        except Exception as e:  # noqa: BLE001 - 失敗の種類だけ残し、前回の内容で続ける
            log.warning("policy sync failed: %s", type(e).__name__)
            return False
        self.last_ok = self._clock()
        return True

    async def run(self, stop: asyncio.Event) -> None:
        """止めるまで取り込みを繰り返す。失敗したときは短めの間隔でやり直す。"""
        while not stop.is_set():
            ok = await self.sync_once()
            wait = self.settings.interval_s if ok else min(self.settings.interval_s, self.settings.retry_s)
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except TimeoutError:
                pass

    async def aclose(self) -> None:
        await self._client.aclose()

    def status(self) -> dict[str, Any]:
        return {"ready": self.ready(), "version": self.version}
