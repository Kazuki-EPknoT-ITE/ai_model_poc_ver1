from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from lla_eval.__main__ import main
from lla_eval.compare import CompareError, compare, compare_table, sign_test_p


def _result(model: str, ok: dict[str, bool], **extra: Any) -> dict[str, Any]:
    return {
        "eval_set": "tool_tasks_synth",
        "data_hash": "sha256:" + "a" * 64,
        "run_id": f"run-{model}",
        "model_id": model,
        "items": [
            {
                "id": i,
                "ok": v,
                "checks": [{"type": "csv_rows_equal", "ok": v}, {"type": "max_steps", "ok": True}],
            }
            for i, v in ok.items()
        ],
        **extra,
    }


def test_sign_test() -> None:
    assert sign_test_p(0, 0) == 1.0
    assert sign_test_p(5, 0) == pytest.approx(0.0625)  # 2 × (1/2)^5
    assert sign_test_p(10, 0) < 0.01
    assert sign_test_p(3, 3) == 1.0


def test_compare_counts_and_templates() -> None:
    ids = [f"memo_to_csv-0{i}" for i in range(1, 6)] + [f"ask_which_memo-0{i}" for i in range(1, 6)]
    base = _result("base", {i: i.startswith("memo") for i in ids})
    cand = _result("lora", {i: i != "memo_to_csv-01" for i in ids})
    r = compare(base, cand)
    assert (r["n"], r["base"]["success"], r["cand"]["success"]) == (10, 5, 9)
    assert r["improved"] == [f"ask_which_memo-0{i}" for i in range(1, 6)]
    assert r["regressed"] == ["memo_to_csv-01"]
    assert r["by_template"]["ask_which_memo"] == {"n": 5, "base_ok": 0, "cand_ok": 5}
    assert r["failed_checks"]["base"] == {"csv_rows_equal": 5}
    assert r["cand_better"] and not r["significant"]  # 5 対 1 は p ≈ 0.22
    text = compare_table(r)
    assert "ask_which_memo" in text and "↑" in text and "偶然の範囲" in text


def test_compare_significant() -> None:
    ids = [f"t-{i:02d}" for i in range(20)]
    r = compare(_result("b", dict.fromkeys(ids, False)), _result("c", dict.fromkeys(ids, True)))
    assert r["significant"] and "有意" in compare_table(r)


def test_compare_refuses_different_sets() -> None:
    a = _result("b", {"x-01": True})
    with pytest.raises(CompareError, match="data_hash"):
        compare(a, _result("c", {"x-01": True}, data_hash="sha256:" + "b" * 64))
    with pytest.raises(CompareError, match="評価セット"):
        compare(a, _result("c", {"x-01": True}, eval_set="other"))


def test_cli_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "a.json").write_text(json.dumps(_result("b", {"x-01": False})), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(_result("c", {"x-01": True})), encoding="utf-8")
    assert main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out.strip().splitlines()[-1])["improved"] == ["x-01"]
    assert "判定" in out.err
    (tmp_path / "c.json").write_text(json.dumps(_result("c", {"x-01": True}, eval_set="z")), encoding="utf-8")
    assert main(["compare", str(tmp_path / "a.json"), str(tmp_path / "c.json")]) == 2
