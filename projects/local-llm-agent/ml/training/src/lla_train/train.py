"""QLoRA 学習（Unsloth + TRL）。学習本体は検証機（GPU）でだけ動く。

--dry-run では GPU 用のライブラリを読み込まずに、設定・データ・手順数の見積もりまでを確かめる。
学習の結果は output_dir/<name>/ に LoRA アダプタと train_meta.json（台帳に登録する値）を残す。
"""

from __future__ import annotations

import importlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lla_train.config import TrainConfig
from lla_train.prepare import PrepareError


def _read_jsonl(p: Path) -> list[dict[str, Any]]:
    if not p.exists():
        raise PrepareError(f"{p} がありません（先に prepare を実行してください）")
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def plan(cfg: TrainConfig, data: Path) -> dict[str, Any]:
    stats = json.loads((data / "stats.json").read_text(encoding="utf-8"))
    train = _read_jsonl(data / "train.jsonl")
    val = _read_jsonl(data / "val.jsonl")
    if stats.get("max_seq_len", 0) > cfg.max_seq_len:
        raise PrepareError(
            f"データは max_seq_len {stats['max_seq_len']} で準備されています。設定（{cfg.max_seq_len}）以下で準備し直してください"
        )
    per_step = cfg.train.batch_size * cfg.train.grad_accum
    steps = math.ceil(len(train) / per_step * cfg.train.epochs)
    return {
        "name": cfg.name,
        "base_model": cfg.base_model,
        "train_examples": len(train),
        "val_examples": len(val),
        "effective_batch": per_step,
        "optimizer_steps": steps,
        "data_hash": stats["data_hash"],
        "protocol_version": stats["protocol_version"],
        "prompt_version": stats["prompt_version"],
    }


def run(cfg: TrainConfig, data: Path, *, dry_run: bool = False) -> dict[str, Any]:
    p = plan(cfg, data)
    if dry_run:
        return {**p, "dry_run": True}
    # ここから下は検証機でだけ動く（requirements-gpu.txt）。型情報のないライブラリなので Any で扱う
    unsloth: Any = importlib.import_module("unsloth")
    datasets: Any = importlib.import_module("datasets")
    trl: Any = importlib.import_module("trl")

    model, tokenizer = unsloth.FastLanguageModel.from_pretrained(
        model_name=cfg.base_model, max_seq_length=cfg.max_seq_len, load_in_4bit=cfg.load_in_4bit
    )
    model = unsloth.FastLanguageModel.get_peft_model(
        model,
        r=cfg.lora.r,
        lora_alpha=cfg.lora.alpha,
        lora_dropout=cfg.lora.dropout,
        target_modules=list(cfg.lora.target_modules),
        use_gradient_checkpointing="unsloth",
        random_state=cfg.train.seed,
    )
    cols = ["prompt", "completion"]
    ds_train = datasets.load_dataset(
        "json", data_files=str(data / "train.jsonl"), split="train"
    ).select_columns(cols)
    ds_val = datasets.load_dataset("json", data_files=str(data / "val.jsonl"), split="train").select_columns(
        cols
    )
    out = Path(cfg.output_dir) / cfg.name
    t = cfg.train
    args = trl.SFTConfig(
        output_dir=str(out),
        max_length=cfg.max_seq_len,
        completion_only_loss=True,
        per_device_train_batch_size=t.batch_size,
        gradient_accumulation_steps=t.grad_accum,
        num_train_epochs=t.epochs,
        learning_rate=t.learning_rate,
        warmup_ratio=t.warmup_ratio,
        weight_decay=t.weight_decay,
        lr_scheduler_type="cosine",
        logging_steps=10,
        eval_strategy="steps" if len(ds_val) else "no",
        eval_steps=t.eval_steps,
        save_steps=t.save_steps,
        seed=t.seed,
        report_to="none",
    )
    trainer = trl.SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=ds_train,
        eval_dataset=ds_val or None,
        args=args,
    )
    result = trainer.train()
    model.save_pretrained(str(out / "adapter"))
    tokenizer.save_pretrained(str(out / "adapter"))
    meta = {
        **p,
        "finished_at": datetime.now(UTC).isoformat(),
        "train_loss": float(result.training_loss),
        "eval": trainer.evaluate() if len(ds_val) else None,
        "config": cfg.to_dict(),
    }
    (out / "train_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    return meta
