"""評価ハーネス（T-50）の Python 側: 評価セットの読み込み・採点・合否表・台帳向けの出力。

Runtime を動かす部分は TypeScript（eval/harness/src）。ここは「データの検証」と「成果物の採点」を受け持つ
（local_llm_test_spec.md §3、local_llm_design.md §8.5）。
"""

from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.1.0"
