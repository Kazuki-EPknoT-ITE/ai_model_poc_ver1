"""ダッシュボードの GPU（ADM-03）: dcgm-exporter の出力を読む。取れなくてもダッシュボードは返す。"""

from __future__ import annotations

import httpx
import pytest

from llm_gateway import gpu
from llm_gateway.gpu import parse_dcgm

from .gw_helpers import rig

pytestmark = pytest.mark.anyio

SAMPLE = """# HELP DCGM_FI_DEV_GPU_TEMP GPU temperature (in C).
# TYPE DCGM_FI_DEV_GPU_TEMP gauge
DCGM_FI_DEV_GPU_TEMP{gpu="0",UUID="GPU-abc",device="nvidia0",modelName="NVIDIA GeForce RTX 5080",Hostname="llm-sv01"} 61
# HELP DCGM_FI_DEV_GPU_UTIL GPU utilization (in %).
DCGM_FI_DEV_GPU_UTIL{gpu="0",UUID="GPU-abc",device="nvidia0",modelName="NVIDIA GeForce RTX 5080",Hostname="llm-sv01"} 78
DCGM_FI_DEV_FB_USED{gpu="0",UUID="GPU-abc",device="nvidia0",modelName="NVIDIA GeForce RTX 5080",Hostname="llm-sv01"} 14100
DCGM_FI_DEV_FB_FREE{gpu="0",UUID="GPU-abc",device="nvidia0",modelName="NVIDIA GeForce RTX 5080",Hostname="llm-sv01"} 2203
DCGM_FI_DEV_POWER_USAGE{gpu="0",UUID="GPU-abc",modelName="NVIDIA GeForce RTX 5080",Hostname="llm-sv01"} 250.5
DCGM_FI_DEV_GPU_TEMP{gpu="1",UUID="GPU-def",modelName="NVIDIA RTX PRO 6000",Hostname="llm-sv01"} 55
"""


def test_parse_dcgm() -> None:
    gpus = parse_dcgm(SAMPLE)
    assert gpus[0] == {
        "name": "NVIDIA GeForce RTX 5080",
        "temp_c": 61,
        "util_pct": 78,
        "mem_used_mb": 14100,
        "mem_total_mb": 16303,
    }
    assert gpus[1]["name"] == "NVIDIA RTX PRO 6000" and gpus[1]["temp_c"] == 55
    assert parse_dcgm("garbage\n{}\n") == []


async def test_dashboard_reads_dcgm_and_survives_failures(db_url: str) -> None:
    state = {"ok": True}

    def handler(req: httpx.Request) -> httpx.Response:
        assert str(req.url) == "http://dcgm-exporter:9400/metrics"
        return httpx.Response(200, text=SAMPLE) if state["ok"] else httpx.Response(503)

    async with rig(db_url, upstream_transport=httpx.MockTransport(handler)) as r:
        await r.user("root", role="admin")
        h = r.h("root")
        # 設定が無ければ空
        assert (await r.http.get("/admin/v1/dashboard", headers=h)).json()["gpu"] == []
        r.gw.settings.metrics.dcgm_url = "http://dcgm-exporter:9400/metrics"
        gpu._cache.clear()
        d = (await r.http.get("/admin/v1/dashboard", headers=h)).json()
        assert d["gpu"][0]["name"] == "NVIDIA GeForce RTX 5080" and d["gpu"][0]["temp_c"] == 61
        # exporter が落ちてもダッシュボードは返る（GPU は空）
        state["ok"] = False
        gpu._cache.clear()
        d = await r.http.get("/admin/v1/dashboard", headers=h)
        assert d.status_code == 200 and d.json()["gpu"] == []
