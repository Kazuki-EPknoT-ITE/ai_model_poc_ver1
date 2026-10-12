"""server/deploy/scripts/backup.sh の試験（OPS-04）。docker・age・rsync を差し替えた bash で実行する。

Linux か Git Bash（Windows）で動く。Windows で WSL の bash しか無い場合は飛ばす（パスの形式が違うため）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"

STUB_DOCKER = r"""#!/usr/bin/env bash
# docker compose ... exec -T <service> <cmd...> だけを模擬する
args="$*"
if [[ "$args" == *"pg_dump"* ]]; then
  [ -n "${FAKE_PG_EMPTY:-}" ] && exit 0
  printf 'PGDMP-FAKE-%s' "$BACKUP_DATE"
elif [[ "$args" == *"exec -T rag python"* ]]; then
  cat >/dev/null
  for c in docs_default docs_keiri; do
    mkdir -p "$LLM_ROOT/data/qdrant/snapshots/$c"
    printf 'SNAP' >"$LLM_ROOT/data/qdrant/snapshots/$c/$c-$BACKUP_DATE.snapshot"
  done
  echo "[\"docs_default/docs_default-$BACKUP_DATE.snapshot\", \"docs_keiri/docs_keiri-$BACKUP_DATE.snapshot\"]"
else
  echo "unexpected docker call: $args" >&2
  exit 9
fi
"""

STUB_AGE = r"""#!/usr/bin/env bash
# age -R <recipients> -o <out> : 中身の前に印を付けて書くだけ
out=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out=$2; shift 2 ;; -R) [ -s "$2" ] || exit 7; shift 2 ;; *) shift ;; esac
done
{ printf 'AGE-ENCRYPTED\n'; cat; } >"$out"
"""

STUB_RSYNC = r"""#!/usr/bin/env bash
# rsync -a src/ dst/
src=${@: -2:1}; dst=${@: -1}
mkdir -p "$dst" && cp -r "$src". "$dst"
"""


def _bash() -> str | None:
    bash = shutil.which("bash")
    if bash is None:
        return None
    ostype = subprocess.run([bash, "-c", "echo $OSTYPE"], capture_output=True, text=True, check=False).stdout
    if os.name == "nt" and "msys" not in ostype and "cygwin" not in ostype:
        return None  # WSL の bash
    return bash


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash（Linux か Git Bash）がありません")


def _setup(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "srv"
    for d in ("deploy", "profiles", "secrets", "models/lora/v1", "data/qdrant/snapshots"):
        (root / d).mkdir(parents=True)
    (root / "deploy" / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (root / "secrets" / "backup-age-recipients.txt").write_text("age1examplepublickey\n", encoding="utf-8")
    (root / "secrets" / "gateway.env").write_text("SECRET=1\n", encoding="utf-8")
    (root / "models" / "lora" / "v1" / "adapter.safetensors").write_text("w", encoding="utf-8")
    dest = tmp_path / "nas"
    dest.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("docker", STUB_DOCKER), ("age", STUB_AGE), ("rsync", STUB_RSYNC)):
        p = bindir / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)
    path_sep = ":"  # bash の PATH
    bin_posix = _posix(bindir)
    return {
        "LLM_ROOT": _posix(root),
        "BACKUP_DEST": _posix(dest),
        "TEXTFILE_DIR": _posix(root / "data" / "node-exporter"),
        "PATH_PREFIX": bin_posix + path_sep,
    }


def _posix(p: Path) -> str:
    s = p.as_posix()
    if os.name == "nt" and len(s) > 1 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:]  # Git Bash の形式（/c/Users/...）
    return s


def _run(env: dict[str, str], day: str, **extra: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    e = {**os.environ, **{k: v for k, v in env.items() if k != "PATH_PREFIX"}, "BACKUP_DATE": day, **extra}
    cmd = f'export PATH="{env["PATH_PREFIX"]}$PATH"; bash "{_posix(SCRIPT)}"'
    return subprocess.run(
        [BASH, "-c", cmd], env=e, capture_output=True, text=True, encoding="utf-8", check=False
    )


def test_backup_generations_and_outputs(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    dest = tmp_path / "nas"
    start = date(2026, 10, 1)
    for i in range(20):
        r = _run(env, (start + timedelta(days=i)).isoformat())
        assert r.returncode == 0, r.stderr + r.stdout
    daily = sorted(p.name for p in (dest / "postgres" / "daily").iterdir())
    assert len(daily) == 14 and daily[0] == "llmgw-2026-10-07.dump" and daily[-1] == "llmgw-2026-10-20.dump"
    assert (dest / "postgres" / "daily" / daily[-1]).read_text(encoding="utf-8") == "PGDMP-FAKE-2026-10-20"
    assert [p.name for p in (dest / "postgres" / "monthly").iterdir()] == ["llmgw-2026-10.dump"]
    qdirs = sorted(p.name for p in (dest / "qdrant").iterdir())
    assert len(qdirs) == 7 and qdirs[-1] == "2026-10-20"
    assert (dest / "qdrant" / "2026-10-20" / "docs_keiri" / "docs_keiri-2026-10-20.snapshot").exists()
    # スナップショットはサーバー側から回収（移動）されている
    assert not any((tmp_path / "srv" / "data" / "qdrant" / "snapshots").rglob("*.snapshot"))
    configs = sorted(p.name for p in (dest / "config").iterdir())
    assert len(configs) == 20 and configs[-1] == "config-2026-10-20.tar.gz.age"
    assert (dest / "config" / configs[-1]).read_bytes().startswith(b"AGE-ENCRYPTED")
    assert (dest / "lora" / "v1" / "adapter.safetensors").exists()
    prom = (tmp_path / "srv" / "data" / "node-exporter" / "backup.prom").read_text(encoding="utf-8")
    assert "llm_backup_last_success_timestamp_seconds " in prom
    assert (dest / "last-success").read_text(encoding="utf-8").startswith("2026-10-20 ")
    # 書きかけのファイルが残っていない
    assert not list(dest.rglob("*.part"))


def test_backup_refuses_when_nas_is_missing(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    r = _run({**env, "BACKUP_DEST": _posix(tmp_path / "not-mounted")}, "2026-10-01")
    assert r.returncode != 0 and "バックアップ先" in r.stderr


def test_backup_refuses_without_age_recipients(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    (tmp_path / "srv" / "secrets" / "backup-age-recipients.txt").unlink()
    r = _run(env, "2026-10-01")
    assert r.returncode != 0 and "age の公開鍵" in r.stderr


def test_empty_dump_fails_and_leaves_no_backup(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    r = _run(env, "2026-10-02", FAKE_PG_EMPTY="1")
    assert r.returncode != 0 and "pg_dump の出力が空" in r.stderr
    assert not (tmp_path / "nas" / "postgres" / "daily" / "llmgw-2026-10-02.dump").exists()
    assert not (tmp_path / "srv" / "data" / "node-exporter" / "backup.prom").exists()


def test_bad_date_is_rejected(tmp_path: Path) -> None:
    env = _setup(tmp_path)
    r = _run(env, "20261001")
    assert r.returncode != 0 and "YYYY-MM-DD" in r.stderr
