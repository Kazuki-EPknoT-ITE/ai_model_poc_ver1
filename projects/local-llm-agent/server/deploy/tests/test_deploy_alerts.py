"""アラートのルール（OPS-05）を promtool で検査・試験する。promtool が無ければ飛ばす。

promtool は環境変数 PROMTOOL（実行ファイルのパス）か PATH から探す。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROM_DIR = Path(__file__).resolve().parents[1] / "prometheus"
PROMTOOL = os.environ.get("PROMTOOL") or shutil.which("promtool")
pytestmark = pytest.mark.skipif(PROMTOOL is None, reason="promtool がありません（PROMTOOL で指定できます）")


def _promtool(*args: str) -> subprocess.CompletedProcess[str]:
    assert PROMTOOL is not None
    return subprocess.run(
        [PROMTOOL, *args], cwd=PROM_DIR, capture_output=True, text=True, encoding="utf-8", check=False
    )


def test_rules_are_valid() -> None:
    r = _promtool("check", "rules", "alerts.yml")
    assert r.returncode == 0, r.stdout + r.stderr


def test_rules_fire_as_designed() -> None:
    r = _promtool("test", "rules", "alerts.test.yml")
    assert r.returncode == 0, r.stdout + r.stderr
