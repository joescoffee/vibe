---
name: verify-vibe
description: "Drive the installed Vibe desktop app (macOS, Tauri + WKWebView) through Accessibility and prove what it did: launch it, import and transcribe a real file, and check the result on screen, on disk and in the log. Use when a change touches transcription, the transcripts store, Recents or handoff, or when a bug report cannot be reproduced by reading code."
---

# verify-vibe

Vibe's transcription half is scriptable three ways. Its **save** half is not: `saveTranscript()`
lives only in TypeScript (`desktop/src/lib/transcripts-store.ts:285`) and every caller is a React
component. That gap is why a finished 1h51m transcript that never reached `~/Documents/Vibe` could
not be reproduced by any agent, and why it later stopped happening without anyone learning the
cause. This skill closes the gap by driving the real app.

Read this cold, mid-task, before touching the app. Every claim below was executed against a live
instance, not inferred.

## The run directory

Everything a run produces goes in one place, created before anything else:

```
plans/verify-vibe/runs/<RUN_ID>/
├── launch.json  vibe.pid  verdict.txt
├── app_config.before.json      window-state.before.json
├── fixture-en.wav  fixture-en.wav.sha256
├── logs/pre/     every log_*.txt, copied BEFORE launch   ← the point of gate 0
├── logs/post/    every log_*.txt, copied after teardown
├── ax/<step>.ax.txt      depth⇥role⇥title⇥description⇥value⇥domClassList
├── shell/<ts>-<step>.json  {argv, rc, stdout, stderr, wall_ms} for every external command
├── fs/<step>.fs.json     path, size, sha256, mtime under the run's projects dir
└── projects/             where this run's transcripts are written
```

It lives under `plans/` because that is already where this repo keeps validation artifacts
(`AGENTS.md`). **Run directories are local evidence. Never attach one to a PR** — `CONTRIBUTE.md`
is explicit that helper output does not belong there.

## Three verdicts, never two

```
PASS    <id> — <observed>, rc=0, source=<artifact>
FAIL    <id> — expected <X>, observed <Y>, rc=0, source=<artifact>
BLOCKED <id> — <why it could not run>, rc=<n>, source=<artifact>
```

`verify_vibe.py` exits **0 on PASS, 1 on FAIL, 2 on BLOCKED**. The distinction is the reason this
skill exists: a `curl` that failed to connect once returned zero bytes and was reported as
"0 characters", which read exactly like a successful-but-empty transcript.

Rules that keep it from collapsing back into two:

- **Exit code is asserted before content.** Every external call goes through `run_shell`, which
  records `rc` as a field separate from the body. When `rc != 0` the body is not interpreted.
- **Every number carries its denominator and its source.** Not `segments=0`. `records=1 of
  expected=1, source=fs/03-done.fs.json`.
- **"Not found" must first prove the instrument still works.** When an element lookup fails,
  `press` and `exists` re-resolve the control element `Toggle recents`. If that resolves, the
  element is genuinely absent → `FAIL`. If it does not, the harness lost the web area or lost
  accessibility → `BLOCKED`. Never report absence without this.
- **Any `BLOCKED` in a step makes the step `BLOCKED`.** Mixed verdicts resolve pessimistically.

## Launch

```bash
V=./.claude/skills/verify-vibe/verify_vibe.py
RUN="plans/verify-vibe/runs/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RUN/projects"
PROJ="$(pwd)/$RUN/projects"

$V snapshot-evidence --run "$RUN"                      # gate 0 — must be first
$V fixture --out "$RUN/fixture-en.wav"
$V config-set --run "$RUN" --key transcription.projectsPath --json "\"$PROJ\""
$V launch  --run "$RUN"                                # gate 1
$V doctor  --run "$RUN" --expect-commit "$(git rev-parse --short=8 HEAD)" --projects "$PROJ"
```

### Gate 0 comes first because launching destroys evidence

`clean_old_logs` (`desktop/src-tauri/src/cleaner.rs:6-34`) is called from `setup.rs:44`, which runs
**before** the CLI branch at `setup.rs:123`. So *any* execution of the bundle — including
`vibe transcribe …`, which never opens a window — deletes every `log_*.txt` except the current one.
Yesterday's evidence is destroyed by the act of reproducing the bug.

`snapshot-evidence` also refuses to continue when a `vibe` is already running, because that
instance may hold the only copy of what you are about to delete. Quit it deliberately first.

### "Today's log" is the wrong file

The log filename is fixed from `Local::now()` at launch and never rotates. An instance started on
the 30th is still appending to `log_2026-09-30.txt` on the 1st. Always take the newest by mtime
across `log_*.txt`; never construct `log_$(date +%F).txt`.

