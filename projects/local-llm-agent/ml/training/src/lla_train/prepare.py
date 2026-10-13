"""ml/datagen の出力（train.jsonl + manifest.json）を、TRL の prompt-completion 形式に整える。

- prompt は Runtime が組み立てた messages そのもの、completion はモデルが返すべき1手（assistant）。
  損失は completion にだけかける（TRL の prompt-completion 形式の既定）。
- 検証用は手順例（episode）単位で分ける。同じ手順例の手が学習と検証の両方に入らないようにするため。
- 受け付けないもの: manifest の sha256 と train.jsonl が合わない、評価セットとの重複がある（eval_overlap）、
  completion がアクションの JSON でない。長すぎる例（推定トークン数 > max_seq_len）は落として数える。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Runtime と同じ目安（日本語は1トークンあたり約1.5文字。packages/agent-runtime の tokens 見積もり）
CHARS_PER_TOKEN = 1.5


class PrepareError(Exception):
    pass


@dataclass
class Stats:
    sources: list[dict[str, Any]] = field(default_factory=list)
    train: int = 0
    val: int = 0
    duplicates: int = 0
    too_long: int = 0
    by_template: Counter[str] = field(default_factory=Counter)
    max_tokens_est: int = 0


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    chars = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):  # 画像などの部品つき。文字の部分だけ数える
            chars += sum(len(str(p.get("text", ""))) for p in c if isinstance(p, dict))
    return int(chars / CHARS_PER_TOKEN) + 4 * len(messages)


def load_source(d: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_path, train_path = d / "manifest.json", d / "train.jsonl"
    if not manifest_path.exists() or not train_path.exists():
        raise PrepareError(
            f"{d} に manifest.json と train.jsonl がありません（ml/datagen の出力を指定してください）"
        )
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    body = train_path.read_bytes()
    if _sha256(body) != manifest.get("sha256"):
        raise PrepareError(
            f"{train_path} が manifest.json の sha256 と合いません（生成後に書き換えられた可能性）"
        )
    overlap = (manifest.get("eval_overlap") or {}).get("files") or []
    if overlap:
        raise PrepareError(
            f"{d} は評価セットと重複するファイルを使っています（{len(overlap)}件）。学習に使えません"
        )
    rows = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
    return manifest, rows


def _is_val(episode: str, val_pct: int) -> bool:
    return int(_sha256(episode.encode("utf-8"))[:8], 16) % 100 < val_pct


def prepare(sources: list[Path], out: Path, *, val_pct: int = 10, max_seq_len: int = 8192) -> Stats:
    if not 0 <= val_pct < 100:
        raise PrepareError("val_pct は 0〜99 で指定してください")
    st = Stats()
    seen: set[str] = set()
    versions: set[tuple[str, str]] = set()
    train_lines: list[str] = []
    val_lines: list[str] = []
    for d in sources:
        manifest, rows = load_source(d)
        versions.add((str(manifest.get("protocol_version")), str(manifest.get("prompt_version"))))
        st.sources.append(
            {
                "dir": d.name,
                "sha256": manifest.get("sha256"),
                "samples": len(rows),
                "protocol_version": manifest.get("protocol_version"),
                "prompt_version": manifest.get("prompt_version"),
            }
        )
        for i, r in enumerate(rows):
            try:
                action = json.loads(r["completion"])
            except (KeyError, json.JSONDecodeError) as e:
                raise PrepareError(
                    f"{d.name} の {i + 1} 行目: completion がアクションの JSON ではありません"
                ) from e
            if not isinstance(action, dict) or "type" not in action:
                raise PrepareError(f"{d.name} の {i + 1} 行目: completion に type がありません")
            prompt = r["messages"]
            completion = [{"role": "assistant", "content": r["completion"]}]
            key = _sha256(
                json.dumps([prompt, completion], ensure_ascii=False, sort_keys=True).encode("utf-8")
            )
            if key in seen:
                st.duplicates += 1
                continue
            seen.add(key)
            tokens = estimate_tokens(prompt + completion)
            if tokens > max_seq_len:
                st.too_long += 1
                continue
            st.max_tokens_est = max(st.max_tokens_est, tokens)
            line = json.dumps(
                {
                    "prompt": prompt,
                    "completion": completion,
                    "episode": r["episode"],
                    "template": r["template"],
                },
                ensure_ascii=False,
            )
            if _is_val(str(r["episode"]), val_pct):
                val_lines.append(line)
                st.val += 1
            else:
                train_lines.append(line)
                st.train += 1
                st.by_template[str(r["template"])] += 1
    if len(versions) > 1:
        # 雛形・形式の版が混ざると、推論時と違う入力で学習することになる
        raise PrepareError(f"プロトコル・プロンプトの版が混ざっています: {sorted(versions)}")
    if not train_lines:
        raise PrepareError("学習に使える例がありません")
    out.mkdir(parents=True, exist_ok=True)
    train_body = "".join(x + "\n" for x in train_lines).encode("utf-8")
    val_body = "".join(x + "\n" for x in val_lines).encode("utf-8")
    (out / "train.jsonl").write_bytes(train_body)
    (out / "val.jsonl").write_bytes(val_body)
    pv, prv = next(iter(versions))
    stats = {
        "sources": st.sources,
        "train": st.train,
        "val": st.val,
        "duplicates": st.duplicates,
        "too_long": st.too_long,
        "max_seq_len": max_seq_len,
        "max_tokens_est": st.max_tokens_est,
        "by_template": dict(sorted(st.by_template.items())),
        "protocol_version": pv,
        "prompt_version": prv,
        # モデル台帳の train_data_hash に登録する値（学習に実際に使った2ファイル）
        "data_hash": _sha256(train_body + b"\0" + val_body),
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return st
