#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The gate a regular improvement loop is allowed to commit behind.

`chore ci` is the pull-request gate: nine checks, fast, scoped to what a PR touches. It is not
enough to let an agent commit unattended, because it never compiles `server/` and never touches
`handoff/`. Both of those have shipped defects this year that nothing mechanical would have caught
-- the unbounded inbound buffer and the unauthenticated transcription endpoint were found by a
human review, in files no job linted.

This runs the lot and answers once: ACCEPT, REJECT or BLOCKED.

Design rules, each from a failure this repository actually had:

- **The verdict is the exit code.** 0 ACCEPT, 1 REJECT, 2 BLOCKED. A lever that prints its verdict
  and exits 0 is not a gate; `verify-vibe`'s doctor check was exactly that, and a blind run drove
  straight through a FAILED build identity.
- **Exit codes are taken outside pipes.** `subprocess.run` with no shell pipeline, because
  `cmd | tail; echo $?` reads `tail`'s status, and this tree has lost a day to that twice.
- **"Did not measure" and "no problem" print differently.** A gate whose tool is missing is
  BLOCKED, never PASS. `npx vitest --reporter=basic` once died in its loader and the harness
  reported exit 0; it was read as a pass twice.
- **The subject prints its own identity first.** Commit, branch and whether the working tree is
  clean, before any verdict, so a result can never be attributed to the wrong revision.
- **It has been watched failing.** `--self-test` injects a failing gate and requires REJECT, and a
  missing binary and requires BLOCKED. A check nobody has seen fail is not a check.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Where cargo writes, when anything says so. Unset by default: cargo's own target directory is
# right for CI, for a human at a terminal and under launchd. The one place it is wrong is a
# sandboxed agent session, where a build script writing under the project tree gets
# `Operation not permitted (os error 1)` -- and probing for that from Python does not work,
# because ordinary writes into `target/` succeed there while the build script's do not. So this
# is an explicit override rather than a guess, and the gate below turns that EPERM into BLOCKED
# with the variable's name in the message.
TARGET_OVERRIDE = os.environ.get("VIBE_IMPROVE_TARGET_DIR")
TARGET_ROOT = Path(TARGET_OVERRIDE) if TARGET_OVERRIDE else None

# Output that means the gate could not run, not that the code is wrong. Each of these has been
# seen: EPERM from a build script writing into a sandboxed project tree, and a toolchain that is
# not installed. Reporting either as FAIL is the P5 failure this file exists to avoid.
# Matched on the condition, not on one spelling of it. The first version of this list held
# `Operation not permitted (os error 1)` and missed the very next real instance, which cargo
# rendered as `Os { code: 1, kind: PermissionDenied, message: "Operation not permitted" }` from
# a build script's panic. Same condition, two surfaces -- which is the classifier bug this file
# is otherwise written to avoid, caught here only because the control case was run too.
TOOLING_FAILURE = (
    "Operation not permitted",
    "PermissionDenied",
    "Permission denied",
    "error: no such command",
    "is not installed for the toolchain",
    "linker `cc` not found",
)

# Which of those mean "cargo could not write where it was pointed", and so have a known fix.
PERMISSION_MARKERS = ("Operation not permitted", "PermissionDenied", "Permission denied")


class Gate:
    def __init__(self, name: str, cmd: list[str], cwd: Path, target: str | None = None, needs: str | None = None):
        self.name, self.cmd, self.cwd, self.target, self.needs = name, cmd, cwd, target, needs


