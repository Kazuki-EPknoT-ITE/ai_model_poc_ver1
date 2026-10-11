"""ペイロードの暗号化（AES-256-GCM。ADR-007, SEC-06）と秘密情報の読み込み。

暗号文の形式（自己記述）: b"\\x01" + len(key_id)[1B] + key_id + nonce[12B] + ciphertext+tag
- 暗号化のたびに新しい nonce を使う（同じ鍵で nonce を使い回さない）。
- AAD は `<job_id>|<用途>`。別のジョブ・別の列の暗号文に差し替えても復号できない。
- key_id を含めるので、鍵を更新しても古い鍵で暗号化したものを読める（鍵のローテーション）。
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings

_VERSION = 1
_NONCE_LEN = 12


class CryptoError(Exception):
    pass


@dataclass
class Sealed:
    key_id: str
    nonce: bytes
    blob: bytes


class PayloadCipher:
    def __init__(self, keys: dict[str, bytes], active: str) -> None:
        if active not in keys:
            raise CryptoError("有効な鍵（active）が鍵の一覧にありません")
        for kid, k in keys.items():
            if len(k) != 32:
                raise CryptoError(f"鍵 {kid} は 32 バイト（AES-256）である必要があります")
            if not kid or len(kid.encode()) > 255:
                raise CryptoError("key_id が不正です")
        self._keys = {kid: AESGCM(k) for kid, k in keys.items()}
        self.active = active

    @property
    def key_ids(self) -> list[str]:
        return list(self._keys)

    def seal(self, job_id: str, label: str, plaintext: bytes) -> Sealed:
        nonce = os.urandom(_NONCE_LEN)
        kid = self.active.encode()
        ct = self._keys[self.active].encrypt(nonce, plaintext, _aad(job_id, label))
        blob = bytes([_VERSION, len(kid)]) + kid + nonce + ct
        return Sealed(self.active, nonce, blob)

    def encrypt(self, job_id: str, label: str, plaintext: bytes) -> bytes:
        return self.seal(job_id, label, plaintext).blob

    def decrypt(self, job_id: str, label: str, blob: bytes) -> bytes:
        if len(blob) < 2 or blob[0] != _VERSION:
            raise CryptoError("暗号文の形式が不正です")
        klen = blob[1]
        kid = blob[2 : 2 + klen].decode()
        nonce = blob[2 + klen : 2 + klen + _NONCE_LEN]
        ct = blob[2 + klen + _NONCE_LEN :]
        aes = self._keys.get(kid)
        if aes is None:
            raise CryptoError(f"鍵 {kid} がありません（鍵を失った可能性があります）")
        try:
            return aes.decrypt(nonce, ct, _aad(job_id, label))
        except Exception as e:  # InvalidTag
            raise CryptoError("復号できません（改ざん・鍵違い）") from e


def _aad(job_id: str, label: str) -> bytes:
    return f"{job_id}|{label}".encode()


# ---- 鍵の生成・読み込み ----


def generate_payload_keys(key_id: str = "k1") -> dict[str, object]:
    return {"active": key_id, "keys": {key_id: base64.b64encode(os.urandom(32)).decode()}}


def parse_payload_keys(text: str) -> PayloadCipher:
    data = json.loads(text)
    if not isinstance(data, dict) or "keys" not in data or "active" not in data:
        raise CryptoError('ペイロード鍵は {"active": "<id>", "keys": {"<id>": "<base64>"}} の形式です')
    keys = {str(k): base64.b64decode(str(v)) for k, v in dict(data["keys"]).items()}
    return PayloadCipher(keys, str(data["active"]))


def generate_ed25519_pem() -> bytes:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def parse_ed25519_pem(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise CryptoError("JWT の署名鍵は Ed25519 の秘密鍵（PEM, PKCS#8）である必要があります")
    return key


def _secret(env: str, file_env: str, file_setting: str | None, settings: Settings) -> bytes:
    """値の環境変数 → ファイルパスの環境変数 → 設定ファイルのパス の順に探す。"""
    if v := os.environ.get(env):
        return v.encode()
    path = os.environ.get(file_env) or (str(settings.resolve(file_setting)) if file_setting else None)
    if path is None:
        raise CryptoError(f"秘密情報がありません: 環境変数 {env} か {file_env} を設定してください")
    return Path(path).read_bytes()


def load_signing_key(settings: Settings) -> Ed25519PrivateKey:
    raw = _secret(
        "LLMGW_JWT_PRIVATE_KEY", "LLMGW_JWT_PRIVATE_KEY_FILE", settings.secrets.jwt_private_key_file, settings
    )
    return parse_ed25519_pem(raw)


def load_payload_cipher(settings: Settings) -> PayloadCipher:
    raw = _secret(
        "LLMGW_PAYLOAD_KEYS", "LLMGW_PAYLOAD_KEYS_FILE", settings.secrets.payload_keys_file, settings
    )
    return parse_payload_keys(raw.decode("utf-8"))
