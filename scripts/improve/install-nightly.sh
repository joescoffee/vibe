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

# ── Preflight: can a launchd job actually reach this checkout? ──
#
# macOS TCC protects ~/Downloads, ~/Documents and ~/Desktop. A LaunchAgent running /bin/bash has
# no grant for any of them, so a repository in one of those folders is unreachable to it -- the
# job fires, gets `Operation not permitted` and exits 126, nightly, silently.
#
# This is measured rather than guessed: a path-prefix check would miss a symlink, a different
# protected location, or a future macOS that protects somewhere else. The probe is the same thing
# the real job does, so a pass means the real job can run.
#
# The first version of this installer checked `launchctl list` and called that armed. The job was
# listed and could do nothing. "Installed" is not "works", which is the whole lesson of this
# repository's evaluate.py and which I still had to learn here.
preflight_tcc() {
  local probe_label="$LABEL.preflight"
  local probe_script probe_plist probe_out
  probe_script="$(mktemp /tmp/vibe-preflight-XXXXXX.sh)"
  probe_plist="$HOME/Library/LaunchAgents/$probe_label.plist"
  probe_out="$(mktemp /tmp/vibe-preflight-out-XXXXXX)"

  cat > "$probe_script" <<PROBE
#!/bin/bash
if ls "$ROOT" >/dev/null 2>&1; then echo REACHABLE > "$probe_out"; else echo "UNREACHABLE \$(ls "$ROOT" 2>&1 | head -1)" > "$probe_out"; fi
PROBE
  chmod +x "$probe_script"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$probe_plist" <<PROBEPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$probe_label</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$probe_script</string></array>
  <key>WorkingDirectory</key><string>/private/tmp</string>
  <key>RunAtLoad</key><false/>
</dict></plist>
PROBEPLIST

  : > "$probe_out"
  launchctl unload -w "$probe_plist" 2>/dev/null || true
  launchctl load -w "$probe_plist" 2>/dev/null || true
  launchctl kickstart -k "gui/$(id -u)/$probe_label" >/dev/null 2>&1 || true
  local i
  for i in $(seq 1 30); do [ -s "$probe_out" ] && break; sleep 0.2; done
  local verdict; verdict="$(cat "$probe_out" 2>/dev/null)"
  launchctl unload -w "$probe_plist" 2>/dev/null || true
  rm -f "$probe_plist" "$probe_script" "$probe_out"

  case "$verdict" in
    REACHABLE) return 0 ;;
    "") echo "  preflight inconclusive: the probe job never wrote a result."; return 2 ;;
    *)  echo "  preflight: $verdict"; return 1 ;;
  esac
}

echo "=== preflight: can launchd reach $ROOT ? ==="
set +e
preflight_tcc
PRE=$?
set -e
if [ "$PRE" -ne 0 ]; then
  cat <<MSG

REFUSING TO ARM. A launchd job cannot read this checkout.

  $ROOT

macOS TCC protects ~/Downloads, ~/Documents and ~/Desktop, and a LaunchAgent running /bin/bash
holds no grant for any of them. Arming anyway produces a job that fires nightly, exits 126 and
changes nothing -- which is worse than no job, because the calendar says it ran.

Three ways forward, best first:

  1. Move the checkout somewhere unprotected -- ~/src, ~/Projects, anywhere outside those three
     folders -- and run this script again. Nothing else changes; it derives every path from where
     it is run.

  2. Use an in-session schedule instead. A Claude session inherits the terminal's TCC grant, so
     it can read Downloads where launchd cannot. It dies with the session and expires in 7 days.

  3. Grant Full Disk Access to /bin/bash in System Settings. This works and it is a bad trade:
     every shell script on this machine gets access to everything.

MSG
  exit 1
fi
echo "  reachable."
echo

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
