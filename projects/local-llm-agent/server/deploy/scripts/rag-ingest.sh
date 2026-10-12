#!/usr/bin/env bash
# 社内文書の夜間の取り込み（OPS-01、AGT-07）。llm-rag-ingest.timer から 1:00 に実行する（2:00 のバックアップより前）。
# 差分だけを取り込む（変わっていないファイルは飛ばす）。成功したら node-exporter の textfile に時刻と件数を書く。
set -euo pipefail

LLM_ROOT=${LLM_ROOT:-/srv/llm}
COMPOSE_DIR=${COMPOSE_DIR:-$LLM_ROOT/deploy}
TEXTFILE_DIR=${TEXTFILE_DIR:-$LLM_ROOT/data/node-exporter}

mkdir -p "$TEXTFILE_DIR"
start=$(date +%s)
out=$(docker compose --project-directory "$COMPOSE_DIR" run --rm -T rag llm-rag ingest --config /profiles/rag.yaml 2>&1) || {
  status=$?
  echo "$out" | tail -n 20 >&2
  echo "[rag-ingest] 失敗しました（終了コード $status）" >&2
  exit "$status"
}
echo "$out" | tail -n 5
# 取り込みの要約（JSON の1行）から件数を拾う。無ければ 0
changed=$(echo "$out" | grep -oE '"(added|updated)": ?[0-9]+' | grep -oE '[0-9]+$' | awk '{s+=$1} END {print s+0}')
{
  echo "# HELP llm_rag_ingest_last_success_timestamp_seconds 最後に社内文書の取り込みが成功した時刻"
  echo "# TYPE llm_rag_ingest_last_success_timestamp_seconds gauge"
  echo "llm_rag_ingest_last_success_timestamp_seconds $(date +%s)"
  echo "# HELP llm_rag_ingest_duration_seconds 取り込みにかかった時間"
  echo "# TYPE llm_rag_ingest_duration_seconds gauge"
  echo "llm_rag_ingest_duration_seconds $(($(date +%s) - start))"
  echo "# HELP llm_rag_ingest_changed_documents 前回から追加・更新した文書の数"
  echo "# TYPE llm_rag_ingest_changed_documents gauge"
  echo "llm_rag_ingest_changed_documents ${changed:-0}"
} >"$TEXTFILE_DIR/rag_ingest.prom.part"
mv -f "$TEXTFILE_DIR/rag_ingest.prom.part" "$TEXTFILE_DIR/rag_ingest.prom"
echo "[rag-ingest] 完了"
