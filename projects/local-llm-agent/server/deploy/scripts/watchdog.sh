#!/usr/bin/env bash
# ハングしたコンテナの再起動（OPS-02、server_setup.md §8.3）。llm-watchdog.timer から1分ごとに実行する。
#
# Docker は healthcheck が unhealthy になっても自動では再起動しない（restart: は「終了したとき」だけ）。
# ラベル llm.autoheal=true の付いたコンテナが unhealthy なら docker restart する。
# 同じコンテナを短い間に何度も再起動しない（既定 10 分に 1 回まで）。再起動の記録は journald と状態ファイルに残す。
set -euo pipefail

STATE_DIR=${WATCHDOG_STATE_DIR:-/srv/llm/data/watchdog}
COOLDOWN_S=${WATCHDOG_COOLDOWN_S:-600}
NOW=${WATCHDOG_NOW:-$(date +%s)}
TEXTFILE_DIR=${TEXTFILE_DIR:-/srv/llm/data/node-exporter}

mkdir -p "$STATE_DIR" "$TEXTFILE_DIR"
restarted=0
while read -r id name; do
  [ -n "$id" ] || continue
  last_file="$STATE_DIR/$name.last"
  last=$(cat "$last_file" 2>/dev/null || echo 0)
  if [ $((NOW - last)) -lt "$COOLDOWN_S" ]; then
    echo "[watchdog] $name は unhealthy ですが、直前に再起動したため待ちます"
    continue
  fi
  echo "[watchdog] $name が unhealthy のため再起動します"
  docker restart "$id" >/dev/null
  echo "$NOW" >"$last_file"
  count=$(($(cat "$STATE_DIR/$name.count" 2>/dev/null || echo 0) + 1))
  echo "$count" >"$STATE_DIR/$name.count"
  restarted=$((restarted + 1))
done < <(docker ps --filter "label=llm.autoheal=true" --filter "health=unhealthy" --format '{{.ID}} {{.Names}}')

# 監視用: コンテナごとの自動再起動の累計（アラート ContainerRestartedByWatchdog が見る）
{
  echo "# HELP llm_watchdog_restarts_total ハングで自動再起動した回数"
  echo "# TYPE llm_watchdog_restarts_total counter"
  for f in "$STATE_DIR"/*.count; do
    [ -e "$f" ] || continue
    echo "llm_watchdog_restarts_total{container=\"$(basename "$f" .count)\"} $(cat "$f")"
  done
} >"$TEXTFILE_DIR/watchdog.prom.part"
mv -f "$TEXTFILE_DIR/watchdog.prom.part" "$TEXTFILE_DIR/watchdog.prom"
[ "$restarted" -eq 0 ] || echo "[watchdog] $restarted 件を再起動しました"
