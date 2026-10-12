#!/usr/bin/env bash
# 毎晩のバックアップ（OPS-04、server_setup.md §8.2）。systemd の llm-backup.timer から 2:00 に実行する。
#
#   対象                     方法                                   保持
#   PostgreSQL               pg_dump -Fc（コンテナ内）               日次14世代・月次12世代
#   Qdrant                   コレクションごとのスナップショット API   日次7世代
#   LoRA（モデル台帳の版）    rsync（削除しない）                     全版
#   設定・compose・secrets   tar → age で暗号化（公開鍵だけ置く）     日次30世代
#
# 成功したら node-exporter の textfile に最終成功時刻を書く（アラート LlmBackupStale が見る）。
# 環境変数で場所とコマンドを差し替えられる（試験: server/deploy/tests/test_backup.py）。
set -euo pipefail

LLM_ROOT=${LLM_ROOT:-/srv/llm}
DEST=${BACKUP_DEST:-/mnt/nas-backup}
COMPOSE_DIR=${COMPOSE_DIR:-$LLM_ROOT/deploy}
AGE_RECIPIENTS=${AGE_RECIPIENTS:-$LLM_ROOT/secrets/backup-age-recipients.txt}
TEXTFILE_DIR=${TEXTFILE_DIR:-$LLM_ROOT/data/node-exporter}
TODAY=${BACKUP_DATE:-$(date +%F)}
PG_USER=${PG_USER:-llmgw}
PG_DB=${PG_DB:-llmgw}
KEEP_PG_DAILY=${KEEP_PG_DAILY:-14}
KEEP_PG_MONTHLY=${KEEP_PG_MONTHLY:-12}
KEEP_QDRANT=${KEEP_QDRANT:-7}
KEEP_CONFIG=${KEEP_CONFIG:-30}

log() { echo "[backup $(date +%T)] $*"; }
die() {
  echo "[backup] 失敗: $*" >&2
  exit 1
}
compose() { docker compose --project-directory "$COMPOSE_DIR" "$@"; }

# 名前順（日付入り）に並べて、新しい方から keep 件を残す
prune() {
  local dir=$1 pattern=$2 keep=$3
  [ -d "$dir" ] || return 0
  local files
  mapfile -t files < <(find "$dir" -maxdepth 1 -type f -name "$pattern" -printf '%f\n' | sort)
  local n=${#files[@]}
  if [ "$n" -gt "$keep" ]; then
    for f in "${files[@]:0:$((n - keep))}"; do
      rm -f -- "$dir/$f"
      log "古い世代を削除: $f"
    done
  fi
}

[[ "$TODAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "BACKUP_DATE は YYYY-MM-DD です: $TODAY"
[ -d "$DEST" ] && [ -w "$DEST" ] || die "バックアップ先 $DEST に書けません（NAS がマウントされているか確認してください）"
[ -s "$AGE_RECIPIENTS" ] || die "age の公開鍵 $AGE_RECIPIENTS がありません（秘密鍵はサーバーに置かない）"

# ---- 1. PostgreSQL ----
mkdir -p "$DEST/postgres/daily" "$DEST/postgres/monthly"
tmp="$DEST/postgres/daily/.llmgw-$TODAY.dump.part"
compose exec -T postgres pg_dump -U "$PG_USER" -Fc "$PG_DB" >"$tmp"
[ -s "$tmp" ] || die "pg_dump の出力が空です"
mv -f "$tmp" "$DEST/postgres/daily/llmgw-$TODAY.dump"
if [ "${TODAY:8:2}" = "01" ]; then
  cp -f "$DEST/postgres/daily/llmgw-$TODAY.dump" "$DEST/postgres/monthly/llmgw-${TODAY:0:7}.dump"
fi
prune "$DEST/postgres/daily" 'llmgw-*.dump' "$KEEP_PG_DAILY"
prune "$DEST/postgres/monthly" 'llmgw-*.dump' "$KEEP_PG_MONTHLY"
log "PostgreSQL: llmgw-$TODAY.dump"

# ---- 2. Qdrant（rag コンテナから内部ネットワークで API を呼ぶ。スナップショットは /srv/llm/data/qdrant/snapshots に出る）----
mkdir -p "$DEST/qdrant/$TODAY.part"
snapshots=$(compose exec -T rag python - <<'PY'
import json, urllib.request
base = "http://qdrant:6333"
def call(method, path):
    req = urllib.request.Request(base + path, method=method)
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["result"]
out = []
for c in call("GET", "/collections")["collections"]:
    snap = call("POST", f"/collections/{c['name']}/snapshots?wait=true")
    out.append(f"{c['name']}/{snap['name']}")
print(json.dumps(out))
PY
)
for rel in $(echo "$snapshots" | tr -d '[]",' ); do
  src="$LLM_ROOT/data/qdrant/snapshots/$rel"
  [ -s "$src" ] || die "Qdrant のスナップショットが見つかりません: $src"
  mkdir -p "$DEST/qdrant/$TODAY.part/$(dirname "$rel")"
  mv -f "$src" "$DEST/qdrant/$TODAY.part/$rel"
done
rm -rf "$DEST/qdrant/$TODAY"
mv "$DEST/qdrant/$TODAY.part" "$DEST/qdrant/$TODAY"
# 世代はフォルダ単位で数える
mapfile -t qdirs < <(find "$DEST/qdrant" -mindepth 1 -maxdepth 1 -type d -regex '.*/[0-9-]*' -printf '%f\n' | sort)
if [ "${#qdirs[@]}" -gt "$KEEP_QDRANT" ]; then
  for d in "${qdirs[@]:0:$((${#qdirs[@]} - KEEP_QDRANT))}"; do rm -rf -- "${DEST:?}/qdrant/$d"; done
fi
log "Qdrant: $(echo "$snapshots" | tr -cd ',' | wc -c | awk '{print $1+1}') 件（$snapshots）"

# ---- 3. LoRA（全版を残す。削除はしない）----
if [ -d "$LLM_ROOT/models/lora" ]; then
  mkdir -p "$DEST/lora"
  rsync -a "$LLM_ROOT/models/lora/" "$DEST/lora/"
  log "LoRA: rsync 済み"
fi

# ---- 4. 設定・compose・secrets（age で暗号化。復号の秘密鍵は管理者がオフラインで保管）----
mkdir -p "$DEST/config"
tar -C "$LLM_ROOT" -czf - deploy profiles secrets |
  age -R "$AGE_RECIPIENTS" -o "$DEST/config/.config-$TODAY.tar.gz.age.part"
mv -f "$DEST/config/.config-$TODAY.tar.gz.age.part" "$DEST/config/config-$TODAY.tar.gz.age"
prune "$DEST/config" 'config-*.tar.gz.age' "$KEEP_CONFIG"
log "設定: config-$TODAY.tar.gz.age"

# ---- 5. 成功の記録（監視用）----
mkdir -p "$TEXTFILE_DIR"
now=$(date +%s)
printf '# HELP llm_backup_last_success_timestamp_seconds 最後にバックアップが成功した時刻\n# TYPE llm_backup_last_success_timestamp_seconds gauge\nllm_backup_last_success_timestamp_seconds %s\n' "$now" \
  >"$TEXTFILE_DIR/backup.prom.part"
mv -f "$TEXTFILE_DIR/backup.prom.part" "$TEXTFILE_DIR/backup.prom"
echo "$TODAY $(date -Is)" >"$DEST/last-success"
log "完了"
