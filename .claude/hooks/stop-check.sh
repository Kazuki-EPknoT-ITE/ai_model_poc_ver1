#!/usr/bin/env bash
# Stop hook for agency-agents.
# If any project source file was modified during THIS session (newer than the
# session marker) AND ref/LOG.md was NOT updated afterwards, block the stop and
# require the post-implementation protocol: run E2E (Playwright) + write a
# hand-off log entry. Writing ref/LOG.md last clears the check (self-resetting,
# no loop). Pure Q&A turns (no source change) pass through untouched.
#
# Development happens under projects/<app>/. If the change is inside a project,
# the block message names that project and asks for a browser E2E on it.
set -u

SCRIPT="${BASH_SOURCE[0]}"
DIR="$(cd "$(dirname "$SCRIPT")" >/dev/null 2>&1 && pwd)"
ROOT="$(cd "$DIR/../.." >/dev/null 2>&1 && pwd)"

LOG="$ROOT/ref/LOG.md"
MARKER="$ROOT/.claude/.session-start"

# Can't evaluate without both anchors -> do not block.
[ -f "$LOG" ] || exit 0
[ -f "$MARKER" ] || exit 0

# Files changed this session (newer than MARKER) and after the last log update
# (newer than LOG). Excludes log/config/test-output/build/deps.
changed="$(find "$ROOT" -type f -newer "$LOG" -newer "$MARKER" \
  -not -path "*/ref/*" \
  -not -path "*/.claude/*" \
  -not -path "*/.git/*" \
  -not -path "*/.github/*" \
  -not -path "*/node_modules/*" \
  -not -path "*/playwright-report/*" \
  -not -path "*/test-results/*" \
  -not -path "*/.next/*" \
  -not -path "*/dist/*" \
  -not -path "*/build/*" \
  -not -path "*/.venv/*" \
  -not -path "*/__pycache__/*" \
  2>/dev/null)"

[ -z "$changed" ] && exit 0

# Which changed files live under projects/<name>/ ? (safe charset only)
proj="$(printf '%s\n' "$changed" \
  | sed -n 's#.*/projects/\([^/][^/]*\)/.*#\1#p' \
  | sort -u | tr -cd 'a-zA-Z0-9._, \n-' | paste -sd, - | sed 's/^,*//; s/,*$//')"

if [ -n "$proj" ]; then
  reason="Implementation detected in projects/[$proj] but not handed off. Per CLAUDE.md and ref/E2E_TESTING.md: (1) launch that project (its start command / base URL, see its e2e config) and run a Playwright browser E2E over the flow you changed, capturing pass/fail. (2) Append a hand-off entry to the TOP of ref/LOG.md including the E2E result. Write the ref/LOG.md entry LAST so this check clears."
else
  reason="Implementation detected this session but not handed off. Per CLAUDE.md and ref/E2E_TESTING.md: (1) if a runnable app is involved, run the Playwright browser E2E; otherwise record E2E as N/A with the reason and run the repo checks (scripts/lint-agents.sh etc.). (2) Append a hand-off entry to the TOP of ref/LOG.md including the E2E result. Write the ref/LOG.md entry LAST so this check clears."
fi

printf '{"decision":"block","reason":"%s"}' "$reason"
exit 0
