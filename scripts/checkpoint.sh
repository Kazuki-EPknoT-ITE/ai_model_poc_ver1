#!/usr/bin/env bash
# checkpoint.sh — save the current state (code + ref/LOG.md) as a restore point.
#
# Each checkpoint is one git commit, so it is a point in time you can roll back
# to later with scripts/rollback.sh. Checkpoints stay LOCAL (not pushed); push
# explicitly when a state is stable.
#
# Usage: scripts/checkpoint.sh "short summary of what changed"
set -euo pipefail
cd "$(dirname "$0")/.."

msg="${1:-manual checkpoint}"
ts="$(date '+%Y-%m-%d %H:%M:%S')"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: not a git repository. Run 'git init' first."
  exit 1
fi

# Nothing changed? Then there is nothing to checkpoint.
if git diff --quiet && git diff --cached --quiet \
   && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  echo "checkpoint: nothing to save (working tree clean)"
  exit 0
fi

git add -A
git commit -q -m "checkpoint: ${msg}" -m "ts: ${ts}"
echo "checkpoint saved: $(git rev-parse --short HEAD)  ${msg}"
echo "  (local restore point — roll back with: scripts/rollback.sh --list)"
