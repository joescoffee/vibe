# Transcribe a local file

Importing an audio or video file from disk transcribes it and saves the result as a project folder
in the transcripts store, so it is still there after a restart.

This is the path where five transcriptions finished on 2026-09-15 and produced no project folder at
all, including a 1h51m meeting recording. It later stopped failing without the cause ever being
found. Nothing here is a hypothetical risk.

## Sub-features

- `import-browse` — the drop zone opens the native picker and accepts a file.
- `import-transcribe` — the queue starts with no further input and runs to completion. There is no
  separate "Transcribe" press: `enqueue` calls `runLoop()` itself.
- `import-save` — the finished transcript lands on disk as
  `<name>-<yyyyMMdd-HHmmss>/transcript.vibe.json` plus `audio.<ext>`.
- `import-source-preserved` — the user's original file is **not** moved or deleted
  (`moveSourceMedia` is false for imports).
- `import-save-failure-is-loud` — when saving fails, the error modal appears **and** a
  `vibe::frontend` line reaches the log.
- `import-drop` — **not drivable.** `NSDraggingDestination`. Report `BLOCKED` every run; never let
  `import-browse` stand in for it.
- `import-save-large` — manual/overnight tier, see the map README.

## How to get to it (user POV)

- On the idle screen, with the `File` tab selected, click the dashed drop zone
  (`將音訊、影片或資料夾拖曳至此 or click to browse your files` — the label is half-translated).
- Or drag a file onto the window.
- If a transcript is already open, click `New transcription` in the sidebar first.

## Driving it with verify-vibe

Preconditions: gate 2 green; `$RUN/projects` empty; `$RUN/fixture-en.wav` produced by `say`;
`transcription.saveTranscripts` true.

```bash
V=./.claude/skills/verify-vibe/verify_vibe.py
PROJ="$(pwd)/$RUN/projects"

$V dump        --run "$RUN" --step 01-idle
$V press       --run "$RUN" --class-contains "border-dashed"
$V open-panel  --run "$RUN" --path "$(pwd)/$RUN/fixture-en.wav"
$V wait-log    --run "$RUN" --pattern "transcription complete" --timeout 600
$V dump        --run "$RUN" --step 03-done
$V fs-snapshot --run "$RUN" --step 03-done --projects "$PROJ" --expect-records 1
$V frontend-errors --run "$RUN"
```

What proves it (all four, not any one):

| Oracle | Assertion |
|---|---|
| Screen | `03-done.ax.txt` contains the transcript text |
| Disk | exactly one `projects/*/transcript.vibe.json`; `.segments` ≥ 1; `.audioFile` present |
| Content | keyword hits counted with a denominator, e.g. `4 of 5` from `quick/brown/fox/lazy/dog` |
| Source | `$RUN/fixture-en.wav` still exists |
| Log | zero `vibe::frontend` lines |

Verified run: `File-fixture-en-20261002-010051/` holding `transcript.vibe.json` and `audio.wav`,
1 segment, `4 of 5` keywords (whisper heard "dob" for "dog"), fixture intact, log silent.

## Gotchas

- **A tone fixture makes this check vacuous.** Zero segments → `persist()` returns at
  `use-transcribe-queue.ts:349` → `saveTranscript` is never called → green having proven nothing.
- `transcription.modelOptions.init_prompt` ships as a Traditional Chinese glossary and will skew an
  English fixture.
- The fixture path must be ASCII; `keystroke` cannot type non-ASCII reliably.
- `persist()` is fire-and-forget. `transcription complete` does **not** mean saved. Poll the disk.
- `copySourceMedia` failure is **swallowed** on this branch (`transcripts-store.ts:303-307`): a
  record can exist with no `audioFile`. Assert `audioFile`, not merely that the record exists.
- The open panel is application-modal; Tauri IPC is blocked while it is up. Do not interleave other
  harness calls, and never type before both guards in `open-panel` have passed.
- `reserveProjectFolder` inserts `-2-` on a name collision. Match by glob, not by predicted name.
- The first run after a cold start loads a 1.6 GB model. Budget the timeout accordingly.
