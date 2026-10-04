#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pick the next backlog item and say which playbook it routes to.

The half of `autofix` that is worth having without its infrastructure: a queue, a priority, and a
named next action. What it does not do is decide anything a human owns -- `needs_human` items are
reported and skipped, because deleting source, moving a deployment origin and touching the user's
own transcripts are not an agent's calls.

Three playbooks are refused outright. `autopilot-full`, `autopilot-stack` and `autonomous-run`
each state as a precondition that `METACOG_MIN_CHARS` be raised or the metacognition Stop hook
disabled. In this repository that hook caught six "the guard is installed but detects nothing at
the highest-risk point" false greens in one day. A routing table that can emit them is a routing
table that can turn itself off, so the refusal is here in code rather than in the prose of
CLAUDE.md, which has already failed to prevent this class of thing three times.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKLOG = HERE / "backlog.json"
PLAYBOOKS = Path.home() / ".claude" / "skills" / "poteto-mode" / "playbooks"

BANNED = {"autopilot-full.md", "autopilot-stack.md", "autonomous-run.md"}


def load() -> list[dict]:
    if not BACKLOG.is_file():
        raise SystemExit(f"BLOCKED  {BACKLOG} is missing; nothing was picked")
    return json.loads(BACKLOG.read_text(encoding="utf-8"))["items"]


def check_routes(items: list[dict]) -> list[str]:
    """Every route must name a playbook that exists and is not banned."""
    problems = []
    for item in items:
        target = item["routes_to"]
        if target in BANNED:
            problems.append(f"{item['id']} routes to {target}, which is banned in this repository")
        elif not (PLAYBOOKS / target).is_file():
            problems.append(f"{item['id']} routes to {target}, which does not exist under {PLAYBOOKS}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="list the whole backlog")
    parser.add_argument("--check", action="store_true", help="validate every route and exit")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        bad = [{"id": "probe", "routes_to": "autopilot-full.md"},
               {"id": "probe2", "routes_to": "no-such-playbook.md"}]
        problems = check_routes(bad)
        ok = len(problems) == 2 and "banned" in problems[0] and "does not exist" in problems[1]
        print(f"  {'ok' if ok else 'MISMATCH'}  a banned route and a missing route are both refused")
        good = check_routes(load())
        ok2 = good == []
        print(f"  {'ok' if ok2 else 'MISMATCH'}  the real backlog routes cleanly" + ("" if ok2 else f": {good}"))
        print(f"self-test: {'PASS' if ok and ok2 else 'FAIL'}")
        return 0 if (ok and ok2) else 1

    items = load()
    problems = check_routes(items)
    if problems:
        for p in problems:
            print(f"BLOCKED  {p}", file=sys.stderr)
        return 2
    if args.check:
        print(f"ok  {len(items)} items, every route resolves and none is banned")
        return 0

    agent_items = [i for i in items if not i["needs_human"]]
    human_items = [i for i in items if i["needs_human"]]

    if args.all:
        for item in items:
            owner = "HUMAN" if item["needs_human"] else "agent"
            print(f"  {owner}  {item['id']:28} {item['class']:20} -> {item['routes_to']}")
        print(f"\n{len(agent_items)} an agent may start, {len(human_items)} need a decision first")
        return 0

    waiting = [i for i in agent_items if not i.get("blocked_by")]
    if not waiting:
        # Empty and blocked read differently on purpose.
        if agent_items:
            print("BLOCKED  every agent-startable item is blocked:")
            for item in agent_items:
                print(f"           {item['id']}: {item['blocked_by']}")
            return 2
        print("BLOCKED  nothing in the backlog is an agent's to start; "
              f"{len(human_items)} item(s) are waiting on a decision")
        return 2

    item = waiting[0]
    print(f"next: {item['id']}")
    print(f"  {item['title']}")
    print(f"  found:    {item['found']}")
    print(f"  class:    {item['class']}")
    print(f"  playbook: {PLAYBOOKS / item['routes_to']}")
    print(f"\n  {item['detail']}")
    print(f"\n  Gate before committing:  uv run scripts/improve/evaluate.py")
    print(f"  ACCEPT exits 0, REJECT 1, BLOCKED 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
