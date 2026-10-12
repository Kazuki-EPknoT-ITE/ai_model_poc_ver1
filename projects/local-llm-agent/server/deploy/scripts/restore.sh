#!/usr/bin/env bash
# バックアップからの復元（OPS-04。月に1回、予備機で訓練する。server_setup.md §8.2）。
#
#   restore.sh postgres <llmgw-YYYY-MM-DD.dump>          DB を置き換える（gateway を止めてから）
#   restore.sh qdrant   <バックアップの日付フォルダ>        コレクションごとにスナップショットから戻す
#   restore.sh config   <config-YYYY-MM-DD.tar.gz.age> <age の秘密鍵ファイル> <展開先>
#                                                         設定を展開する（上書きはしない。差分を見て手で戻す）
#
# 環境変数 LLM_ROOT / COMPOSE_DIR / PG_USER / PG_DB で場所を変えられる。
set -euo pipefail

LLM_ROOT=${LLM_ROOT:-/srv/llm}
COMPOSE_DIR=${COMPOSE_DIR:-$LLM_ROOT/deploy}
PG_USER=${PG_USER:-llmgw}
PG_DB=${PG_DB:-llmgw}

die() {
  echo "[restore] 失敗: $*" >&2
  exit 1
}
compose() { docker compose --project-directory "$COMPOSE_DIR" "$@"; }

cmd=${1:-}
case "$cmd" in
postgres)
  dump=${2:?ダンプファイルを指定してください}
  [ -s "$dump" ] || die "$dump がありません"
  if [ -n "$(compose ps --status running -q gateway 2>/dev/null)" ]; then
    die "gateway が動いています。先に docker compose stop gateway を実行してください"
  fi
  compose exec -T postgres pg_restore -U "$PG_USER" -d "$PG_DB" --clean --if-exists --no-owner <"$dump"
  echo "[restore] PostgreSQL を戻しました。docker compose start gateway で再開してください"
  ;;
qdrant)
  day=${2:?バックアップの日付フォルダ（…/qdrant/YYYY-MM-DD）を指定してください}
  [ -d "$day" ] || die "$day がありません"
  dest="$LLM_ROOT/data/qdrant/snapshots"
  for f in "$day"/*/*; do
    [ -f "$f" ] || continue
    c=$(basename "$(dirname "$f")")
    mkdir -p "$dest/$c"
    cp -f "$f" "$dest/$c/"
    name=$(basename "$f")
    compose exec -T rag python - "$c" "$name" <<'PY'
import json, sys, urllib.request
c, name = sys.argv[1], sys.argv[2]
body = json.dumps({"location": f"file:///qdrant/storage/snapshots/{c}/{name}", "priority": "snapshot"}).encode()
req = urllib.request.Request(f"http://qdrant:6333/collections/{c}/snapshots/recover?wait=true", data=body, method="PUT", headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=3600) as r:
    print(c, json.load(r)["status"])
PY
  done
  echo "[restore] Qdrant を戻しました"
  ;;
config)
  file=${2:?暗号化した設定ファイルを指定してください}
  key=${3:?age の秘密鍵ファイルを指定してください}
  out=${4:?展開先のフォルダを指定してください}
  [ -e "$out" ] && [ -n "$(ls -A "$out" 2>/dev/null)" ] && die "$out は空ではありません（上書きしないため、空のフォルダを指定してください）"
  mkdir -p "$out"
  age -d -i "$key" "$file" | tar -C "$out" -xzf -
  chmod -R go-rwx "$out"
  echo "[restore] $out に展開しました（secrets を含みます。確認したら削除してください）"
  ;;
*)
  sed -n '2,9p' "$0"
  exit 2
  ;;
esac
