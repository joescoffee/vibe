# Recents round trip

Opening, renaming and deleting a saved project from the sidebar. This is the second user-facing
view onto the transcripts store, and the only one that can show a save was *durable* rather than
merely written.

> **Not yet driven.** Seeded from source reading; the harness primitives it needs all exist and are
> proven, but no run has executed this file end to end. Treat the steps below as a draft until one
> has, and fix what the first run disproves.

The three most recent commits touching the store all live here: `e0875437` (a re-run that cannot
write back to its project folder now says so), `2e3666bb` (a transcript that fails to save says so),
`84797d3f`. A rename that updates the folder but not `.name` inside `transcript.vibe.json` is the
exact shape of `e0875437`.

## Sub-features

- `recents-list` — a saved project appears in the sidebar.
- `recents-open` — selecting it loads the transcript, and what is on screen matches the record on
  disk.
- `recents-rename` — `reconcileProjectName` + `renameTranscript`: the folder is renamed **and**
  `.name` inside the record is updated. Both, or it is a `FAIL`.
- `recents-rerun-writeback` — re-transcribing a project that already has a `savedPath` takes the
  `updateTranscriptSegments` branch, not `persist()`. If the folder has been moved or deleted since,
  the error modal must appear and a `vibe::frontend` line must reach the log.
- `recents-delete` — the project is removed from disk.
- `recents-legacy-flat` — a hand-placed `<name>-<stamp>.vibe.json` (no folder) must still list, open
  and delete.

## How to get to it (user POV)

- Click `Toggle recents` in the sidebar.
- Click a project entry under `RECENTS`.
- `Transcript actions` → Rename / Delete, or edit the project name inline.

## Driving it with verify-vibe

Preconditions: gate 2 green, and feature `transcribe-local-file` has already produced one project in
`$RUN/projects` — that project is the only expected entry, because changing `projectsPath` empties
the list.

```bash
$V press  --run "$RUN" --name "Toggle recents"
$V dump   --run "$RUN" --step 10-recents
$V exists --run "$RUN" --name-contains "fixture-en"
$V press  --run "$RUN" --name-contains "fixture-en"
$V dump   --run "$RUN" --step 11-opened
$V press  --run "$RUN" --name "Transcript actions"
$V dump   --run "$RUN" --step 12-actions-menu     # read the menu item names before pressing
```

After a rename, assert **both** halves:

```bash
$V fs-snapshot --run "$RUN" --step 13-renamed --projects "$PROJ"
# and: jq -r .name "$PROJ"/<new-folder>/transcript.vibe.json  ==  the new name
```

## Gotchas

- **The list is virtualized.** An off-screen row is *absent* from the AX tree, not merely
  unreadable. `AXScrollToVisible` before asserting absence, or the absence is a false negative.
- **Delete goes through a native dialog** (`dialog.ask`), whose buttons are localized by the system
  locale. Confirm with `key code 36`, never by matching a button title.
- `Transcript actions` opens a menu; its items are a separate AX subtree from the web area. Dump
  before pressing rather than guessing item names.
- Renaming is asynchronous and serialized behind `serializeProjectOperation`. Re-read the live
  `savedPath` after a rename instead of reusing the one you captured earlier.
- `recents-rerun-writeback` is the one sub-feature that needs a *destructive* precondition: move the
  project folder aside between the save and the re-run. Do that inside `$RUN/projects` only.
