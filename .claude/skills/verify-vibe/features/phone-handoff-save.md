# Phone handoff save

A phone records audio, streams it to the desktop over iroh, the desktop transcribes it and saves the
result. This is the `moveSourceMedia: true` branch — **the only save path that deletes the user's
source audio** (`fs.remove(input.sourcePath)`). A failure here is unrecoverable, not merely annoying.

> **Not yet driven.** Seeded from source reading. Unlike the other two it needs a one-time pairing
> and a first build of `handoff/probe`; budget for that before trusting the steps below.

## Why it is not a substitute for `transcribe-local-file`

`chore phone-probe` is genuinely attractive: it speaks the real wire protocol, hits the real
`saveTranscript` against the real filesystem, and needs no AX, no focus stealing and no keystrokes.
It is the right driver for *this* feature and the wrong one for the import bug.

`moveSourceMedia` forks the behaviour three ways inside `saveTranscript`
(`transcripts-store.ts:303-325`). On a failed media copy the **import** branch swallows the error and
still writes a record; the **handoff** branch aborts. Opposite behaviours through one function. It
also skips the entire upstream chain the import path uses — `pick_media_paths` → `enqueuePaths` →
`runLoop` → `persist` → `reconcileProjectName`. A transcript lost at any of those is invisible here.

## Sub-features

- `handoff-enable` — the endpoint binds and a pairing URL can be assembled.
- `handoff-receive` — the probe's audio arrives and transcribes.
- `handoff-save` — a project folder is written, with the record and the media copy.
- `handoff-source-removed` — the staged source is gone **and** there is no
  `failed to remove staged media` line in the log.
- `handoff-no-segments-no-save` — `handoff-transcript-saver.tsx` returns early on zero segments.
  Assert that **nothing** is written; this is the one place a tone fixture is the correct input.

## How to get to it (user POV)

`Settings → Phone`, enable handoff, scan the QR with a phone, record, send.

## Driving it with verify-vibe

Handoff is off by default and `restore_on_startup` only runs during `setup()`, so flipping
`handoff.enabled` in the config file needs a relaunch — which destroys a day of logs. **Prefer one
AX trip** through `Settings → Phone`, which costs nothing and destroys nothing.

```bash
$V press --run "$RUN" --name "Phone"      # or the Settings route; dump first to confirm
$V dump  --run "$RUN" --step 20-phone
```

Then assemble the pairing URL without a QR scan — the probe only parses the `#` fragment:

- endpoint id: the `handoff endpoint bound: <id>` line in the log
- token: `handoff.pairing.invitationToken` in `app_config.json`
- URL: `http://localhost:8088/#<endpoint_id>:<token>`

```bash
cp "$RUN/fixture-en.wav" "$RUN/fixture-handoff.wav"   # this branch DELETES what it is given
cargo run --quiet --manifest-path handoff/probe/Cargo.toml -- \
  --pair --device-token <fixed> --url 'http://localhost:8088/#<id>:<token>' \
  --file "$RUN/fixture-handoff.wav"

$V fs-snapshot     --run "$RUN" --step 21-handoff --projects "$PROJ"
$V frontend-errors --run "$RUN"
```

## Gotchas

- **Send a copy.** This branch deletes the file it is given.
- `refresh_invitation()` rotates the token after every successful pair. Re-read the config, or pin
  `--device-token`.
- `handoff/probe` has never been built in this checkout and declares its own detached workspace with
  `iroh` + full-feature `tokio`. Budget 3–6 minutes and network for the first build.
- `chore` may not be on PATH; invoke `cargo run --manifest-path handoff/probe/Cargo.toml` directly.
- The pairing secret lives in the URL *fragment* so it never reaches a server. Do not log the
  assembled URL into run evidence that might be shared.
