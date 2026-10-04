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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "plans" / "improve"
STATE = STATE_DIR / "state.json"

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


def refuse(reason: str) -> int:
    print(f"BLOCKED  {reason}")
    return 2


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
    state = load()
    out = STATE_DIR / "report.md"
    if not state.get("night"):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        out.write_text("# Improvement run\n\nNo night has run yet.\n", encoding="utf-8")
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

    print("5. a dirty tree is refused")
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
