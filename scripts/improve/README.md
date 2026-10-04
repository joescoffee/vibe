# The regular improvement loop

A PDCA cycle over this repository: pick the next item, do it, prove it, keep or revert.

```
chore improve        what to do next, and which poteto-mode playbook it routes to
  ... do the work, following that playbook ...
chore gates          ACCEPT (exit 0) -> commit.  REJECT (1) or BLOCKED (2) -> revert.
```

Weekly, `.github/workflows/gates.yml` runs the same gate with nothing path-filtered out.

## Why this is not `/autofix`

The `autofix` skill describes this loop well and none of its machinery is installed. Measured on
2026-10-05:

| What `autofix/SKILL.md` instructs | Present |
|---|---|
| `scripts/autofix/` — `program.md`, `evaluate.sh`, `quality-backlog.json`, `safety.json`, `dashboard.js`, … | no |
| MCP tools `autofix_next_issue`, `_evaluate`, `_commit`, `_discard`, `_status`, `_scan` | none; `ToolSearch` matches nothing |
| `npm run autofix:start` and the other six scripts | no |
| Files the skill itself ships | 1 — `SKILL.md` |

So `/autofix` here is a document describing infrastructure that does not exist, which is the
failure mode this repository's own `CLAUDE.md` calls P5: a guard installed and never run. The
*shape* it describes is right, and this directory is that shape built from parts that do exist.

Two of its ideas were worth taking and are here: a backlog with an explicit next item, and a
commit gate that can say no. Two were not. Its ISO 25010 composite score is a single number over
four weighted dimensions, and a number that moves for several reasons tells you nothing about
which one moved — this loop reports per-gate verdicts instead. And its nightly unattended cadence
(36 cycles, 11pm to 5am) assumes the agent may decide anything in the backlog; here the items a
human owns are marked and skipped.

## The pieces

### `evaluate.py` — the gate

Sixteen checks across four Cargo workspaces and two Node projects. `chore ci` runs nine of them:
it never compiles `server/` and never reaches `handoff/`, and both have shipped defects that
nothing mechanical would have caught.

Every design rule in it comes from a failure this tree actually had:

- **The verdict is the exit code.** `verify-vibe`'s doctor check printed FAIL and exited 0, and a
  blind run drove straight through it. A lever that reports without exiting non-zero is not a gate.
- **Exit codes come from the command, not from a pipeline.** `cmd | tail; echo $?` reads `tail`'s
  status. That cost a day, twice.
- **"Did not measure" prints differently from "no problem".** A missing tool is BLOCKED, never
  PASS. `npx vitest --reporter=basic` once died in its loader while the harness reported exit 0,
  and that was read as a pass twice.
- **The subject names itself first.** Commit, branch, clean-or-dirty, unpushed count — before any
  verdict, so a result can never be attributed to the wrong revision.
- **It has been watched failing.** `--self-test` proves all three verdicts are reachable and that
  one FAIL among passes still rejects the run. It is part of `chore ci`, because a lever nobody
  checks is a lever that rots.

### `backlog.json` — what is left

Hand-written, not generated. Every item is something a review or a person actually found and
nobody has done, so an empty backlog means finished rather than unscanned.

`needs_human: true` marks what an agent must not decide alone — deleting source, moving a
deployment origin, touching the user's own transcripts, pushing. `chore improve` reports those and
picks something else.

### `route.py` — which playbook

Each item names a file under `~/.claude/skills/poteto-mode/playbooks/`. The router refuses three
of them in code: `autopilot-full`, `autopilot-stack` and `autonomous-run` each require the
metacognition Stop hook to be raised or disabled, and that hook caught six false greens in this
repository in a single day. The refusal is in `route.py` rather than in prose because the prose
version is already in `CLAUDE.md` and prose has failed to prevent this class of thing three times.

`--check` validates that every route resolves to a playbook that exists.

## Adding to the backlog

Append to `items` with: `id`, `title`, `found` (who or what found it — a review, a commit, a
person), `detail`, `class`, `routes_to`, and `needs_human`. Run `uv run scripts/improve/route.py
--check` to confirm the route resolves. `blocked_by` holds an item back without removing it.
