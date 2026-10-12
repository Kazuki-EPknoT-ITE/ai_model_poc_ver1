"""ダッシュボードの GPU（ADM-03）: dcgm-exporter の /metrics（Prometheus 形式）から温度・使用率・メモリを読む。

設定 `metrics.dcgm_url`（例 http://dcgm-exporter:9400/metrics）が無ければ空のリストを返す（管理画面は「取得できません」）。
取得できないときも空のリストにし、ダッシュボード全体は失敗させない。10秒だけ結果を覚えておく。
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gateway import Gateway

log = logging.getLogger("llm_gateway.gpu")

_LINE = re.compile(r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)")
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
FIELDS = {
    "DCGM_FI_DEV_GPU_TEMP": "temp_c",
    "DCGM_FI_DEV_GPU_UTIL": "util_pct",
    "DCGM_FI_DEV_FB_USED": "mem_used_mb",
    "DCGM_FI_DEV_FB_FREE": "mem_free_mb",
}
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def parse_dcgm(text: str) -> list[dict[str, Any]]:
    """dcgm-exporter の出力を GPU ごとにまとめる（名前・温度・使用率・メモリ MB）。"""
    gpus: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m or m.group("name") not in FIELDS:
            continue
        labels = dict(_LABEL.findall(m.group("labels") or ""))
        key = f"{labels.get('Hostname', '')}/{labels.get('gpu', labels.get('UUID', '0'))}"
        g = gpus.setdefault(key, {"name": labels.get("modelName", f"GPU {labels.get('gpu', '?')}")})
        try:
            g[FIELDS[m.group("name")]] = float(m.group("value"))
        except ValueError:
            continue
    out = []
    for g in gpus.values():
        used, free = g.get("mem_used_mb"), g.get("mem_free_mb")
        out.append(
            {
                "name": g["name"],
                "temp_c": round(g.get("temp_c", 0)),
                "util_pct": round(g.get("util_pct", 0)),
                "mem_used_mb": round(used or 0),
                "mem_total_mb": round((used or 0) + (free or 0)),
            }
        )
    return out


async def gpu_status(gw: Gateway) -> list[dict[str, Any]]:
    url = gw.settings.metrics.dcgm_url
    if not url:
        return []
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < 10:
        return hit[1]
    try:
        r = await gw.http.get(url, timeout=3.0)
        r.raise_for_status()
        gpus = parse_dcgm(r.text)
    except Exception as e:  # noqa: BLE001 - 取れなければ空で返す（ダッシュボードは止めない）
        log.warning("dcgm metrics unavailable: %s", type(e).__name__)
        gpus = []
    _cache[url] = (time.monotonic(), gpus)
    return gpus
