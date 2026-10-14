"""評価ハーネスの Python 側の入口（TypeScript のランナーと障害注入試験から呼ぶ）。

    python -m lla_eval load <set_dir> --cache <dir>            評価セットを検証して JSON で出す
    python -m lla_eval hash <set_dir> --cache <dir>            data_hash を計算する（登録用）
    python -m lla_eval finalize <set_dir> --record <json> --out <dir> --cache <dir>
                                                               採点 → 結果 JSON・台帳用 JSON → 合否表
    python -m lla_eval gen-invoices <out> --count N [--broken JSON] [--mock-fixtures <dir>]
    python -m lla_eval xlsx-rows <path> [--sheet NAME]         セル値を JSON で出す（試験の照合用）
    python -m lla_eval open-check <path>                       Office 文書が開けるか
    python -m lla_eval compare <基準の結果.json> <候補の結果.json>  2回の結果を比べる（POC 条件2）

PYTHONPATH に eval/harness を入れて実行する（ランナーが設定する）。
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from .compare import CompareError, compare, compare_table
from .manifest import ManifestError, compute_data_hash, load_set, parse_manifest, resolve_data_root
from .report import build_result, registry_rows, table
from .scoring import open_check, read_xlsx_rows, score
from .synth import invoice_model_output, make_invoices


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    return str(o)


def _print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=_default) + "\n")


def cmd_load(a: argparse.Namespace) -> int:
    loaded = load_set(Path(a.set_dir).resolve(), Path(a.cache), verify_hash=not a.no_verify_hash)
    _print(loaded.to_json())
    return 0


def cmd_hash(a: argparse.Namespace) -> int:
    import yaml  # noqa: PLC0415

    set_dir = Path(a.set_dir).resolve()
    m = parse_manifest(yaml.safe_load((set_dir / "manifest.yaml").read_text(encoding="utf-8")))
    root = resolve_data_root(m, set_dir, Path(a.cache))
    _print({"data_hash": compute_data_hash(root, set_dir / "items.jsonl"), "data_root": str(root)})
    return 0


def cmd_finalize(a: argparse.Namespace) -> int:
    set_dir = Path(a.set_dir).resolve()
    loaded = load_set(set_dir, Path(a.cache), verify_hash=not a.no_verify_hash)
    record = json.loads(Path(a.record).read_text(encoding="utf-8"))
    sc = score(loaded.manifest.scorer, loaded.items, record.get("outputs") or {}, set_dir, loaded.data_root)
    result = build_result(loaded.to_json(), loaded.manifest, record, sc)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    run_id = str(record.get("run_id"))
    (out / f"{run_id}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=_default), encoding="utf-8"
    )
    (out / f"{run_id}.registry.json").write_text(
        json.dumps(registry_rows(result), ensure_ascii=False, indent=1, default=_default), encoding="utf-8"
    )
    sys.stderr.write(table(result) + "\n")
    _print(
        {"result": str(out / f"{run_id}.json"), "verdict": result["verdict"], "metrics": result["metrics"]}
    )
    return 1 if a.strict and result["verdict"] != "合格" else 0


def cmd_gen_invoices(a: argparse.Namespace) -> int:
    broken = json.loads(a.broken) if a.broken else None
    bad = make_invoices(Path(a.out), a.count, broken=broken, expected=not a.no_expected, start=a.start)
    if a.mock_fixtures:
        d = Path(a.mock_fixtures)
        d.mkdir(parents=True, exist_ok=True)
        width = max(3, len(str(a.count)))
        for n in range(1, a.count + 1):
            (d / f"INV-{n:0{width}d}.json").write_text(
                json.dumps(invoice_model_output(n + a.start - 1), ensure_ascii=False), encoding="utf-8"
            )
    _print({"broken": bad})
    return 0


def cmd_xlsx_rows(a: argparse.Namespace) -> int:
    _print(read_xlsx_rows(Path(a.path), a.sheet))
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    base = json.loads(Path(a.base).read_text(encoding="utf-8"))
    cand = json.loads(Path(a.cand).read_text(encoding="utf-8"))
    try:
        r = compare(base, cand, alpha=a.alpha)
    except CompareError as e:
        _print({"error": [str(e)]})
        return 2
    sys.stderr.write(compare_table(r) + "\n")
    _print(r)
    return 0


def cmd_open_check(a: argparse.Namespace) -> int:
    reason = open_check(Path(a.path))
    _print({"ok": reason is None, "reason": reason})
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lla_eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("load")
    p.add_argument("set_dir")
    p.add_argument("--cache", required=True)
    p.add_argument("--no-verify-hash", action="store_true")
    p.set_defaults(fn=cmd_load)
    p = sub.add_parser("hash")
    p.add_argument("set_dir")
    p.add_argument("--cache", required=True)
    p.set_defaults(fn=cmd_hash)
    p = sub.add_parser("finalize")
    p.add_argument("set_dir")
    p.add_argument("--record", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--cache", required=True)
    p.add_argument("--no-verify-hash", action="store_true")
    p.add_argument("--strict", action="store_true", help="総合が合格でなければ終了コード1")
    p.set_defaults(fn=cmd_finalize)
    p = sub.add_parser("gen-invoices")
    p.add_argument("out")
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--broken", default=None)
    p.add_argument("--mock-fixtures", default=None)
    p.add_argument("--no-expected", action="store_true")
    p.add_argument("--start", type=int, default=1, help="中身の通し番号の始まり（ファイル名は 1 から）")
    p.set_defaults(fn=cmd_gen_invoices)
    p = sub.add_parser("xlsx-rows")
    p.add_argument("path")
    p.add_argument("--sheet", default=None)
    p.set_defaults(fn=cmd_xlsx_rows)
    p = sub.add_parser("open-check")
    p.add_argument("path")
    p.set_defaults(fn=cmd_open_check)
    p = sub.add_parser("compare")
    p.add_argument("base")
    p.add_argument("cand")
    p.add_argument("--alpha", type=float, default=0.05)
    p.set_defaults(fn=cmd_compare)
    a = ap.parse_args(argv)
    try:
        rc: int = a.fn(a)
    except ManifestError as e:
        _print({"error": e.problems})
        sys.stderr.write("評価セットを実行できません:\n" + "\n".join(f"- {x}" for x in e.problems) + "\n")
        return 2
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
