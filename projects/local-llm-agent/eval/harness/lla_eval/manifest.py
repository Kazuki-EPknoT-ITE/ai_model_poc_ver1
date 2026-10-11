"""評価セット（eval/sets/<name>/）の読み込みと検証（local_llm_test_spec.md §3.1）。

- manifest.yaml の項目を検証する。誤りはまとめて ManifestError で返す。
- 実データはリポジトリに置かない。data_root は次のどれか:
    nas://<path>   … 社内 NAS（環境変数 LLA_EVAL_NAS_ROOT にマウント先を入れる）
    synthetic      … generator で合成する（合成データだけで回る試験用）
    fixtures[/…]   … セットのフォルダにある合成データ
- data_hash（items と参照データ全体のハッシュ）が一致しなければ実行しない。
- split: eval のセットは、学習データのハッシュ一覧と照合し、重複があれば実行しない。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

RUNNERS = ("agent", "tool", "rag", "bench", "fault_injection")
SCORERS = ("invoice_fields", "artifact_checks", "doc_quality", "resilience")
TARGET_RE = re.compile(r"^\s*(>=|<=|==|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")
NAS_ENV = "LLA_EVAL_NAS_ROOT"
TRAIN_ENV = "LLA_EVAL_TRAIN_HASHES"

# 採点器ごとに items.jsonl の各行が持つべき項目
ITEM_KEYS: dict[str, tuple[str, ...]] = {
    "invoice_fields": ("id", "file", "schema", "expected"),
    "artifact_checks": ("id", "request", "checks"),
    "doc_quality": ("id", "source", "ops"),
    "resilience": ("id", "cause", "automated"),
}


class ManifestError(Exception):
    """manifest・items・データの検証に失敗した（実行しない）。"""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class DataUnavailable(ManifestError):
    """実データの置き場（NAS）に接続できない。"""


@dataclass(frozen=True)
class Metric:
    name: str
    target: str

    def check(self, value: float | None) -> bool | None:
        """目標を満たすか。値がない（証拠がない）場合は None。"""
        if value is None:
            return None
        m = TARGET_RE.match(self.target)
        assert m is not None  # 読み込み時に検証済み
        op, rhs = m.group(1), float(m.group(2))
        return {
            ">=": value >= rhs,
            "<=": value <= rhs,
            "==": value == rhs,
            ">": value > rhs,
            "<": value < rhs,
        }[op]


@dataclass(frozen=True)
class Manifest:
    name: str
    version: int
    requirement_ids: list[str]
    data_root: str
    data_hash: str | None
    split: str
    runner: str
    scorer: str
    model_targets: list[str]
    metrics: list[Metric]
    timeout_per_item_s: int = 600
    repeat: int = 1
    seed: int = 42
    temperature: float = 0.0
    description: str = ""
    generator: dict[str, Any] | None = None
    train_hash_index: str | None = None
    mock: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["metrics"] = [asdict(m) for m in self.metrics]
        return d


def _req(raw: dict[str, Any], key: str, typ: type | tuple[type, ...], problems: list[str]) -> Any:
    if key not in raw:
        problems.append(f"{key} がありません")
        return None
    v = raw[key]
    if not isinstance(v, typ) or (isinstance(v, bool) and typ is int):
        problems.append(f"{key} の型が違います（{type(v).__name__}）")
        return None
    return v


def parse_manifest(raw: Any) -> Manifest:
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise ManifestError(["manifest.yaml の中身がマッピングではありません"])
    known = {
        "name",
        "version",
        "requirement_ids",
        "data_root",
        "data_hash",
        "split",
        "runner",
        "scorer",
        "model_targets",
        "metrics",
        "timeout_per_item_s",
        "repeat",
        "seed",
        "temperature",
        "description",
        "generator",
        "train_hash_index",
        "mock",
    }
    for k in raw:
        if k not in known:
            problems.append(f"未知の項目 {k}")
    name = _req(raw, "name", str, problems)
    version = _req(raw, "version", int, problems)
    req_ids = _req(raw, "requirement_ids", list, problems)
    data_root = _req(raw, "data_root", str, problems)
    split = _req(raw, "split", str, problems)
    runner = _req(raw, "runner", str, problems)
    scorer = _req(raw, "scorer", str, problems)
    metrics_raw = _req(raw, "metrics", list, problems)
    if name is not None and not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name):
        problems.append("name は英小文字・数字・_ で書きます")
    if version is not None and version < 1:
        problems.append("version は1以上です")
    if split is not None and split != "eval":
        problems.append(f"split は eval だけを受け付けます（{split}）。学習用のデータを評価に使わないため")
    if runner is not None and runner not in RUNNERS:
        problems.append(f"runner {runner} は使えません（{', '.join(RUNNERS)}）")
    if scorer is not None and scorer not in SCORERS:
        problems.append(f"scorer {scorer} は使えません（{', '.join(SCORERS)}）")
    if data_root is not None and not (
        data_root.startswith("nas://") or data_root == "synthetic" or data_root.split("/")[0] == "fixtures"
    ):
        problems.append(
            "data_root は nas://… / synthetic / fixtures のどれかです（実データをリポジトリに置かない）"
        )
    data_hash = raw.get("data_hash")
    if data_hash is not None and not (
        isinstance(data_hash, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", data_hash)
    ):
        problems.append("data_hash は sha256:<64桁> の形です")
    if data_root is not None and data_root.startswith("nas://") and data_hash is None:
        problems.append("NAS のデータには data_hash が必要です")
    if data_root == "synthetic" and not isinstance(raw.get("generator"), dict):
        problems.append("data_root: synthetic には generator が必要です")
    metrics: list[Metric] = []
    for i, m in enumerate(metrics_raw or []):
        if (
            not isinstance(m, dict)
            or not isinstance(m.get("name"), str)
            or not isinstance(m.get("target"), str)
        ):
            problems.append(f"metrics[{i}] は {{name, target}} です")
            continue
        if not TARGET_RE.match(m["target"]):
            problems.append(f"metrics[{i}].target {m['target']!r} は「>= 0.98」の形で書きます")
            continue
        metrics.append(Metric(m["name"], m["target"].strip()))
    model_targets = raw.get("model_targets", [])
    if not isinstance(model_targets, list) or not all(isinstance(x, str) for x in model_targets):
        problems.append("model_targets は文字列の配列です")
    for key, typ in (("timeout_per_item_s", int), ("repeat", int), ("seed", int)):
        if key in raw and (not isinstance(raw[key], typ) or isinstance(raw[key], bool) or raw[key] < 0):
            problems.append(f"{key} は0以上の整数です")
    if "repeat" in raw and isinstance(raw["repeat"], int) and raw["repeat"] < 1:
        problems.append("repeat は1以上です")
    temperature = raw.get("temperature", 0.0)
    if not isinstance(temperature, int | float) or isinstance(temperature, bool):
        problems.append("temperature は数値です")
    if problems:
        raise ManifestError(problems)
    return Manifest(
        name=name,
        version=version,
        requirement_ids=[str(x) for x in req_ids],
        data_root=data_root,
        data_hash=data_hash,
        split=split,
        runner=runner,
        scorer=scorer,
        model_targets=list(model_targets),
        metrics=metrics,
        timeout_per_item_s=int(raw.get("timeout_per_item_s", 600)),
        repeat=int(raw.get("repeat", 1)),
        seed=int(raw.get("seed", 42)),
        temperature=float(temperature),
        description=str(raw.get("description", "")),
        generator=raw.get("generator"),
        train_hash_index=raw.get("train_hash_index"),
        mock=dict(raw.get("mock") or {}),
    )


def load_items(path: Path, scorer: str) -> list[dict[str, Any]]:
    problems: list[str] = []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not path.exists():
        raise ManifestError([f"{path.name} がありません"])
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"items.jsonl {n}行目が JSON ではありません: {e.msg}")
            continue
        if not isinstance(obj, dict):
            problems.append(f"items.jsonl {n}行目がオブジェクトではありません")
            continue
        for k in ITEM_KEYS[scorer]:
            if k not in obj:
                problems.append(f"items.jsonl {n}行目に {k} がありません")
        iid = obj.get("id")
        if isinstance(iid, str):
            if iid in seen:
                problems.append(f"id {iid} が重複しています")
            seen.add(iid)
        items.append(obj)
    if not items and not problems:
        problems.append("items.jsonl が空です")
    if problems:
        raise ManifestError(problems)
    return items


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def data_files(root: Path) -> list[Path]:
    """参照データのファイル（隠しファイル・作業用の印は除く）。フォルダが無ければ空。"""
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))


def compute_data_hash(root: Path, items_path: Path) -> str:
    """items.jsonl の中身と、参照データ全体（相対パス＋各ファイルのハッシュ）から求める。"""
    h = hashlib.sha256()
    h.update(b"items\0")
    h.update(items_path.read_bytes().replace(b"\r\n", b"\n"))
    for p in data_files(root):
        rel = p.relative_to(root).as_posix()
        h.update(f"\0{rel}\0{file_sha256(p)}".encode())
    return "sha256:" + h.hexdigest()


def resolve_data_root(m: Manifest, set_dir: Path, cache_dir: Path) -> Path:
    if m.data_root.startswith("nas://"):
        base = os.environ.get(NAS_ENV)
        if not base:
            raise DataUnavailable(
                [
                    f"{m.data_root} は社内 NAS のデータです。{NAS_ENV} にマウント先を設定した端末で実行してください"
                ]
            )
        root = Path(base) / m.data_root.removeprefix("nas://")
        if not root.is_dir():
            raise DataUnavailable([f"NAS のフォルダがありません: {root}"])
        return root
    if m.data_root == "synthetic":
        from .synth import generate  # noqa: PLC0415  合成は必要なときだけ読み込む

        assert m.generator is not None
        root = cache_dir / f"{m.name}-v{m.version}"
        marker = root / ".complete"
        if not marker.exists():
            generate(m.generator, root)
            marker.write_text("ok", encoding="utf-8")
        return root
    return set_dir / m.data_root


def _read_hash_index(spec: str, set_dir: Path) -> set[str]:
    if spec.startswith("nas://"):
        base = os.environ.get(NAS_ENV)
        if not base:
            raise DataUnavailable([f"学習データのハッシュ一覧 {spec} を読むには {NAS_ENV} が必要です"])
        p = Path(base) / spec.removeprefix("nas://")
    else:
        p = Path(spec) if Path(spec).is_absolute() else set_dir / spec
    if not p.exists():
        raise ManifestError([f"学習データのハッシュ一覧がありません: {p}"])
    out: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip().removeprefix("sha256:")
        if s and not s.startswith("#"):
            out.add(s.split()[0].lower())
    return out


def split_check(m: Manifest, set_dir: Path, root: Path) -> dict[str, Any]:
    """評価データと学習データの重複を、ファイルのハッシュで照合する（§3.1、§8.1）。"""
    spec = m.train_hash_index or os.environ.get(TRAIN_ENV)
    if not spec:
        return {"status": "not_configured", "overlap": [], "checked_files": 0}
    train = _read_hash_index(spec, set_dir)
    files = data_files(root)
    overlap = [p.relative_to(root).as_posix() for p in files if file_sha256(p) in train]
    if overlap:
        raise ManifestError(
            [f"評価データが学習データと重複しています（{len(overlap)}件）: {', '.join(overlap[:5])}"]
        )
    return {"status": "ok", "overlap": [], "checked_files": len(files), "train_hashes": len(train)}


@dataclass
class LoadedSet:
    set_dir: Path
    manifest: Manifest
    items: list[dict[str, Any]]
    data_root: Path
    data_hash: str
    split: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "set_dir": str(self.set_dir),
            "manifest": self.manifest.to_json(),
            "items": self.items,
            "data_root": str(self.data_root),
            "data_hash": self.data_hash,
            "split_check": self.split,
        }


def load_set(set_dir: Path, cache_dir: Path, *, verify_hash: bool = True) -> LoadedSet:
    mpath = set_dir / "manifest.yaml"
    if not mpath.exists():
        raise ManifestError([f"{mpath} がありません"])
    m = parse_manifest(yaml.safe_load(mpath.read_text(encoding="utf-8")))
    if m.name != set_dir.name and not set_dir.name.startswith(m.name):
        raise ManifestError([f"manifest の name（{m.name}）とフォルダ名（{set_dir.name}）が合いません"])
    items_path = set_dir / "items.jsonl"
    items = load_items(items_path, m.scorer)
    root = resolve_data_root(m, set_dir, cache_dir)
    actual = compute_data_hash(root, items_path)
    if verify_hash and m.data_hash is not None and actual != m.data_hash:
        raise ManifestError(
            [
                f"data_hash が一致しません（manifest: {m.data_hash[:19]}…, 実際: {actual[:19]}…）。版を上げて登録し直してください"
            ]
        )
    sc = split_check(m, set_dir, root)
    return LoadedSet(set_dir, m, items, root, actual, sc)
