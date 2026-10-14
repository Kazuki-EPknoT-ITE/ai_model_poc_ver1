"""学習の結果（train_meta.json）を、モデル台帳（Gateway の /admin/v1/models、設計書 §8.4）の1行にする。

- train_data_hash には、prepare の data_hash（実際に学習に使った train.jsonl と val.jsonl のハッシュ）を入れる。
  datagen の manifest の sha256 は出所の照合用で、prepare の stats.json の sources に残っている。
- 登録した版は candidate になる。評価（pnpm eval --register）で旧版以上なら、管理画面か API で本番にする。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from lla_train.prepare import PrepareError

_ID = re.compile(r"^[A-Za-z0-9._+-]{1,120}$")


def registry_entry(
    run_dir: Path,
    *,
    slot: str,
    quant: str,
    model_id: str | None = None,
    lora_path: str | None = None,
) -> dict[str, Any]:
    meta_path = run_dir / "train_meta.json"
    if not meta_path.exists():
        raise PrepareError(f"{meta_path} がありません（学習が終わっていないか、--dry-run の結果です）")
    meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    if not (run_dir / "adapter").is_dir():
        raise PrepareError(f"{run_dir / 'adapter'} がありません（LoRA アダプタが保存されていません）")
    name = str(meta["name"])
    mid = model_id or f"{name}-{str(meta['data_hash'])[:8]}"
    if not _ID.match(mid):
        raise PrepareError(f"台帳の ID に使えない文字があります: {mid}")
    return {
        "id": mid,
        "slot": slot,
        "base_model": str(meta["base_model"]),
        "quant": quant,
        "lora": name,
        # 推論サーバーから見た LoRA の場所（既定は学習した場所。別の場所へ置くなら指定する）
        "lora_path": lora_path or str((run_dir / "adapter").resolve()),
        "train_data_hash": str(meta["data_hash"]),
        "protocol_version": str(meta["protocol_version"]),
    }


def post_entry(
    gateway: str, entry: dict[str, Any], *, login: str | None = None, password: str | None = None
) -> Any:
    login = login or os.environ.get("LLA_TRAIN_ADMIN_LOGIN") or os.environ.get("LLA_EVAL_ADMIN_LOGIN")
    password = (
        password or os.environ.get("LLA_TRAIN_ADMIN_PASSWORD") or os.environ.get("LLA_EVAL_ADMIN_PASSWORD")
    )
    if not login or not password:
        raise PrepareError(
            "台帳への登録には管理者の資格情報が必要です（LLA_TRAIN_ADMIN_LOGIN / LLA_TRAIN_ADMIN_PASSWORD）"
        )
    if not gateway.startswith(("http://", "https://")):
        raise PrepareError(f"Gateway の URL は http(s):// で指定してください: {gateway}")
    base = gateway.rstrip("/")
    token = _post(
        f"{base}/v1/auth/login", {"login": login, "password": password, "device_name": "lla-train"}
    )["access_token"]
    return _post(f"{base}/admin/v1/models", entry, token)


def _post(url: str, body: dict[str, Any], token: str | None = None) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8"), headers, method="POST")  # noqa: S310  http(s) だけ（post_entry で確認）
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310  管理者が指定した Gateway の URL
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise PrepareError(f"Gateway が受け付けませんでした: HTTP {e.code} {detail}") from e
    except urllib.error.URLError as e:
        raise PrepareError(f"Gateway に接続できません: {e.reason}") from e
