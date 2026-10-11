#!/usr/bin/env bash
# rollback.sh — restore the repo (code + ref/LOG.md) to an earlier checkpoint.
#
# SAFE BY DESIGN: every rollback first saves the current HEAD as a recoverable
# backup tag (backup/<timestamp>), so any rollback can itself be undone with
# `--undo`. Nothing is ever permanently lost. Requires a clean working tree so
# uncommitted work is never silently discarded.
#
# Usage:
#   scripts/rollback.sh --list            # show restore points + backups
#   scripts/rollback.sh --last            # undo the most recent checkpoint
#   scripts/rollback.sh <commit|tag>      # restore to a specific checkpoint
#   scripts/rollback.sh --undo            # undo the last rollback (restore backup)
set -euo pipefail
cd "$(dirname "$0")/.."

git rev-parse --git-dir >/dev/null 2>&1 || { echo "ERROR: not a git repository."; exit 1; }

list() {
  echo "== Restore points (newest first) =="
  git log --oneline -20 --decorate
  echo
  echo "== Backups (states saved before past rollbacks; use with --undo or a name) =="
  git tag -l 'backup/*' | sort -r | head -20
  echo
  echo "Roll back with:  scripts/rollback.sh --last   |   scripts/rollback.sh <commit|tag>"
}

require_clean() {
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: you have uncommitted changes. Save or discard them first:"
    echo "  scripts/checkpoint.sh \"wip\"     # save as a checkpoint"
    echo "  git restore .                     # or discard them"
    exit 1
  fi
}

backup() {  # prints the backup tag name
  local tag; tag="backup/$(date '+%Y%m%d-%H%M%S')"
  git tag "$tag" HEAD
  printf '%s' "$tag"
}

do_reset() {  # $1 = target ref, $2 = human label
  local target="$1" label="$2" b
  git rev-parse --verify "${target}^{commit}" >/dev/null 2>&1 \
    || { echo "ERROR: unknown checkpoint '${target}'. Try: scripts/rollback.sh --list"; exit 1; }
  b="$(backup)"
  git reset --hard "$target" >/dev/null
  echo "rolled back ${label}."
  echo "  now at: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"
  echo "  code AND ref/LOG.md restored to that point."
  echo "  undo this rollback: scripts/rollback.sh --undo    (backup saved: ${b})"
}

case "${1:-}" in
  ""|--list|-l)
    list ;;
  --undo)
    require_clean
    last_backup="$(git tag -l 'backup/*' | sort -r | head -1)"
    [ -n "$last_backup" ] || { echo "no backup found to undo."; exit 1; }
    git reset --hard "$last_backup" >/dev/null
    echo "undid last rollback -> restored ${last_backup}"
    echo "  now at: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s)"
    ;;
  --last)
    require_clean
    git rev-parse --verify HEAD~1 >/dev/null 2>&1 \
      || { echo "no previous checkpoint to roll back to."; exit 1; }
    do_reset "HEAD~1" "the last checkpoint" ;;
  -*)
    echo "unknown option: $1"; echo; list; exit 1 ;;
  *)
    require_clean
    do_reset "$1" "to $1" ;;
esac
