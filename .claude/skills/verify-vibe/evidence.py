"""Run-directory plumbing, the three-verdict protocol, and evidence capture.

Imported by verify_vibe.py and ax.py. Holds nothing that knows how to drive a UI.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "github.com.thewh1teagle.vibe"
APP_BINARY = Path("/Applications/vibe.app/Contents/MacOS/vibe")
# Anchored: an unanchored pattern also matches the `vibe-server` sidecar, which made
# the ownership check report "more than one vibe running" on a perfectly normal launch.
APP_PGREP = f"^{APP_BINARY}$"

class Blocked(Exception):
    """The check could not run. Distinct from the check running and failing."""


class Failed(Exception):
    """The check ran and the observation did not match the expectation."""


# --------------------------------------------------------------------------- plumbing

def emit(verdict: str, check: str, **fields) -> None:
    payload = {"verdict": verdict, "check": check, **fields}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    sys.exit({"PASS": 0, "FAIL": 1, "BLOCKED": 2}[verdict])


def run_shell(argv: list[str], run: Path | None, step: str, timeout: float = 60.0) -> dict:
    """Run a command and record argv, rc, stdout, stderr and wall time as separate fields.

    Keeping rc out of the body is the whole point: a connection failure returning zero
    bytes must not be readable as an empty-but-successful result.
    """
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"timed out after {timeout}s"
    record = {
        "argv": argv,
        "rc": rc,
        "stdout": out,
        "stderr": err,
        "wall_ms": round((time.monotonic() - started) * 1000),
    }
    if run is not None:
        shell_dir = run / "shell"
        shell_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
        (shell_dir / f"{stamp}-{step}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return record


def newest_log() -> Path | None:
    """The log the app is actually appending to.

    The filename is fixed from Local::now() at launch, so a process started yesterday is
    still writing yesterday's file. Never construct `log_$(date +%F).txt`.
    """
    logs = [Path(p) for p in glob.glob(str(CONFIG_DIR / "log_*.txt"))]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def log_entries(since_iso: str | None = None) -> list[dict]:
    """Every JSON log entry at or after `since_iso`, across all log files.

    Filtering by timestamp is not optional. clean_old_logs deletes the previous file and
    the app opens a new one named for the launch date, so "the newest log" right after a
    launch can still be the *previous* session's file -- and its historical readiness
    markers will match happily. A run that trusted them reported a dead session's sidecar
    port as if it were live.
    """
    files = [Path(p) for p in glob.glob(str(CONFIG_DIR / "log_*.txt"))]
    if not files:
        raise Blocked(f"no log_*.txt in {CONFIG_DIR}")
    out = []
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_iso and entry.get("timestamp", "") < since_iso:
                continue
            entry["_file"] = path.name
            out.append(entry)
    return out


def launch_start(run: Path) -> str | None:
    marker = run / "launch.json"
    if not marker.is_file():
        return None
    return json.loads(marker.read_text(encoding="utf-8")).get("start_iso")


# Only build.identity may be waived. Everything else that fails means driving would
# either be impossible or would write somewhere it must not -- and isolation.projects is
# the one standing between a run and the user's real transcripts.
OVERRIDABLE_CHECKS = frozenset({"build.identity"})


def write_gate2(run: Path, verdict: str, checks: list, override: dict | None) -> Path:
    """Record gate 2's outcome where the driving commands can find it."""
    path = run / "gate2.json"
    path.write_text(json.dumps({
        "verdict": verdict,
        "checks": [{"verdict": v, "id": i, "detail": d} for v, i, d in checks],
        "override": override,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def require_gate2(run: Path, command: str) -> None:
    """Refuse to drive the app unless gate 2 is green, or was waived on the record.

    The gate used to be advisory. A blind run of this skill drove the app through a
    FAILED build.identity -- doctor exited 1, the run carried on, and the report read
    clean. A guard that fires and changes nothing is not a guard.
    """
    marker = run / "gate2.json"
    if not marker.is_file():
        raise Blocked(f"{command} refused: gate 2 has not run for this run directory. "
                      f"Run `doctor` first.")
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Must be BLOCKED, not an uncaught traceback: an escaping exception exits 1,
        # which this harness defines as FAIL -- a *red* the caller cannot distinguish
        # from "the button was there and the press failed".
        raise Blocked(f"{command} refused: {marker} is unreadable ({exc})")
    if state.get("verdict") == "PASS":
        return
    failing = [c["id"] for c in state.get("checks", []) if c["verdict"] != "PASS"]
    waivable = {c["id"] for c in state.get("checks", [])
                if c["verdict"] == "FAIL" and c["id"] in OVERRIDABLE_CHECKS}
    override = state.get("override")
    if override and set(failing) <= waivable:
        return
    # Computed from the SAME set the decision uses. Deriving the advice from
    # OVERRIDABLE_CHECKS instead told the reader to re-run with --override for a
    # BLOCKED build.identity, which no override will ever let through.
    unwaivable = sorted(set(failing) - waivable)
    advice = (f"{unwaivable} cannot be waived." if unwaivable else
              "Re-run `doctor --override '<reason>'` to proceed on the record.")
    raise Blocked(f"{command} refused: gate 2 is {state.get('verdict')} on {sorted(failing)}. {advice}")


def read_config() -> dict:
    path = CONFIG_DIR / "app_config.json"
    if not path.is_file():
        raise Blocked(f"{path} does not exist")
    return json.loads(path.read_text(encoding="utf-8"))


# The repo this skill ships inside; `config-keys.ts` is the only list of what the app really reads.
REPO = Path(__file__).resolve().parents[3]
CONFIG_KEYS_TS = REPO / "desktop" / "src" / "lib" / "config-keys.ts"


def known_config_keys() -> set[str]:
    """Every key the app persists, read from `config-keys.ts` rather than restated here.

    `app_config.json` is flat: its keys *contain* dots, they are not paths. So writing
    `transcription.modelOptions.lang` as a top-level key creates a sibling of
    `transcription.modelOptions` that nothing reads -- and a readback of the same flat key
    confirms it, which is how this skill spent a whole run with the real `init_prompt` still in
    place while reporting that it had been cleared.
    """
    if not CONFIG_KEYS_TS.is_file():
        raise Blocked(f"{CONFIG_KEYS_TS} not found -- cannot tell a real config key from a typo")
    keys = set(re.findall(r""":\s*'([a-zA-Z0-9_.]+)'""", CONFIG_KEYS_TS.read_text(encoding="utf-8")))
    if len(keys) < 20:
        # A regex that stops matching would make every key look unknown, or every key look fine
        # depending on which way it failed. Refuse instead of guessing.
        raise Blocked(f"only {len(keys)} keys parsed out of {CONFIG_KEYS_TS}; the parser is wrong")
    return keys


def resolve_config_path(key: str) -> tuple[str, list[str]]:
    """Split `key` into the flat key the app reads and the path inside its value.

    `transcription.projectsPath` -> `('transcription.projectsPath', [])`
    `transcription.modelOptions.lang` -> `('transcription.modelOptions', ['lang'])`

    Raises `Blocked` for anything that is neither, rather than writing a key nothing reads.
    """
    keys = known_config_keys()
    if key in keys:
        return key, []
    parts = key.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        head = ".".join(parts[:cut])
        if head in keys:
            return head, parts[cut:]
    raise Blocked(
        f"{key!r} is not a key the app reads, and no prefix of it is either. "
        f"See {CONFIG_KEYS_TS.relative_to(REPO)}."
    )


def pid_of(run: Path) -> int:
    f = run / "vibe.pid"
    if not f.is_file():
        raise Blocked(f"{f} missing -- launch was never run for this run directory")
    return int(f.read_text().strip())


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# --------------------------------------------------------------------- AppleScript bodies


# ------------------------------------------------------------------ evidence capture

def cmd_frontend_errors(args) -> None:
    """Any vibe::frontend line is a reportFailure that reached the Rust log."""
    run = Path(args.run).resolve()
    entries = log_entries(launch_start(run))
    hits = [e["fields"]["message"] for e in entries
            if e.get("target") == "vibe::frontend" and "message" in e.get("fields", {})]
    out = run / "frontend-errors.json"
    out.write_text(json.dumps(hits, ensure_ascii=False, indent=2), encoding="utf-8")
    if hits:
        emit("FAIL", "log.frontend-silent", rc=0,
             detail=f"{len(hits)} vibe::frontend line(s): {hits[:3]}", source=str(out))
    emit("PASS", "log.frontend-silent", rc=0, detail="no vibe::frontend lines", source=str(out))


def cmd_fs_snapshot(args) -> None:
    run = Path(args.run).resolve()
    root = Path(args.projects).resolve()
    if not root.is_dir():
        emit("BLOCKED", f"fs.{args.step}", rc=1, detail=f"{root} does not exist")
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            records.append({
                "path": str(path.relative_to(root)),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()[:16],
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            })
    out = run / "fs" / f"{args.step}.fs.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    found = [r for r in records if r["path"].endswith("transcript.vibe.json")]
    if args.expect_records is not None and len(found) != args.expect_records:
        emit("FAIL", f"fs.{args.step}", rc=0,
             detail=(f"transcript.vibe.json count={len(found)} of expected={args.expect_records}, "
                     f"files_total={len(records)}"), source=str(out))
    emit("PASS", f"fs.{args.step}", rc=0,
         detail=f"records={len(found)}, files_total={len(records)}", source=str(out))


