#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Drive the installed Vibe desktop app and prove what it did.

Every subcommand prints one JSON object to stdout and exits 0 on PASS, 1 on FAIL, 2 on
BLOCKED. BLOCKED means the check could not run -- it is never a pass. See SKILL.md.

This file owns the app lifecycle (the gates) and the CLI. UI driving lives in ax.py;
run-directory plumbing and evidence capture live in evidence.py.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ax import (  # noqa: E402
    _AX_TRUSTED, _EXISTS, _QUIT, CONTROL_ELEMENT,
    cmd_dump, cmd_exists, cmd_open_panel, cmd_press, osascript,
)
from evidence import (  # noqa: E402
    APP_BINARY, APP_PGREP, CONFIG_DIR, OVERRIDABLE_CHECKS, Blocked, Failed, alive,
    cmd_frontend_errors, cmd_fs_snapshot, emit, launch_start, log_entries, newest_log,
    pid_of, read_config, resolve_config_path, run_shell, write_gate2,
)

READINESS_MARKERS = {
    "commit": "COMMIT HASH:",
    "sidecar": "vibe-server ready on port",
    "webview": "crash.txt",  # cmd/app.rs logs this only once React mounts
}



# ------------------------------------------------------------- lifecycle gates

def cmd_snapshot_evidence(args) -> None:
    """Gate 0. Copy every log out before any vibe process can destroy them.

    clean_old_logs runs at setup.rs:44, before the CLI branch at :123, so *any* execution
    of the bundle -- GUI or `vibe transcribe` -- deletes every log but the current one.
    """
    run = Path(args.run).resolve()
    pre = run / "logs" / "pre"
    pre.mkdir(parents=True, exist_ok=True)

    existing = sorted(glob.glob(str(CONFIG_DIR / "log_*.txt")))
    for src in existing:
        shutil.copy2(src, pre / Path(src).name)

    for name, dest in (("app_config.json", "app_config.before.json"),
                       (".window-state.json", "window-state.before.json")):
        src = CONFIG_DIR / name
        if src.is_file():
            shutil.copy2(src, run / dest)

    (pre / "configdir.ls.txt").write_text(
        "\n".join(sorted(p.name for p in CONFIG_DIR.iterdir())), encoding="utf-8"
    )

    copied = len(list(pre.glob("log_*.txt")))
    if copied < len(existing):
        emit("BLOCKED", "gate0.evidence", rc=1,
             detail=f"copied={copied} of source={len(existing)} log files", source=str(pre))

    try:
        json.loads((run / "app_config.before.json").read_text(encoding="utf-8"))
    except Exception as exc:
        emit("BLOCKED", "gate0.evidence", rc=1, detail=f"app_config.before.json unreadable: {exc}")

    running = run_shell(["pgrep", "-f", APP_PGREP], run, "gate0-pgrep")
    if running["stdout"].strip():
        emit("BLOCKED", "gate0.evidence", rc=1,
             detail=("a vibe process is already running (pids "
                     f"{running['stdout'].split()}); it may hold the only copy of evidence "
                     "this run is about to destroy. Quit it first, or drive it deliberately."),
             source=str(pre))

    emit("PASS", "gate0.evidence", rc=0,
         detail=f"logs={copied} of expected>={len(existing)}, no pre-existing vibe process",
         source=str(pre))


