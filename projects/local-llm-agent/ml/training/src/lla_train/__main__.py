"""lla-train: ツール呼び出し学習の CLI。

uv run python -m lla_train prepare <datagen の出力フォルダ>... --out <フォルダ> [--val-pct 10] [--max-seq-len 8192]
uv run python -m lla_train check-config <configs/*.yaml>
uv run python -m lla_train train --config <configs/*.yaml> --data <prepare の出力> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lla_train.config import ConfigError, load_config
from lla_train.prepare import PrepareError, prepare
from lla_train.train import run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lla-train")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="ml/datagen の出力を学習用に整える")
    p.add_argument("sources", nargs="+", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--val-pct", type=int, default=10)
    p.add_argument("--max-seq-len", type=int, default=8192)
    c = sub.add_parser("check-config", help="学習の設定を確かめる")
    c.add_argument("config", type=Path)
    t = sub.add_parser("train", help="QLoRA で学習する（--dry-run は GPU なしで見積もりまで）")
    t.add_argument("--config", type=Path, required=True)
    t.add_argument("--data", type=Path, required=True)
    t.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "prepare":
            st = prepare(list(a.sources), a.out, val_pct=a.val_pct, max_seq_len=a.max_seq_len)
            out = {"train": st.train, "val": st.val, "duplicates": st.duplicates, "too_long": st.too_long}
        elif a.cmd == "check-config":
            out = load_config(a.config).to_dict()
        else:
            out = run(load_config(a.config), a.data, dry_run=a.dry_run)
    except ConfigError as e:
        print(json.dumps({"error": e.args[0]}, ensure_ascii=False))
        return 2
    except PrepareError as e:
        print(json.dumps({"error": [str(e)]}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
