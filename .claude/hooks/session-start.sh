#!/usr/bin/env bash
# SessionStart hook for agency-agents.
# 1) Refresh a per-session marker used by stop-check.sh to detect work done this session.
# 2) Remind Claude to load the ref/ project rules and log before responding.
set -u

SCRIPT="${BASH_SOURCE[0]}"
DIR="$(cd "$(dirname "$SCRIPT")" >/dev/null 2>&1 && pwd)"
ROOT="$(cd "$DIR/../.." >/dev/null 2>&1 && pwd)"

touch "$ROOT/.claude/.session-start" 2>/dev/null || true

echo "[agency-agents] SESSION START: read ref/PROJECT_RULES.md and ref/LOG.md before responding (see CLAUDE.md). After each implementation, run the E2E test (ref/E2E_TESTING.md) and append a hand-off entry to ref/LOG.md."