def cmd_config_set(args) -> None:
    """Edit app_config.json in place. config_watcher.rs reloads it without a relaunch,
    which matters because every relaunch destroys a day of logs."""
    run = Path(args.run).resolve()
    path = CONFIG_DIR / "app_config.json"
    value = json.loads(args.json)

    # `app_config.json` is flat: its keys contain dots, they are not paths. Writing
    # `transcription.modelOptions.lang` as a top-level key used to create a sibling of
    # `transcription.modelOptions` that nothing reads, and reading back the same flat key
    # confirmed it -- so a whole run transcribed with the real `init_prompt` still in place
    # while this command reported it cleared. `resolve_config_path` refuses a key the app
    # does not read rather than inventing one.
    flat_key, inner = resolve_config_path(args.key)

    config = read_config()
    if inner:
        container = config.get(flat_key)
        if not isinstance(container, dict):
            emit("BLOCKED", f"config.{args.key}", rc=2,
                 detail=f"{flat_key!r} is {type(container).__name__}, not an object; cannot set {'.'.join(inner)}",
                 source=str(path))
        cursor = container
        for part in inner[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[inner[-1]] = value
    else:
        config[flat_key] = value
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    time.sleep(1.0)  # let config_watcher pick it up
    # Read back through the same resolution, from a fresh load of the file. Reading back the
    # key we just wrote in the shape we just wrote it is what made the old version unfalsifiable.
    observed = read_config().get(flat_key)
    for part in inner:
        observed = observed.get(part) if isinstance(observed, dict) else None
    if observed != value:
        emit("FAIL", f"config.{args.key}", rc=0,
             detail=f"expected {value!r}, observed {observed!r} at {flat_key!r}{'.' + '.'.join(inner) if inner else ''}",
             source=str(path))
    emit("PASS", f"config.{args.key}", rc=0,
         detail=f"{args.key}={observed!r} (written to {flat_key!r}{' path ' + '.'.join(inner) if inner else ''})",
         source=str(path))


def cmd_launch(args) -> None:
    """Gate 1. Start the app we own, and prove it is ours and ready."""
    run = Path(args.run).resolve()
    if not (run / "logs" / "pre").is_dir():
        emit("BLOCKED", "gate1.launch", rc=1,
             detail="gate0 (snapshot-evidence) has not run for this run directory")
    if not APP_BINARY.is_file():
        emit("BLOCKED", "gate1.launch", rc=1, detail=f"{APP_BINARY} not installed")

    env = dict(os.environ, RUST_LOG=args.rust_log)
    stdout = (run / "stdout.log").open("wb")
    stderr = (run / "stderr.log").open("wb")
    # Anchor before spawning, and subtract a second of clock slack. Everything after this
    # instant is ours; anything before it belongs to a session that no longer exists.
    start_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    start_iso = start_iso[:-1] + ".000000Z"
    # Empty argv keeps GUI mode: is_cli_args([]) is false (cli.rs:40-47). Any other
    # argument routes to the sidecar and creates no window.
    proc = subprocess.Popen([str(APP_BINARY)], stdout=stdout, stderr=stderr, env=env)
    (run / "vibe.pid").write_text(str(proc.pid), encoding="utf-8")
    (run / "launch.json").write_text(
        json.dumps({"pid": proc.pid, "start_iso": start_iso}, indent=2), encoding="utf-8")

    deadline = time.monotonic() + args.timeout
    seen: dict[str, str] = {}
    while time.monotonic() < deadline:
        # The single-instance plugin forwards argv to an existing app and exits. A dead
        # pid while a vibe still runs means we are not the owner.
        if not alive(proc.pid):
            other = run_shell(["pgrep", "-f", APP_PGREP], run, "launch-pgrep")
            detail = ("our process exited immediately; "
                      + ("another vibe is running (single-instance forwarded our argv): "
                         f"{other['stdout'].split()}" if other["stdout"].strip()
                         else "and no vibe is running -- it crashed, see stderr.log"))
            emit("BLOCKED", "gate1.launch", rc=1, detail=detail, source=str(run / "stderr.log"))
        try:
            entries = log_entries(start_iso)
        except Blocked:
            time.sleep(0.4)
            continue
        for entry in entries:
            message = entry.get("fields", {}).get("message", "")
            for key, marker in READINESS_MARKERS.items():
                if marker in message:
                    seen[key] = message
        if len(seen) == len(READINESS_MARKERS):
            break
        time.sleep(0.4)

    missing = sorted(set(READINESS_MARKERS) - set(seen))
    if missing:
        emit("BLOCKED", "gate1.launch", rc=1, pid=proc.pid,
             detail=f"missing readiness markers {missing}; saw {sorted(seen)}",
             source=str(newest_log() or CONFIG_DIR))

    port = None
    if "sidecar" in seen:
        port = seen["sidecar"].rsplit(" ", 1)[-1]
    emit("PASS", "gate1.launch", rc=0, pid=proc.pid, port=port,
         detail=f"markers={sorted(seen)}", source=str(newest_log()))


def cmd_doctor(args) -> None:
    """Gate 2. Seven read-only checks. Any BLOCKED makes the whole gate BLOCKED."""
    run = Path(args.run).resolve()
    # Invalidate first. `emit` is sys.exit, and six of them sit between here and
    # write_gate2 -- so without this, a doctor that refuses or cannot run leaves the
    # PREVIOUS run's PASS on disk and `press` sails through it. That was a real hole,
    # proven by driving the CLI: marker byte-identical across a refused --override,
    # and the next press got past the gate. A permission must be re-earned, never
    # inherited from a run that did not finish.
    (run / "gate2.json").unlink(missing_ok=True)
    checks: list[tuple[str, str, str]] = []  # (verdict, id, detail)

    trusted = osascript(_AX_TRUSTED, [], run, "doctor-ax")
    if trusted["rc"] != 0:
        checks.append(("BLOCKED", "ax.trusted",
                       "accessibility not granted to the controlling app: "
                       + trusted["stderr"].strip()[:200]))
    else:
        checks.append(("PASS", "ax.trusted", "System Events responds"))

    try:
        pid = pid_of(run)
    except Blocked as exc:
        emit("BLOCKED", "gate2.doctor", rc=1, detail=str(exc))

    if not alive(pid):
        checks.append(("BLOCKED", "process.ours", f"pid {pid} is gone"))
    else:
        found = run_shell(["pgrep", "-f", APP_PGREP], run, "doctor-pgrep")
        pids = found["stdout"].split()
        if str(pid) not in pids:
            checks.append(("BLOCKED", "process.ours", f"pid {pid} is not a vibe process"))
        elif len(pids) > 1:
            checks.append(("BLOCKED", "process.ours", f"more than one vibe running: {pids}"))
        else:
            checks.append(("PASS", "process.ours", f"pid {pid} is the only vibe"))

    since = launch_start(run)
    if since is None:
        emit("BLOCKED", "gate2.doctor", rc=1,
             detail="launch.json missing -- cannot tell this session's log lines from a dead one's")
    try:
        entries = log_entries(since)
    except Blocked as exc:
        emit("BLOCKED", "gate2.doctor", rc=1, detail=str(exc))
    messages = [e.get("fields", {}).get("message", "") for e in entries]

    commit = next((m.split("COMMIT HASH:")[1].strip() for m in messages if "COMMIT HASH:" in m), None)
    if commit is None:
        checks.append(("BLOCKED", "build.identity", "no COMMIT HASH line in the newest log"))
    elif args.expect_commit and not commit.startswith(args.expect_commit[:8]):
        checks.append(("FAIL", "build.identity",
                       f"expected {args.expect_commit[:8]}, observed {commit} -- "
                       "driving a stale build proves nothing about your change"))
    else:
        checks.append(("PASS", "build.identity", f"COMMIT HASH={commit}"))

    port = next((m.rsplit(" ", 1)[-1] for m in messages if "vibe-server ready on port" in m), None)
    if port is None:
        checks.append(("BLOCKED", "sidecar.health", "no port line in the log"))
    else:
        probe = run_shell(
            ["curl", "-fsS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3",
             f"http://127.0.0.1:{port}/health"], run, "doctor-health")
        if probe["rc"] != 0:
            checks.append(("BLOCKED", "sidecar.health",
                           f"curl rc={probe['rc']} (7=connection refused) on port {port}"))
        elif probe["stdout"].strip() != "200":
            checks.append(("FAIL", "sidecar.health",
                           f"expected http 200, observed {probe['stdout'].strip()!r}"))
        else:
            checks.append(("PASS", "sidecar.health", f"http 200 on port {port}"))

    if any("crash.txt" in m for m in messages):
        checks.append(("PASS", "webview.mounted", "crash.txt probe logged -- React mounted"))
    else:
        checks.append(("BLOCKED", "webview.mounted",
                       "no crash.txt probe; the window may exist without the app mounting"))

    control = osascript(_EXISTS, [str(pid), "title", CONTROL_ELEMENT], run, "doctor-control")
    verdict = control["stdout"].strip()
    if control["rc"] != 0 or verdict.startswith("BLOCKED"):
        checks.append(("BLOCKED", "ax.reachable", f"{verdict or control['stderr'].strip()[:160]}"))
    elif verdict == "ABSENT":
        checks.append(("FAIL", "ax.reachable", f"control element {CONTROL_ELEMENT!r} not found"))
    else:
        checks.append(("PASS", "ax.reachable", verdict))

    try:
        config = read_config()
    except Blocked as exc:
        emit("BLOCKED", "gate2.doctor", rc=1, detail=str(exc))
    projects = config.get("transcription.projectsPath")
    expected = str(Path(args.projects).resolve()) if args.projects else None
    if expected and projects != expected:
        checks.append(("BLOCKED", "isolation.projects",
                       f"transcription.projectsPath={projects!r}, expected {expected!r} -- "
                       "refusing to write into the user's real transcripts folder"))
    elif config.get("transcription.saveTranscripts", True) is not True:
        checks.append(("BLOCKED", "isolation.projects",
                       "transcription.saveTranscripts is false; saving would never be attempted"))
    else:
        checks.append(("PASS", "isolation.projects", f"projectsPath={projects!r}"))

    verdict_path = run / "verdict.txt"
    with verdict_path.open("a", encoding="utf-8") as handle:
        for state, check_id, detail in checks:
            handle.write(f"{state:7} {check_id} — {detail}\n")

    worst = "BLOCKED" if any(c[0] == "BLOCKED" for c in checks) else (
        "FAIL" if any(c[0] == "FAIL" for c in checks) else "PASS")

    failing = {check_id for state, check_id, _ in checks if state != "PASS"}
    override = None
    if args.override and not failing:
        emit("BLOCKED", "gate2.doctor", rc=1,
             detail="--override was given but every check passed; an override that waives nothing "
                    "is noise in the record. Drop the flag.",
             source=str(verdict_path))
    if args.override:
        unwaivable = sorted(failing - OVERRIDABLE_CHECKS)
        if unwaivable:
            emit("BLOCKED", "gate2.doctor", rc=1,
                 detail=f"--override cannot waive {unwaivable}; only {sorted(OVERRIDABLE_CHECKS)} "
                        "may be driven through knowingly",
                 source=str(verdict_path))
        override = {"reason": args.override, "waived": sorted(failing)}
        with verdict_path.open("a", encoding="utf-8") as handle:
            handle.write(f"OVERRIDE gate2.doctor — waived {sorted(failing)}: {args.override}\n")

    marker = write_gate2(run, worst, checks, override)
    emit(worst, "gate2.doctor", rc=0, checks=[{"verdict": v, "id": i, "detail": d} for v, i, d in checks],
         override=override, source=str(verdict_path), gate=str(marker))



def cmd_wait_log(args) -> None:
    run = Path(args.run).resolve()
    deadline = time.monotonic() + args.timeout
    since = launch_start(run)
    while time.monotonic() < deadline:
        entries = log_entries(since)
        for entry in entries:
            if args.pattern in entry.get("fields", {}).get("message", ""):
                emit("PASS", "wait-log", rc=0, detail=f"matched {args.pattern!r}",
                     source=str(newest_log()))
        time.sleep(1.0)
    emit("BLOCKED", "wait-log", rc=124,
         detail=f"{args.pattern!r} never appeared within {args.timeout}s",
         source=str(newest_log()))



def cmd_quit(args) -> None:
    """Gate 4. Graceful quit by pid, then restore config, then copy the post logs."""
    run = Path(args.run).resolve()
    pid = pid_of(run)
    notes = []

    if alive(pid):
        osascript(_QUIT, [str(pid)], run, "quit-menu")
        for _ in range(40):
            if not alive(pid):
                break
            time.sleep(0.25)
    if alive(pid):
        notes.append("graceful quit timed out, sent SIGTERM")
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            if not alive(pid):
                break
            time.sleep(0.25)

    # Never pkill by name: the user's own instance and an unrelated `tauri dev` both match.
    orphans = run_shell(["pgrep", "-P", str(pid)], run, "quit-orphans")
    (run / "orphans.txt").write_text(orphans["stdout"], encoding="utf-8")

    for backup, name in (("app_config.before.json", "app_config.json"),
                         ("window-state.before.json", ".window-state.json")):
        src = run / backup
        if src.is_file():
            shutil.copy2(src, CONFIG_DIR / name)

    post = run / "logs" / "post"
    post.mkdir(parents=True, exist_ok=True)
    for src in glob.glob(str(CONFIG_DIR / "log_*.txt")):
        shutil.copy2(src, post / Path(src).name)

    pre = run / "logs" / "pre"
    if not pre.is_dir() or not any(pre.iterdir()):
        emit("BLOCKED", "gate4.teardown", rc=1,
             detail="logs/pre is missing or empty after cleanup — cleanup ate the proof")
    if alive(pid):
        emit("FAIL", "gate4.teardown", rc=0, detail=f"pid {pid} still alive after SIGTERM")
    if orphans["stdout"].strip():
        emit("FAIL", "gate4.teardown", rc=0,
             detail=f"orphan children left: {orphans['stdout'].split()}")

    emit("PASS", "gate4.teardown", rc=0,
         detail=f"pid gone, no orphans, config restored, pre/={len(list(pre.iterdir()))} files"
                + (f"; {'; '.join(notes)}" if notes else ""),
         source=str(run))


def cmd_fixture(args) -> None:
    """Real speech, not a tone.

    A synthetic tone transcribes to zero segments, persist() returns early at
    use-transcribe-queue.ts:349, and saveTranscript is never called -- the run would go
    green having exercised nothing.
    """
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    text = args.text
    result = run_shell(["say", "-o", str(out), "--data-format=LEI16@16000", text], None, "fixture")
    if result["rc"] != 0 or not out.is_file():
        emit("BLOCKED", "fixture", rc=result["rc"], detail=result["stderr"].strip()[:200])
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    Path(str(out) + ".sha256").write_text(digest, encoding="utf-8")
    emit("PASS", "fixture", rc=0,
         detail=f"bytes={out.stat().st_size}, sha256={digest[:16]}, text={text!r}", source=str(out))


# ------------------------------------------------------------------------------- main



# ------------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def with_run(p):
        p.add_argument("--run", required=True, help="run directory under plans/verify-vibe/runs/")
        return p

    with_run(sub.add_parser("snapshot-evidence")).set_defaults(func=cmd_snapshot_evidence)

    p = with_run(sub.add_parser("config-set"))
    p.add_argument("--key", required=True)
    p.add_argument("--json", required=True, help="the value, as JSON")
    p.set_defaults(func=cmd_config_set)

    p = with_run(sub.add_parser("launch"))
    p.add_argument("--rust-log", default="vibe=DEBUG")
    p.add_argument("--timeout", type=float, default=90.0)
    p.set_defaults(func=cmd_launch)

    p = with_run(sub.add_parser("doctor"))
    p.add_argument("--expect-commit", default=None)
    p.add_argument("--projects", default=None)
    p.add_argument("--override", default=None, metavar="REASON",
                   help="drive on through a waivable failure, recorded in verdict.txt")
    p.set_defaults(func=cmd_doctor)

    p = with_run(sub.add_parser("dump"))
    p.add_argument("--step", required=True)
    p.set_defaults(func=cmd_dump)

    for name, func in (("press", cmd_press), ("exists", cmd_exists)):
        p = with_run(sub.add_parser(name))
        p.add_argument("--name")
        p.add_argument("--name-contains")
        p.add_argument("--class-contains")
        p.set_defaults(func=func)

    p = with_run(sub.add_parser("open-panel"))
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_open_panel)

    p = with_run(sub.add_parser("wait-log"))
    p.add_argument("--pattern", required=True)
    p.add_argument("--timeout", type=float, default=1800.0)
    p.set_defaults(func=cmd_wait_log)

    with_run(sub.add_parser("frontend-errors")).set_defaults(func=cmd_frontend_errors)

    p = with_run(sub.add_parser("fs-snapshot"))
    p.add_argument("--step", required=True)
    p.add_argument("--projects", required=True)
    p.add_argument("--expect-records", type=int, default=None)
    p.set_defaults(func=cmd_fs_snapshot)

    with_run(sub.add_parser("quit")).set_defaults(func=cmd_quit)

    p = sub.add_parser("fixture")
    p.add_argument("--out", required=True)
    p.add_argument("--text", default="The quick brown fox jumps over the lazy dog. "
                                     "Verification run for the Vibe transcripts store.")
    p.set_defaults(func=cmd_fixture)

    args = parser.parse_args()
    try:
        args.func(args)
    except Blocked as exc:
        emit("BLOCKED", args.command, rc=1, detail=str(exc))
    except Failed as exc:
        emit("FAIL", args.command, rc=0, detail=str(exc))


if __name__ == "__main__":
    main()