Worse, and found by running this skill against itself: right after launch the newest file can still
be the **previous** session's, whose historical `COMMIT HASH` / `vibe-server ready on port` lines
match the readiness check happily. The first version of this harness reported a dead session's port
as live. Every log read is therefore filtered by the launch timestamp recorded in `launch.json`.

### Why the raw binary and not `open`

Empty argv keeps GUI mode — `is_cli_args([])` is false (`cli.rs:40-47`); any other argument routes
to the sidecar and creates no window. `open` also gives you no PID you own, and you need one,
because `tauri_plugin_single_instance` means that if a `vibe` is already running your exec forwards
its argv and exits immediately. Your recorded PID then dies while a `vibe` keeps running. `launch`
detects exactly that and reports `BLOCKED`, not a crash.

### Readiness: three markers, all required

| Marker | Target | Proves |
|---|---|---|
| `COMMIT HASH: <sha>` | `vibe::setup` (`setup.rs:119`) | build identity |
| `vibe-server ready on port N` | `vibe::server::process` (`process.rs:264`) | sidecar up, and the live port |
| `checking path …/crash.txt` | `vibe::cmd::app` (`app.rs:144`) | **React actually mounted** |

The third is the only line that proves the webview is alive; `Non CLI mode` only proves a window
was requested.

The sidecar port comes from the log, **not** from `app_config.json` — there is no `api.baseUrl` key
unless the user has switched the API server on.

## Doctor

`doctor` is read-only and answers "is this instance worth driving?". Seven checks:

| Check | Blocks when |
|---|---|
| `ax.trusted` | the controlling app lacks Accessibility permission — otherwise "element not found" and "not permitted" are indistinguishable |
| `process.ours` | our pid is gone, or more than one `vibe` is running |
| `build.identity` | the log's `COMMIT HASH` is not the one you asked for — driving a stale build proves nothing about your change |
| `sidecar.health` | no port line, or `curl` could not connect (`rc=7`), or not HTTP 200 |
| `webview.mounted` | no `crash.txt` probe |
| `ax.reachable` | the control element `Toggle recents` does not resolve |
| `isolation.projects` | `transcription.projectsPath` is not the run's directory, or `saveTranscripts` is off |

The last one is not optional. `~/Documents/Vibe` holds the user's real work. A verification run must
never write there.

### The gate is enforced, not advisory

`doctor` writes its verdict to `gate2.json`, and `press` and `open-panel` refuse to run unless that
file says `PASS` — exit 2, no AppleScript sent. `dump` and `exists` stay open on purpose, so a
failed doctor can still be diagnosed.

This is not belt-and-braces. A blind run of this skill by an agent that had never seen it drove the
app through a **failed** `build.identity`: doctor exited 1, the run carried on, and the final report
read clean. The guard fired and changed nothing, which is the exact defect this skill exists to
catch, in the skill itself.

To proceed through a failure knowingly:

```bash
$V doctor --run "$RUN" --expect-commit "$(git rev-parse --short=8 HEAD)" --projects "$PROJ" \
   --override "installed build is N commits behind; those commits touch only CI and docs"
```

The reason lands in `verdict.txt` as an `OVERRIDE` line and in `gate2.json`, so a reader sees what
was waived and why instead of having to take the prose on trust.

**Only `build.identity` can be waived.** Driving a knowingly stale build is a defensible call;
everything else that fails means driving is either impossible or would write where it must not.
`isolation.projects` in particular is the one check standing between a run and the user's real
transcripts, and `--override` is refused outright when it is among the failures. `config_watcher.rs` reloads external edits live, so `config-set` redirects it
**without a relaunch** — which matters precisely because every relaunch destroys a day of logs.

The `pgrep` pattern is anchored (`^…/vibe$`). Unanchored it also matches the `vibe-server` sidecar,
which made `process.ours` report "more than one vibe running" on a perfectly normal launch.

## Drive

```bash
$V dump        --run "$RUN" --step 01-idle
$V press       --run "$RUN" --class-contains "border-dashed"
$V open-panel  --run "$RUN" --path "$(pwd)/$RUN/fixture-en.wav"
$V wait-log    --run "$RUN" --pattern "transcription complete" --timeout 600
$V fs-snapshot --run "$RUN" --step 03-done --projects "$PROJ" --expect-records 1
$V frontend-errors --run "$RUN"
```

### Selectors

The AX tree does expose the DOM: `window 1 → AXGroup → AXScrollArea → AXWebArea`, and below that
real `AXButton` / `AXCheckBox` / `AXPopUpButton` nodes, each with `AXPress`. Driving is
`perform action "AXPress"` — no mouse, no coordinates.

Prefer selectors in this order:

