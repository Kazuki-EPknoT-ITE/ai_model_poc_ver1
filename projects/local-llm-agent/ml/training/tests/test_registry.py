from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from lla_train.__main__ import main
from lla_train.prepare import PrepareError
from lla_train.registry import post_entry, registry_entry

META = {
    "name": "tool-12b",
    "base_model": "/srv/llm/models/base/gemma-4-12b-it/abc",
    "data_hash": "0123456789abcdef" * 4,
    "protocol_version": "1.0",
    "prompt_version": "1.0",
}


def _run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "tool-12b"
    (d / "adapter").mkdir(parents=True)
    (d / "train_meta.json").write_text(json.dumps(META), encoding="utf-8")
    return d


def test_entry_from_train_meta(tmp_path: Path) -> None:
    e = registry_entry(_run_dir(tmp_path), slot="local-12b", quant="qat-int4")
    assert e["id"] == "tool-12b-01234567"
    assert e["train_data_hash"] == META["data_hash"]
    assert e["lora"] == "tool-12b" and e["lora_path"].endswith("adapter")
    assert e["base_model"] == META["base_model"] and e["slot"] == "local-12b"
    e2 = registry_entry(
        _run_dir(tmp_path / "x"), slot="s", quant="q", model_id="v2", lora_path="/srv/lora/v2"
    )
    assert (e2["id"], e2["lora_path"]) == ("v2", "/srv/lora/v2")


def test_entry_refuses_unfinished_or_bad_id(tmp_path: Path) -> None:
    with pytest.raises(PrepareError, match="train_meta.json"):
        registry_entry(tmp_path, slot="s", quant="q")
    d = _run_dir(tmp_path)
    with pytest.raises(PrepareError, match="ID"):
        registry_entry(d, slot="s", quant="q", model_id="bad id/..")
    (d / "adapter").rmdir()
    with pytest.raises(PrepareError, match="アダプタ"):
        registry_entry(d, slot="s", quant="q")


class _Stub(BaseHTTPRequestHandler):
    calls: list[tuple[str, dict[str, Any], str | None]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Stub.calls.append((self.path, body, self.headers.get("Authorization")))
        if self.path == "/v1/auth/login":
            ok = body["password"] == "pw"  # noqa: S105  試験用の仮の値
            self._send(200 if ok else 401, {"access_token": "tok"} if ok else {"error": "bad"})
        else:
            self._send(201, {**body, "status": "candidate"})

    def _send(self, code: int, obj: dict[str, Any]) -> None:
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def stub() -> Iterator[str]:
    _Stub.calls = []
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_post_logs_in_and_registers(tmp_path: Path, stub: str) -> None:
    e = registry_entry(_run_dir(tmp_path), slot="local-12b", quant="qat-int4")
    got = post_entry(stub, e, login="admin", password="pw")  # noqa: S106  試験用の仮の値
    assert got["status"] == "candidate"
    assert [c[0] for c in _Stub.calls] == ["/v1/auth/login", "/admin/v1/models"]
    assert _Stub.calls[1][1] == e and _Stub.calls[1][2] == "Bearer tok"
    with pytest.raises(PrepareError, match="HTTP 401"):
        post_entry(stub, e, login="admin", password="wrong")  # noqa: S106  試験用の仮の値


def test_post_needs_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "LLA_TRAIN_ADMIN_LOGIN",
        "LLA_TRAIN_ADMIN_PASSWORD",
        "LLA_EVAL_ADMIN_LOGIN",
        "LLA_EVAL_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(PrepareError, match="資格情報"):
        post_entry("http://127.0.0.1:9", {})
    with pytest.raises(PrepareError, match="http"):
        post_entry("file:///etc/passwd", {}, login="a", password="b")  # noqa: S106  試験用の仮の値


def test_cli_register(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = _run_dir(tmp_path)
    assert main(["register", "--run", str(d), "--slot", "local-12b", "--quant", "qat-int4"]) == 0
    assert (
        json.loads(capsys.readouterr().out.strip().splitlines()[-1])["train_data_hash"] == META["data_hash"]
    )
    assert main(["register", "--run", str(tmp_path / "none"), "--slot", "s", "--quant", "q"]) == 2