def gates() -> list[Gate]:
    d, s, w, p = ROOT / "desktop", ROOT / "server", ROOT / "handoff/wasm", ROOT / "handoff/probe"
    return [
        Gate("frontend.test",      ["pnpm", "exec", "vitest", "run"], d, needs="pnpm"),
        Gate("frontend.types",     ["pnpm", "check-types"], ROOT, needs="pnpm"),
        Gate("frontend.lint",      ["pnpm", "exec", "eslint", "src"], d, needs="pnpm"),
        Gate("i18n",               ["uv", "run", "scripts/check_i18n.py"], ROOT, needs="uv"),
        Gate("quality.selftest",   ["uv", "run", "scripts/transcript_quality.py", "--self-test"], ROOT, needs="uv"),
        Gate("root.fmt",           ["cargo", "fmt", "--manifest-path", "Cargo.toml", "--all", "--", "--check"], ROOT, "root", "cargo"),
        Gate("root.clippy",        ["cargo", "clippy", "--manifest-path", "Cargo.toml", "--all-targets", "--", "-D", "warnings"], ROOT, "root", "cargo"),
        Gate("root.test",          ["cargo", "test", "--manifest-path", "Cargo.toml", "--all"], ROOT, "root", "cargo"),
        Gate("server.fmt",         ["cargo", "fmt", "--all", "--", "--check"], s, "server", "cargo"),
        Gate("server.test",        ["cargo", "test", "--all"], s, "server", "cargo"),
        Gate("handoff.wasm.fmt",   ["cargo", "fmt", "--", "--check"], w, "wasm", "cargo"),
        Gate("handoff.wasm.clippy",["cargo", "clippy", "--all-targets", "--", "-D", "warnings"], w, "wasm", "cargo"),
        Gate("handoff.wasm.test",  ["cargo", "test"], w, "wasm", "cargo"),
        Gate("handoff.probe.fmt",  ["cargo", "fmt", "--", "--check"], p, "wasm", "cargo"),
        Gate("handoff.probe.clippy",["cargo", "clippy", "--all-targets", "--", "-D", "warnings"], p, "wasm", "cargo"),
        Gate("handoff.pwa.test",   ["node", "--test", "handoff/pwa/tests/pairing.test.mjs"], ROOT, needs="node"),
    ]


