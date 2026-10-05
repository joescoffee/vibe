#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Policy for an unattended improvement run. The judgement stays with the agent; the rules are here.

A prompt that says "work on a branch, revert on failure, stop after two bad cycles" is an L1 text
rule, and this repository has three recorded cases of an L1 rule being violated anyway. So the
rules that must hold overnight are code: `begin` refuses to start when they are not met, and
`finish` is what moves the circuit breaker.

What this does NOT do is pick or perform the work. `route.py` picks; the agent performs. A script
cannot judge whether a fix is right, and one that pretended to would be the most dangerous part of
an unattended loop.

    night.py begin                      -> refuse, or open a branch and name the next item
    night.py finish --verdict ACCEPT    -> record it, advance or trip the breaker
    night.py report                     -> the morning report, from the record

State lives under `plans/improve/`, which is gitignored: it is local evidence, like the
`verify-vibe` run directories, and never belongs in a commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "plans" / "improve"
STATE = STATE_DIR / "state.json"
LOCK = STATE_DIR / "night.lock"

# Nothing stops two schedulers pointing at the same repo. I armed a launchd job and an in-session
# cron at the same minute without noticing, and the only reason it would not have corrupted a
# night is that `begin` happens to refuse a dirty tree -- which is luck, not a guard. Two runners
# on one branch is the failure; this is the guard.
STALE_LOCK_SECONDS = 8 * 3600

# Two consecutive non-ACCEPT cycles stops the night. One is a bad fix; two in a row means the
# agent is not converging, and a loop that keeps going in that state burns the night and leaves a
# branch nobody wants to read.
BREAKER_LIMIT = 2

# A night may not touch these however the backlog is edited. The projects folder holds the user's
# real transcripts; `binaries/` holds a locally built sidecar that `chore setup` silently replaces.
FORBIDDEN_PATHS = ("desktop/src-tauri/binaries/", "plans/", ".github/workflows/release",
                   "i18n/desktop/en-US.json")


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=check)


def load() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"night": None, "branch": None, "cycles": [], "consecutive_bad": 0}