1. **`--class-contains`** — `AXDOMClassList` returns the Tailwind classes verbatim and is
   **language-independent**. Use it for anything whose label is user-facing prose.
2. **`--name` / `--name-contains`** — accessible names come from `aria-label={m.…()}`, so they are
   i18n messages. They shift with the display language *and* with translation progress. The drop
   zone currently reads `將音訊、影片或資料夾拖曳至此 or click to browse your files` — half
   translated. Treat every name as volatile.
3. **Never coordinates.** The window sits at negative coordinates across a multi-display setup and
   `cliclick` is not installed. `AXScrollToVisible` before pressing; the Recents list is
   virtualized, so an off-screen row is *absent*, not merely unreadable.

`entire contents` returns 0 elements for this process, so every walk is manual recursion. A full
dump is ~20-130 nodes; a targeted find is well under a second.

### The open panel is the load-bearing step

`cmd/files.rs` calls `panel.runModal()` — application-modal, so Tauri IPC is blocked while it is up
and only AX keystrokes can drive it. Keystrokes go to whatever is frontmost, so `open-panel` must
make Vibe frontmost and **two guards gate every keystroke**: it waits for a window of this process
not named `Vibe`, then for the Go-to-Folder sheet, before typing anything. Without them the path
text lands in whatever the user was editing.

> **This step steals focus and types. Do not run it unattended on a machine someone is using.**

`keystroke` cannot reliably type non-ASCII, so **fixture paths must be ASCII** — which also rules
out driving against `~/Documents/Vibe`, where project names contain CJK and apostrophes.

### The fixture must be real speech

`fixture` uses `/usr/bin/say`. Do not substitute a synthetic tone. A tone transcribes to zero
segments, `persist()` returns early at `use-transcribe-queue.ts:349`
(`segments.length === 0`), and **`saveTranscript` is never called** — the run goes green having
exercised nothing, which is the exact failure this skill exists to prevent.

Two config landmines before the first run, both live in the shipped `app_config.json`:

- `transcription.modelOptions.init_prompt` is a long Traditional Chinese BCM glossary and will skew
  an English fixture. Clear it for the run and restore it after.
- Writing `general.displayLanguage` **also rewrites the transcription language**:
  `preference.tsx:371-376` runs `setModelOptions({… lang: displayLanguage.split('-')[0]})` on every
  locale change. Pin the display language first, then re-pin `transcription.modelOptions.lang`, then
  read both back from the file before driving.

## Evidence

A step is proven by three oracles together, not one:

- **On screen** — an `ax/<step>.ax.txt` dump showing the expected state.
- **On disk** — `fs/<step>.fs.json` with size and sha256. Match project folders by glob, never by
  predicted name: `reserveProjectFolder` inserts `-2-` on a collision.
- **In the log** — `frontend-errors` must find **no** `vibe::frontend` lines. That target is written
  only by `log_frontend_error` (`cmd/app.rs`), whose single caller is `reportFailure` in
  `transcripts-store.ts`. A hit there is a swallowed save failure, and is a `FAIL` with the message
  quoted.

A progress bar is not an oracle. `persist()` is fire-and-forget, so `transcription complete` does
not mean saved — poll the filesystem.

Assert content, not just existence. The `say` fixture gives a known sentence, so count keyword hits
with a denominator (`4 of 5`) rather than asserting a non-empty string. Also assert the source file
still exists: the import path must not move the user's media.

## Cleanup

```bash
$V quit --run "$RUN"
```

Graceful quit via the native `vibe → Quit vibe` menu item — the one native handle that stays English
under a zh-TW system locale — targeted by unix id, then SIGTERM only if that times out. Graceful
first because `main.rs` drops the handoff router and flushes analytics on exit; `kill -9` would hide
shutdown bugs.

**Never `pkill -f vibe`.** The user's own instance, a `vibe-server serve`, and an unrelated
checkout's `tauri dev` all match. `quit` kills the pid it recorded and reports orphans by `pgrep -P`.

Teardown restores `app_config.json` and `.window-state.json` from the gate-0 backups, then copies
the post-run logs. It is green only when the pid is gone, there are no orphans, and **`logs/pre/`
still exists with its contents** — a cleanup that eats the proof fails this gate.

## Feature map

[`features/README.md`](features/README.md) indexes what is covered and what is not. The map is the
maintained source of truth: a proof that drives one convenient entry point is incomplete when the
map lists others. Keep it honest with `/maintain-verification-skill`.

Entry points that cannot be driven are printed as `BLOCKED` on every run, forever, by design —
drag-and-drop uses `NSDraggingDestination`, which AX cannot synthesize. A loudly blocked map entry
is honest; a silently omitted one is the failure mode.
