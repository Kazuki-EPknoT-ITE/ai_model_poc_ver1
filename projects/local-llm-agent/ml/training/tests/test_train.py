from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from lla_train.__main__ import main
from lla_train.config import ConfigError, load_config, parse_config
from lla_train.prepare import PrepareError, prepare
from lla_train.train import run

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def _row(ep: str, step: int, template: str = "memo_to_csv", content: str = "依頼") -> dict[str, Any]:
    return {
        "episode": ep,
        "template": template,
        "step": step,
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": f"{content} {ep} {step}"},
        ],
        "completion": json.dumps({"type": "plan_update", "plan": []}),
        "constrained": True,
    }


def _source(d: Path, rows: list[dict[str, Any]], **extra: Any) -> Path:
    d.mkdir(parents=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    (d / "train.jsonl").write_bytes(body)
    manifest = {
        "sha256": hashlib.sha256(body).hexdigest(),
        "protocol_version": "1.0",
        "prompt_version": "1.0",
        "eval_overlap": {"checked": ["x"], "skipped": [], "files": []},
        **extra,
    }
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def _jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def test_prepare_splits_by_episode_and_formats(tmp_path: Path) -> None:
    rows = [_row(f"ep{e}", s) for e in range(40) for s in range(3)]
    src = _source(tmp_path / "src", rows + [rows[0]])  # 最後の1行は重複
    st = prepare([src], tmp_path / "out", val_pct=20)
    assert st.duplicates == 1
    assert st.train + st.val == 120
    train, val = _jsonl(tmp_path / "out" / "train.jsonl"), _jsonl(tmp_path / "out" / "val.jsonl")
    assert val and train
    # 同じ手順例の手が、学習と検証の両方に入らない
    assert not {r["episode"] for r in train} & {r["episode"] for r in val}
    r = train[0]
    assert r["prompt"][0]["role"] == "system"
    assert r["completion"] == [
        {"role": "assistant", "content": json.dumps({"type": "plan_update", "plan": []})}
    ]
    stats = json.loads((tmp_path / "out" / "stats.json").read_text(encoding="utf-8"))
    assert stats["prompt_version"] == "1.0" and len(stats["data_hash"]) == 64
    # 同じ入力なら同じ分け方・同じハッシュ
    prepare([src], tmp_path / "out2", val_pct=20)
    assert (
        json.loads((tmp_path / "out2" / "stats.json").read_text(encoding="utf-8"))["data_hash"]
        == stats["data_hash"]
    )


def test_prepare_drops_too_long(tmp_path: Path) -> None:
    src = _source(tmp_path / "src", [_row("a", 0), _row("b", 0, content="長" * 20000)])
    st = prepare([src], tmp_path / "out", val_pct=0, max_seq_len=1024)
    assert (st.train, st.too_long) == (1, 1)


@pytest.mark.parametrize(
    ("case", "msg"),
    [
        ("tampered", "sha256"),
        ("overlap", "評価セット"),
        ("bad_completion", "アクションの JSON"),
        ("mixed_versions", "版が混ざって"),
    ],
)
def test_prepare_refuses(tmp_path: Path, case: str, msg: str) -> None:
    srcs = [_source(tmp_path / "a", [_row("a", 0)])]
    if case == "tampered":
        with (srcs[0] / "train.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(_row("x", 0)) + "\n")
    elif case == "overlap":
        srcs = [
            _source(tmp_path / "b", [_row("b", 0)], eval_overlap={"files": ["tool_tasks_sample/INV-001.pdf"]})
        ]
    elif case == "bad_completion":
        srcs = [_source(tmp_path / "b", [{**_row("b", 0), "completion": "了解しました"}])]
    else:
        srcs.append(_source(tmp_path / "b", [_row("b", 0)], prompt_version="2.0"))
    with pytest.raises(PrepareError, match=msg):
        prepare(srcs, tmp_path / "out", val_pct=0)


def test_config_file_is_valid() -> None:
    for p in CONFIGS.glob("*.yaml"):
        cfg = load_config(p)
        assert cfg.max_seq_len >= 4096, p


def test_config_rejects_bad_values() -> None:
    base = yaml.safe_load((CONFIGS / "qlora-12b-tool.yaml").read_text(encoding="utf-8"))
    for bad in (
        {**base, "lora": {**base["lora"], "r": 7}},
        {**base, "train": {**base["train"], "learning_rate": 0.1}},
        {**base, "train": {**base["train"], "lr": 1e-4}},
        {**base, "unknown": 1},
        {k: v for k, v in base.items() if k != "base_model"},
    ):
        with pytest.raises(ConfigError):
            parse_config(bad)


def test_dry_run_plans_without_gpu_libraries(tmp_path: Path) -> None:
    src = _source(tmp_path / "src", [_row(f"ep{e}", s) for e in range(10) for s in range(4)])
    prepare([src], tmp_path / "data", val_pct=0)
    cfg = load_config(CONFIGS / "qlora-12b-tool.yaml")
    p = run(cfg, tmp_path / "data", dry_run=True)
    assert p["train_examples"] == 40 and p["optimizer_steps"] == 5  # 40 / (2×8) × 2 エポック → 切り上げ 5
    # 準備したときの max_seq_len が設定より大きければ止める
    prepare([src], tmp_path / "data2", val_pct=0, max_seq_len=100000)
    with pytest.raises(PrepareError, match="準備し直して"):
        run(cfg, tmp_path / "data2", dry_run=True)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = _source(tmp_path / "src", [_row("a", 0), _row("b", 1)])
    assert main(["prepare", str(src), "--out", str(tmp_path / "d"), "--val-pct", "0"]) == 0
    assert (
        main(
            [
                "train",
                "--config",
                str(CONFIGS / "qlora-12b-tool.yaml"),
                "--data",
                str(tmp_path / "d"),
                "--dry-run",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["dry_run"] is True
    assert main(["prepare", str(tmp_path / "missing"), "--out", str(tmp_path / "e")]) == 2