def save(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


LOG_DIR = Path.home() / "Library" / "Logs" / "VibeImprove"
LAUNCHD_LABEL = "com.vibe.improve"


def launchd_state() -> dict | None:
    """What launchd knows about the nightly job, or None when it holds no such job.

    `None` and `{"runs": 0}` are different answers. The first means nothing is armed. The
    second means something is armed and has never fired, which is a schedule that has not
    come round yet. Collapsing them is the bug this function exists to avoid.
    """
    probe = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        return None
    runs = re.search(r"runs = (\d+)", probe.stdout)
    code = re.search(r"last exit code = (\d+)", probe.stdout)
    return {
        "armed": True,
        "runs": int(runs.group(1)) if runs else 0,
        "last_exit": int(code.group(1)) if code else None,
    }


def night_logs() -> list[dict]:
    """One row per `night.sh` log file. Empty when the durable runner has never written one."""
    rows = []
    for path in sorted(LOG_DIR.glob("20*.log")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rows.append({
            "date": path.stem,
            "invocations": text.count("night starting"),
            "cycles": text.count("handing to claude"),
            # `night.sh` writes this as its last act. Its absence after a start is the only
            # evidence in the log that the run did not finish on its own terms.
            "finished": text.count("night finished"),
            "last_line": next((l for l in reversed(text.split("\n")) if l.strip()), ""),
        })
    return rows


def gather_evidence() -> dict:
    """Three independent sources, never one.

    `state.json` is written by `begin`, so a job that dies before `begin` leaves none. That is
    exactly the failure worth reporting, and the only source that can see it is launchd.
    """
    return {"state": load() if STATE.is_file() else None,
            "launchd": launchd_state(),
            "logs": night_logs()}


def classify_night(evidence: dict) -> str:
    """`ran`, `fired and failed`, or `nothing scheduled`.

    Order matters. State first, because a night that got as far as `begin` is a night that ran
    whatever launchd later reports. Then a fired-but-stateless job, which is the dead night.
    Everything else is a schedule that has not produced anything, armed or not.
    """
    if evidence["state"] and evidence["state"].get("night"):
        return "ran"
    launchd = evidence["launchd"]
    if launchd and launchd["runs"] > 0:
        return "fired and failed"
    # A start is not a failure. `night.sh` can exit cleanly with no cycles when the deadline has
    # already passed, which is what my own 07:18 hand-run did, and the first version of this
    # classifier called that a dead night. Count starts that never reached the finish marker.
    if any(row["invocations"] > row["finished"] for row in evidence["logs"]):
        return "fired and failed"
    return "nothing scheduled"


def refuse(reason: str) -> int:
    print(f"BLOCKED  {reason}")
    return 2


def take_lock() -> str | None:
    """Return a refusal reason, or None having taken the lock.

    `O_EXCL` so the check and the take are one operation: a lock taken by reading first and
    writing second is two runners agreeing they are alone.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    import os
    import time
    if LOCK.exists():
        age = time.time() - LOCK.stat().st_mtime
        if age < STALE_LOCK_SECONDS:
            holder = LOCK.read_text(encoding="utf-8").strip()
            return (f"another run holds {LOCK} ({holder}, {age / 60:.0f} min ago). "
                    "Two runners on one branch is the thing this prevents. "
                    f"Delete the lock if that run is dead.")
        LOCK.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return f"another run took {LOCK} in the last instant"
    with os.fdopen(fd, "w") as handle:
        handle.write(f"pid {os.getpid()} at {datetime.now().isoformat(timespec='seconds')}\n")
    return None


def cmd_begin(args) -> int:
    state = load()
    tonight = datetime.now().strftime("%Y-%m-%d")

    if state["consecutive_bad"] >= BREAKER_LIMIT and state.get("night") == tonight:
        return refuse(f"circuit breaker: {state['consecutive_bad']} cycles in a row did not ACCEPT. "
                      f"A human reads {state['branch']} before anything else runs tonight.")

    dirty = git("status", "--porcelain").stdout.strip()
    if dirty and not args.allow_dirty:
        return refuse("the working tree is dirty. An unattended run must start from a known state, "
                      f"or its revert cannot be trusted:\n{dirty[:400]}")

    held = take_lock()
    if held:
        return refuse(held)

    branch = f"improve/{datetime.now().strftime('%m%d')}"
    current = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if current != branch:
        exists = git("rev-parse", "--verify", branch).returncode == 0
        made = git("checkout", branch) if exists else git("checkout", "-b", branch)
        if made.returncode != 0:
            return refuse(f"could not get onto {branch}: {made.stderr.strip()[:200]}")

    if state.get("night") != tonight:
        state = {"night": tonight, "branch": branch, "cycles": [], "consecutive_bad": 0,
                 "base": git("rev-parse", "--short=8", "HEAD").stdout.strip(),
                 "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        save(state)

    print(f"night {tonight} on {branch}, base {state['base']}, cycle {len(state['cycles']) + 1}")
    print(f"  never push. never merge. never touch: {', '.join(FORBIDDEN_PATHS)}")
    print(f"  commit only on ACCEPT from `uv run scripts/improve/evaluate.py`")
    print(f"  on REJECT or BLOCKED: `git checkout -- .` and call finish with that verdict")
    print()
    picked = subprocess.run(["uv", "run", "scripts/improve/route.py"], cwd=ROOT,
                            capture_output=True, text=True)
    print(picked.stdout.rstrip())
    if picked.returncode != 0:
        print(picked.stderr.rstrip())
        return picked.returncode
    return 0


def cmd_finish(args) -> int:
    state = load()
    if not state.get("night"):
        return refuse("no night in progress; `begin` was never run")
    head = git("rev-parse", "--short=8", "HEAD").stdout.strip()
    state["cycles"].append({
        "item": args.item,
        "verdict": args.verdict,
        "note": args.note or "",
        "head": head,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    state["consecutive_bad"] = 0 if args.verdict == "ACCEPT" else state["consecutive_bad"] + 1
    save(state)
    print(f"recorded {args.verdict} for {args.item} at {head}")
    if state["consecutive_bad"] >= BREAKER_LIMIT:
        print(f"circuit breaker tripped after {state['consecutive_bad']} cycles without an ACCEPT. "
              f"Stop for tonight.")
        return 1
    return 0


def cmd_report(args) -> int:
    evidence = gather_evidence()
    verdict = classify_night(evidence)
    state = evidence["state"] or load()
    out = STATE_DIR / "report.md"

    if verdict != "ran":
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        lines = ["# Improvement run", ""]
        if verdict == "fired and failed":
            launchd = evidence["launchd"] or {}
            lines += [
                "**The run fired and died before it did anything.** No cycle was attempted and "
                "nothing in the repository changed.",
                "",
                f"- launchd: {'armed, ' + str(launchd.get('runs')) + ' run(s), last exit code ' + str(launchd.get('last_exit')) if launchd else 'no such job (the durable schedule is not armed)'}",
            ]
            for row in evidence["logs"]:
                lines.append(f"- log {row['date']}: {row['invocations']} invocation(s), "
                             f"{row['cycles']} cycle(s), last line `{row['last_line'][:120]}`")
            stderr = LOG_DIR / "launchd-stderr.log"
            if stderr.is_file() and stderr.stat().st_size:
                body = stderr.read_text(encoding="utf-8", errors="replace").strip().split("\n")
                lines += ["", "What it said before exiting:", "", "```"] + body[-6:] + ["```"]
            lines += ["", "An exit code with no cycles means the runner never reached "
                      "`night.py begin`, so none of its refusals applied. Look at the runner, "
                      "not at the backlog."]
        else:
            lines += ["No night has run yet.", "",
                      f"- launchd: {'armed, never fired' if evidence['launchd'] else 'no such job'}",
                      f"- runner logs: {len(evidence['logs'])} file(s), "
                      f"{sum(r['invocations'] for r in evidence['logs'])} invocation(s)"]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(out)
        return 0

    cycles = state["cycles"]
    accepted = [c for c in cycles if c["verdict"] == "ACCEPT"]
    log = git("log", "--oneline", f"{state['base']}..HEAD").stdout.strip()
    diff = git("diff", "--stat", f"{state['base']}..HEAD").stdout.strip()

    lines = [
        f"# Improvement run — night of {state['night']}",
        "",
        f"Branch `{state['branch']}`, from `{state['base']}`. "
        f"{len(accepted)} of {len(cycles)} cycles accepted.",
    ]
    if state["consecutive_bad"] >= BREAKER_LIMIT:
        lines += ["", f"**The circuit breaker tripped.** {state['consecutive_bad']} cycles in a row "
                      "did not pass the gate, so the run stopped early. That is the designed "
                      "behaviour, not a crash."]
    lines += ["", "## Cycles", ""]
    if not cycles:
        lines.append("None. `begin` ran and nothing was attempted — most likely every "
                     "agent-startable backlog item was blocked.")
    else:
        lines.append("| # | item | verdict | at | note |")
        lines.append("|---|---|---|---|---|")
        for i, c in enumerate(cycles, 1):
            lines.append(f"| {i} | `{c['item']}` | **{c['verdict']}** | `{c['head']}` | {c['note']} |")
    lines += ["", "## Commits", "", f"```\n{log or '(none)'}\n```",
              "", "## Diff", "", f"```\n{diff or '(none)'}\n```",
              "", "## What a human still has to do", ""]
    backlog = json.loads((Path(__file__).resolve().parent / "backlog.json").read_text(encoding="utf-8"))
    for item in backlog["items"]:
        if item["needs_human"]:
            lines.append(f"- **{item['id']}** — {item.get('human_decides', item['title'])}")
    lines += ["", "Nothing was pushed and nothing was merged. Review the branch, then decide."]

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOCK.unlink(missing_ok=True)
    print(out)
    return 0


def self_test() -> int:
    failures = 0
    print("1. the breaker trips on the second non-ACCEPT, not the first")
    bad = 0
    for v in ("REJECT", "REJECT"):
        bad = 0 if v == "ACCEPT" else bad + 1
    ok = bad >= BREAKER_LIMIT
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  two REJECTs -> tripped={bad >= BREAKER_LIMIT}")

    print("2. an ACCEPT clears it")
    bad = 1
    bad = 0 if "ACCEPT" == "ACCEPT" else bad + 1
    ok = bad == 0
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  consecutive_bad={bad}")

    print("3. one REJECT alone does not trip it")
    ok = 1 < BREAKER_LIMIT
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  limit={BREAKER_LIMIT}")

    print("4. the forbidden list names the two that would be silently destructive")
    ok = "desktop/src-tauri/binaries/" in FORBIDDEN_PATHS and "i18n/desktop/en-US.json" in FORBIDDEN_PATHS
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  {FORBIDDEN_PATHS}")

    print("5. a second runner is refused while the first holds the lock")
    import shutil, tempfile
    global STATE_DIR, LOCK
    keep_dir, keep_lock = STATE_DIR, LOCK
    STATE_DIR = Path(tempfile.mkdtemp(prefix="night-lock-"))
    LOCK = STATE_DIR / "night.lock"
    try:
        first = take_lock()
        second = take_lock()
        ok = first is None and second is not None and "another run holds" in second
        failures += 0 if ok else 1
        print(f"   {'ok' if ok else 'MISMATCH'}  first={first!r} second={'refused' if second else 'ALLOWED'}")
        LOCK.unlink(missing_ok=True)
        third = take_lock()
        ok = third is None
        failures += 0 if ok else 1
        print(f"   {'ok' if ok else 'MISMATCH'}  released -> next runner allowed")
    finally:
        shutil.rmtree(STATE_DIR, ignore_errors=True)
        STATE_DIR, LOCK = keep_dir, keep_lock

    print("6. the report tells a dead night from a night that never ran")
    # The morning of 2026-10-06: the job fired at 23:07, exited 126 before `begin` could write
    # state.json, and the report said "No night has run yet" -- the same string it prints when
    # nothing was ever scheduled. One string for two situations is why the operator had to ask.
    cases = [
        ({"state": None, "launchd": None, "logs": []}, "nothing scheduled"),
        ({"state": None, "launchd": {"armed": True, "runs": 1, "last_exit": 126}, "logs": []}, "fired and failed"),
        ({"state": None, "launchd": {"armed": True, "runs": 0, "last_exit": None}, "logs": []}, "nothing scheduled"),
        ({"state": {"night": "2026-10-06", "cycles": []}, "launchd": None, "logs": []}, "ran"),
        # A clean no-op run. `night.sh` exits with no cycles when the deadline has passed, and
        # the first classifier read that start as a death. Caught by reading the output, not by
        # the suite, which is why it is a case now.
        ({"state": None, "launchd": None,
          "logs": [{"invocations": 1, "finished": 1, "cycles": 0}]}, "nothing scheduled"),
        ({"state": None, "launchd": None,
          "logs": [{"invocations": 1, "finished": 0, "cycles": 0}]}, "fired and failed"),
    ]
    for evidence, want in cases:
        got = classify_night(evidence)
        ok = got == want
        failures += 0 if ok else 1
        print(f"   {'ok' if ok else 'MISMATCH'}  {want:18} <- state={evidence['state'] is not None} "
              f"launchd={evidence['launchd']}")

    print("7. a dirty tree is refused")
    ok = refuse("probe") == 2
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  refuse() exits 2")

    print(f"\nself-test: {'PASS' if not failures else f'FAIL ({failures})'}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    b = sub.add_parser("begin"); b.add_argument("--allow-dirty", action="store_true")
    f = sub.add_parser("finish")
    f.add_argument("--item", required=True)
    f.add_argument("--verdict", required=True, choices=["ACCEPT", "REJECT", "BLOCKED"])
    f.add_argument("--note", default="")
    sub.add_parser("report")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.cmd == "begin":
        return cmd_begin(args)
    if args.cmd == "finish":
        return cmd_finish(args)
    if args.cmd == "report":
        return cmd_report(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
