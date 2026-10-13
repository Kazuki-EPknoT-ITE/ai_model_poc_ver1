"""学習の設定（configs/*.yaml）。読み込み時に値の範囲を確かめ、検証機で走らせてから設定の誤りに気づくことを防ぐ。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class LoraConfig:
    r: int = 16
    alpha: int = 32
    dropout: float = 0.0
    target_modules: tuple[str, ...] = (
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    )


@dataclass(frozen=True)
class TrainParams:
    learning_rate: float = 2e-4
    epochs: float = 2.0
    batch_size: int = 2
    grad_accum: int = 8
    warmup_ratio: float = 0.03
    weight_decay: float = 0.0
    seed: int = 3407
    eval_steps: int = 50
    save_steps: int = 200


@dataclass(frozen=True)
class TrainConfig:
    name: str
    base_model: str
    max_seq_len: int
    output_dir: str
    load_in_4bit: bool = True
    lora: LoraConfig = field(default_factory=LoraConfig)
    train: TrainParams = field(default_factory=TrainParams)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check(cond: bool, msg: str, problems: list[str]) -> None:
    if not cond:
        problems.append(msg)


def parse_config(raw: Any) -> TrainConfig:
    if not isinstance(raw, dict):
        raise ConfigError(["設定が YAML のマッピングではありません"])
    known = {"name", "base_model", "max_seq_len", "output_dir", "load_in_4bit", "lora", "train"}
    problems = [f"不明な項目: {k}" for k in raw if k not in known]
    lora_raw = raw.get("lora") or {}
    train_raw = raw.get("train") or {}
    for k in lora_raw:
        _check(k in LoraConfig.__dataclass_fields__, f"lora の不明な項目: {k}", problems)
    for k in train_raw:
        _check(k in TrainParams.__dataclass_fields__, f"train の不明な項目: {k}", problems)
    for k in ("name", "base_model", "max_seq_len", "output_dir"):
        _check(k in raw, f"{k} がありません", problems)
    if problems:
        raise ConfigError(problems)
    lora_args: dict[str, Any] = {k: tuple(v) if k == "target_modules" else v for k, v in lora_raw.items()}
    try:
        cfg = TrainConfig(
            name=str(raw["name"]),
            base_model=str(raw["base_model"]),
            max_seq_len=int(raw["max_seq_len"]),
            output_dir=str(raw["output_dir"]),
            load_in_4bit=bool(raw.get("load_in_4bit", True)),
            lora=LoraConfig(**lora_args),
            train=TrainParams(**train_raw),
        )
    except (TypeError, ValueError) as e:
        raise ConfigError([f"値の型が違います: {e}"]) from e
    t, lo = cfg.train, cfg.lora
    _check(512 <= cfg.max_seq_len <= 131072, "max_seq_len は 512〜131072", problems)
    _check(lo.r in (4, 8, 16, 32, 64, 128), "lora.r は 4/8/16/32/64/128 のどれか", problems)
    _check(lo.alpha > 0, "lora.alpha は正の数", problems)
    _check(0 <= lo.dropout < 1, "lora.dropout は 0 以上 1 未満", problems)
    _check(len(lo.target_modules) > 0, "lora.target_modules が空です", problems)
    _check(0 < t.learning_rate <= 1e-3, "train.learning_rate は 0 より大きく 1e-3 以下", problems)
    _check(0 < t.epochs <= 10, "train.epochs は 0 より大きく 10 以下", problems)
    _check(t.batch_size >= 1 and t.grad_accum >= 1, "train.batch_size と grad_accum は 1 以上", problems)
    _check(0 <= t.warmup_ratio < 0.5, "train.warmup_ratio は 0 以上 0.5 未満", problems)
    _check(t.eval_steps >= 1 and t.save_steps >= 1, "train.eval_steps と save_steps は 1 以上", problems)
    if problems:
        raise ConfigError(problems)
    return cfg


def load_config(path: Path) -> TrainConfig:
    return parse_config(yaml.safe_load(path.read_text(encoding="utf-8")))
