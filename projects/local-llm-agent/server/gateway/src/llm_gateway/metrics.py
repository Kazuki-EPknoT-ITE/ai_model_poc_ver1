"""Prometheus のテキスト形式（外部ライブラリを使わない最小実装）。ラベルに本文は入れない。"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable

JOB_BUCKETS = (0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0)

LabelKey = tuple[tuple[str, str], ...]


def _labels(key: LabelKey) -> str:
    if not key:
        return ""
    inner = ",".join(f'{k}="{_esc(v)}"' for k, v in key)
    return "{" + inner + "}"


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(v: float) -> str:
    if math.isinf(v):
        return "+Inf"
    return repr(float(v)) if not float(v).is_integer() else str(int(v))


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[str, dict[LabelKey, float]] = defaultdict(lambda: defaultdict(float))
        self.help: dict[str, str] = {}
        self.hist_counts: dict[LabelKey, list[int]] = {}
        self.hist_sum: dict[LabelKey, float] = defaultdict(float)
        self.gauges: dict[str, Callable[[], dict[LabelKey, float]]] = {}

    def inc(self, name: str, help_: str, value: float = 1.0, **labels: str) -> None:
        self.help.setdefault(name, help_)
        self.counters[name][tuple(sorted(labels.items()))] += value

    def observe_job(self, seconds: float, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        counts = self.hist_counts.setdefault(key, [0] * (len(JOB_BUCKETS) + 1))
        for i, b in enumerate(JOB_BUCKETS):
            if seconds <= b:
                counts[i] += 1
        counts[-1] += 1
        self.hist_sum[key] += seconds

    def gauge(self, name: str, help_: str, fn: Callable[[], dict[LabelKey, float]]) -> None:
        self.help[name] = help_
        self.gauges[name] = fn

    def render(self) -> str:
        out: list[str] = []
        for name, series in sorted(self.counters.items()):
            out.append(f"# HELP {name} {self.help.get(name, '')}")
            out.append(f"# TYPE {name} counter")
            for key, v in sorted(series.items()):
                out.append(f"{name}{_labels(key)} {_fmt(v)}")
        for name, fn in sorted(self.gauges.items()):
            out.append(f"# HELP {name} {self.help.get(name, '')}")
            out.append(f"# TYPE {name} gauge")
            for key, v in sorted(fn().items()):
                out.append(f"{name}{_labels(key)} {_fmt(v)}")
        hname = "llmgw_job_seconds"
        out.append(f"# HELP {hname} 推論ジョブの処理時間（開始から完了まで）")
        out.append(f"# TYPE {hname} histogram")
        for key, counts in sorted(self.hist_counts.items()):
            for i, b in enumerate(JOB_BUCKETS):
                out.append(f"{hname}_bucket{_labels(key + (('le', _fmt(b)),))} {counts[i]}")
            out.append(f"{hname}_bucket{_labels(key + (('le', '+Inf'),))} {counts[-1]}")
            out.append(f"{hname}_sum{_labels(key)} {_fmt(self.hist_sum[key])}")
            out.append(f"{hname}_count{_labels(key)} {counts[-1]}")
        return "\n".join(out) + "\n"
