#!/bin/bash
# The durable nightly runner: the same loop the in-session cron drives, but owned by launchd so
# it survives this terminal closing, a logout and a reboot.
#
# `CronCreate` jobs live in one Claude session's memory, fire only while its REPL is idle, and
# expire after seven days. That is fine for tonight and wrong for "nightly". This is the version
# that keeps working.
#
# Install:   launchctl load -w ~/Library/LaunchAgents/com.vibe.improve.plist
# Uninstall: launchctl unload -w ~/Library/LaunchAgents/com.vibe.improve.plist
set -uo pipefail

ROOT="${VIBE_IMPROVE_ROOT:-/Users/joeliu/Downloads/vibe}"
# Point cargo outside the project tree. Under launchd the default would work, but this script is
# also run by hand, and from a sandboxed shell a build script writing under `target/` gets EPERM
# -- which `evaluate.py` correctly calls BLOCKED, and a night of BLOCKED is a wasted night.
export VIBE_IMPROVE_TARGET_DIR="${VIBE_IMPROVE_TARGET_DIR:-${TMPDIR:-/tmp}/vibe-improve-target}"
CLAUDE="${VIBE_IMPROVE_CLAUDE:-$HOME/.local/bin/claude}"
DEADLINE_HOUR="${VIBE_IMPROVE_DEADLINE:-5}"
LOG_DIR="$HOME/Library/Logs/VibeImprove"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date +%Y-%m-%d).log"
exec >>"$LOG" 2>&1

say() { echo "[$(date '+%H:%M:%S')] $*"; }

say "night starting in $ROOT"
cd "$ROOT" || { say "FATAL: $ROOT is not there"; exit 1; }
[ -x "$CLAUDE" ] || { say "FATAL: no claude CLI at $CLAUDE"; exit 1; }

cycle=0
while :; do
  # The deadline is checked first so an overrunning cycle cannot start another one.
  if [ "$(date +%-H)" -ge "$DEADLINE_HOUR" ] && [ "$(date +%-H)" -lt 12 ]; then
    say "past ${DEADLINE_HOUR}:00, stopping"
    break
  fi
  cycle=$((cycle + 1))
  [ "$cycle" -gt 40 ] && { say "40 cycles, stopping"; break; }

  # night.py owns every refusal: dirty tree, tripped breaker, nothing startable.
  uv run scripts/improve/night.py begin > "$LOG_DIR/begin.txt" 2>&1
  rc=$?
  cat "$LOG_DIR/begin.txt"
  if [ "$rc" -ne 0 ]; then
    say "night.py begin refused (rc=$rc); stopping"
    break
  fi

  say "cycle $cycle: handing to claude"
  # --print is one-shot, which is why this loop is bash rather than one long session: each cycle
  # starts with a clean context and the backlog as its only memory.
  "$CLAUDE" --print "$(cat <<'PROMPT'
You are one cycle of an unattended improvement run in /Users/joeliu/Downloads/vibe.

`scripts/improve/night.py begin` has already run and opened the branch. Run it again to see the
item it picked and the playbook it routes to; read that playbook and follow it.

Do that one item. Then gate it with `uv run scripts/improve/evaluate.py`. ACCEPT (exit 0) means
commit on the branch. REJECT (1) or BLOCKED (2) means `git checkout -- .` and keep nothing.

Record the outcome either way:
  uv run scripts/improve/night.py finish --item <id> --verdict ACCEPT|REJECT|BLOCKED --note "<one line>"

Never push. Never merge. Never leave the branch. Never touch desktop/src-tauri/binaries/,
i18n/desktop/en-US.json, plans/, or the release workflows. Only the item route.py offered.
PROMPT
)" 2>&1 | tail -40
  say "cycle $cycle done"
done

uv run scripts/improve/night.py report
say "night finished; report at plans/improve/report.md"
