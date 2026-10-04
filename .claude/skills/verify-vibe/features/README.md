# Vibe feature map

What an agent can drive, how to reach it, and what proves it worked. One file per feature. This is
the maintained source of truth for coverage: a run that drives one convenient entry point is
incomplete while the map lists others.

| Feature | Why it is here | Driver |
|---|---|---|
| [transcribe-local-file](transcribe-local-file.md) | The path that lost a 1h51m transcript and then silently stopped failing. `moveSourceMedia: false` | AX + NSOpenPanel |
| [recents-round-trip](recents-round-trip.md) | The three most recent commits all live here; a half-applied rename is the shape of `e0875437` | AX |
| [phone-handoff-save](phone-handoff-save.md) | `moveSourceMedia: true` — the only save path that **deletes** the user's source audio | `handoff/probe`, headless |

## Baseline for every run

- Launch via `verify_vibe.py launch`. Never drive an instance this run did not start.
- `transcription.projectsPath` must point at the run's `projects/` directory. The user's
  `~/Documents/Vibe` holds real work and is off limits.
- Pin `general.displayLanguage`, then re-pin `transcription.modelOptions.lang`, then clear
  `transcription.modelOptions.init_prompt`. Read all three back from the file before driving —
  out of the `transcription.modelOptions` object, which is where they live. `app_config.json` is
  flat and those last two are paths into one key's value, not keys.
- Prefer `--class-contains`; accessible names are i18n messages and shift with translation progress.
- Restore `app_config.json` and `.window-state.json` in cleanup. Never delete the run directory.

## Known-unreachable entry points

These are reported `BLOCKED` on every run, deliberately. Marking an entry point honestly as
unverifiable beats letting a neighbouring check stand in for it.

| Entry point | Why it cannot be driven |
|---|---|
| Drag-and-drop onto the window (`use-drop-target.ts`) | `NSDraggingDestination`; AX cannot synthesize a drag |
| `open -b github.com.thewh1teagle.vibe <file>` | Dead code — `useDeepLinks`/`useSingleInstance` call `setFiles()` on `FilesProvider`, and **nothing reachable reads it back**. The earlier wording here said `pages/home` is unreachable; that is not true. An import-graph walk from `main.tsx` (192 files, dynamic `import()` included) reaches 2 of the 7 files under `pages/home/`, and one of them, `use-recording.ts`, is imported by the live `pages/main/session.tsx`. The correct statement is narrower and stronger: the one reachable `useFilesContext` consumer is `use-audio-download.ts:28`, which destructures `setFiles` only. The `files` state is write-only in all reachable code. Worth filing separately; it also contradicts what `CLAUDE.md` says about deep links. |

## Not yet mapped

- **Transcript export** (`use-transcript-export.ts` → `dialog.save()` → NSSavePanel). This is the
  documented recovery path when saving fails — the error modal tells the user to export before
  closing. If save and export can fail together, the work is gone. Next to add.
- **`import-save-large`.** The real incident was a 1h51m file with a ~1 GB media copy. A ten-second
  `say` fixture runs the same code but not the same durations or `fs.copyFile` volume. Treat as a
  manual/overnight tier; a fast green does not cover it.