def identity() -> dict:
    """Printed before any verdict. A result attributed to the wrong revision is worse than none."""
    def git(*a: str) -> str:
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else "?"
    return {
        "commit": git("rev-parse", "--short=8", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "tree_clean": git("status", "--porcelain") == "",
        "unpushed": git("rev-list", "--count", "@{u}..HEAD"),
    }


def run_gate(gate: Gate, extra_env: dict | None = None) -> dict:
    if gate.needs and shutil.which(gate.needs) is None:
        return {"gate": gate.name, "verdict": "BLOCKED", "rc": None,
                "detail": f"{gate.needs} is not on PATH; this gate did not run"}
    if not gate.cwd.is_dir():
        return {"gate": gate.name, "verdict": "BLOCKED", "rc": None,
                "detail": f"{gate.cwd} does not exist; this gate did not run"}
    env = dict(os.environ, **(extra_env or {}))
    if gate.target and TARGET_ROOT is not None:
        env["CARGO_TARGET_DIR"] = str(TARGET_ROOT / gate.target)
    started = time.monotonic()
    # No shell, no pipeline: the status below is this command's own.
    proc = subprocess.run(gate.cmd, cwd=gate.cwd, env=env, capture_output=True, text=True)
    seconds = round(time.monotonic() - started, 1)
    output = proc.stdout + proc.stderr
    tail = output.strip().split("\n")
    if proc.returncode != 0:
        hit = next((marker for marker in TOOLING_FAILURE if marker in output), None)
        if hit:
            fix = (" Set VIBE_IMPROVE_TARGET_DIR to a writable path outside the project; a build "
                   "script cannot write under it here." if hit in PERMISSION_MARKERS else "")
            return {"gate": gate.name, "verdict": "BLOCKED", "rc": proc.returncode, "seconds": seconds,
                    "detail": f"the gate could not run ({hit}); this says nothing about the code.{fix}"}
    return {
        "gate": gate.name,
        "verdict": "PASS" if proc.returncode == 0 else "FAIL",
        "rc": proc.returncode,
        "seconds": seconds,
        # Three lines was not enough to diagnose from. The first real CI run produced four
        # failures and the artifact could identify two of them; the other two needed the raw
        # runner log, which is the thing this file exists to replace. Terminals get a summary,
        # the JSON keeps everything.
        "detail": "" if proc.returncode == 0 else " / ".join(line.strip() for line in tail[-3:])[:300],
        "output": "" if proc.returncode == 0 else output[-20000:],
    }


def evaluate(only: list[str] | None, extra_env: dict | None = None) -> tuple[str, list[dict], dict]:
    who = identity()
    print(f"measuring {ROOT}", flush=True)
    print(f"  commit {who['commit']} on {who['branch']}, tree {'clean' if who['tree_clean'] else 'DIRTY'}, "
          f"{who['unpushed']} unpushed", flush=True)
    print(f"  cargo target {TARGET_ROOT or "cargo default (VIBE_IMPROVE_TARGET_DIR unset)"}", flush=True)
    results = []
    for gate in gates():
        if only and gate.name not in only:
            continue
        # Only on a terminal: `\r` does not overwrite in a captured log, it just adds noise,
        # and this runs captured more often than not.
        if sys.stdout.isatty():
            print(f"  ...           {gate.name}".ljust(72), end="\r", flush=True)
        row = run_gate(gate, extra_env)
        results.append(row)
        mark = {"PASS": "pass", "FAIL": "FAIL", "BLOCKED": "BLOCK"}[row["verdict"]]
        secs = f"{row['seconds']:>6.1f}s" if row.get("seconds") is not None else "      -"
        print(f"  {mark:<5} {secs}  {row['gate']}", flush=True)
        if row["detail"]:
            for line in [l for l in row.get("output", "").strip().split("\n") if l.strip()][-12:]:
                print(f"           {line[:200]}", flush=True)
    # The summary is derived from the rows, never recomputed from a second pass over the tree.
    if any(r["verdict"] == "FAIL" for r in results):
        verdict = "REJECT"
    elif any(r["verdict"] == "BLOCKED" for r in results) or not results:
        verdict = "BLOCKED"
    else:
        verdict = "ACCEPT"
    return verdict, results, who


EXIT = {"ACCEPT": 0, "REJECT": 1, "BLOCKED": 2}


def self_test() -> int:
    """Prove the three verdicts are reachable. A gate nobody has watched fail is not a gate."""
    failures = 0

    print("1. a failing gate must produce REJECT")
    probe = Gate("probe.fails", ["sh", "-c", "exit 3"], ROOT, needs="sh")
    row = run_gate(probe)
    ok = row["verdict"] == "FAIL" and row["rc"] == 3
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  {row['verdict']} rc={row['rc']}")

    print("2. a missing tool must produce BLOCKED, never PASS")
    row = run_gate(Gate("probe.missing", ["definitely-not-a-binary"], ROOT, needs="definitely-not-a-binary"))
    ok = row["verdict"] == "BLOCKED"
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  {row['verdict']}  {row['detail']}")

    print("3. a passing gate must produce PASS")
    row = run_gate(Gate("probe.passes", ["sh", "-c", "exit 0"], ROOT, needs="sh"))
    ok = row["verdict"] == "PASS"
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  {row['verdict']}")

    print("4. a tooling failure must read BLOCKED, and a real failure must not")
    cases = [
        ("Failed to create /x: Operation not permitted (os error 1)", True),
        ('Err value: Os { code: 1, kind: PermissionDenied, message: "Operation not permitted" }', True),
        ("error: Permission denied (os error 13)", True),
        ("error[E0308]: mismatched types", False),
        ("test result: FAILED. 1 passed; 1 failed", False),
    ]
    for output, want in cases:
        hit = any(marker in output for marker in TOOLING_FAILURE)
        ok = hit == want
        failures += 0 if ok else 1
        print(f"   {'ok' if ok else 'MISMATCH'}  {'BLOCKED' if hit else 'FAIL   '}  {output[:62]}")

    print("5. the exit code must carry the verdict, or this is not a gate")
    ok = EXIT == {"ACCEPT": 0, "REJECT": 1, "BLOCKED": 2}
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  {EXIT}")

    print("6. one FAIL among passes must still REJECT the whole run")
    rows = [{"verdict": "PASS"}, {"verdict": "FAIL"}, {"verdict": "PASS"}]
    derived = "REJECT" if any(r["verdict"] == "FAIL" for r in rows) else "ACCEPT"
    ok = derived == "REJECT"
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  {derived}")

    print("7. BLOCKED must not be reported as ACCEPT")
    rows = [{"verdict": "PASS"}, {"verdict": "BLOCKED"}]
    derived = ("REJECT" if any(r["verdict"] == "FAIL" for r in rows)
               else "BLOCKED" if any(r["verdict"] == "BLOCKED" for r in rows) else "ACCEPT")
    ok = derived == "BLOCKED"
    failures += 0 if ok else 1
    print(f"   {'ok' if ok else 'MISMATCH'}  {derived}")

    print(f"\nself-test: {'PASS' if not failures else f'FAIL ({failures})'}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", help="run just these gates, by name")
    parser.add_argument("--list", action="store_true", help="print the gate names and exit")
    parser.add_argument("--json", type=Path, help="write the full result here")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.list:
        for gate in gates():
            print(gate.name)
        return 0
    if args.self_test:
        return self_test()

    verdict, results, who = evaluate(args.only)
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in ("PASS", "FAIL", "BLOCKED")}
    print(f"\n{verdict}  ({counts['PASS']} pass, {counts['FAIL']} fail, {counts['BLOCKED']} blocked "
          f"of {len(results)})")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"verdict": verdict, "identity": who, "gates": results}, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main())
