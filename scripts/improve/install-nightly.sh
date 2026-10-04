#!/bin/bash
# Arm (or disarm) the durable nightly improvement run.
#
# The plist beside this script is a template: it carries no username and no absolute checkout
# path, so it is safe in a public repository and works in a clone anywhere. This fills it in from
# wherever it is run and hands it to launchd.
#
# Loading it lets an agent edit this repository while nobody is watching. `night.py` holds the
# rules that run under -- branch only, revert on a failed gate, stop after two cycles without an
# ACCEPT, never push -- but the decision to arm it is still a person's.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LABEL="com.vibe.improve"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$ROOT/scripts/improve/$LABEL.plist.template"

if [ "${1:-}" = "--remove" ]; then
  launchctl unload -w "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL"
  launchctl list | grep -q "$LABEL" && { echo "WARNING: still loaded"; exit 1; } || true
  echo "not loaded. Confirmed."
  exit 0
fi

[ -f "$TEMPLATE" ] || { echo "FATAL: no template at $TEMPLATE"; exit 1; }

CLAUDE="${VIBE_IMPROVE_CLAUDE:-$(command -v claude || true)}"
[ -x "$CLAUDE" ] || { echo "FATAL: no claude CLI found. Set VIBE_IMPROVE_CLAUDE to its path."; exit 1; }
CLAUDE_DIR="$(dirname "$CLAUDE")"

# Refuse to clobber a different agent that happens to share the label.
if [ -f "$PLIST" ] && ! grep -q "$ROOT" "$PLIST"; then
  echo "FATAL: $PLIST exists and points somewhere else. Inspect it before overwriting."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/VibeImprove"
sed -e "s#__ROOT__#$ROOT#g" \
    -e "s#__HOME__#$HOME#g" \
    -e "s#__CLAUDE_DIR__#$CLAUDE_DIR#g" \
    -e "s#__CLAUDE__#$CLAUDE#g" \
    "$TEMPLATE" > "$PLIST"

plutil -lint "$PLIST" >/dev/null || { echo "FATAL: generated plist is malformed"; rm -f "$PLIST"; exit 1; }
if grep -q "__[A-Z_]*__" "$PLIST"; then
  echo "FATAL: unsubstituted placeholders remain:"; grep -o "__[A-Z_]*__" "$PLIST" | sort -u
  rm -f "$PLIST"; exit 1
fi

launchctl unload -w "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

# A load that silently did nothing is the failure worth catching here.
if launchctl list | grep -q "$LABEL"; then
  echo "armed: $LABEL"
  echo "  root:    $ROOT"
  echo "  claude:  $CLAUDE"
  echo "  fires:   23:07 nightly"
  echo "  logs:    $HOME/Library/Logs/VibeImprove/"
  echo "  disarm:  $0 --remove"
else
  echo "FATAL: launchctl load reported success but the job is not listed."
  exit 1
fi
