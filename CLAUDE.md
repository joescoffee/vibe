# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` covers working style, file-size limits and delegation; this file covers commands and architecture. `server/AGENTS.md` applies inside `server/`.

## Commands

Tasks run through `chore` (see `chore list`). JS is `pnpm` only; Python scripts are standalone `uv` scripts.

```bash
chore setup          # REQUIRED before dev/build — downloads the vibe-server + ffmpeg sidecars
chore dev            # setup, then `pnpm exec tauri dev`
chore dev-web        # frontend only in a browser, with Tauri mocked (see src/mock-tauri/)
chore build          # setup, then `pnpm exec tauri build`
chore ci             # lint → check-types → check-i18n → test
```

`chore setup` is idempotent but checks for *both* the server and ffmpeg; a half-populated
`desktop/src-tauri/binaries/` fails much later during the Tauri build.

Individual gates:

```bash
chore lint           # eslint, then cargo fmt --check and cargo clippy -D warnings
chore check-types    # tsc --noEmit over desktop/ and website/
chore check-i18n     # uv run scripts/check_i18n.py
chore test           # cd desktop && pnpm test  →  vitest run
```

A single frontend test:

```bash
cd desktop && pnpm exec vitest run src/lib/transcript-export.test.ts
cd desktop && pnpm exec vitest run -t 'escapes HTML data'      # by test name
```

There is no vitest config — it runs on defaults. Tests needing DOM opt in per-file with a
`// @vitest-environment jsdom` docblock on line 1. Mock Tauri per-file with
`vi.mock('@tauri-apps/api/core', …)`; `src/mock-tauri/` is **not** a test harness — it exists only
for `chore dev-web` and no test imports it.

**Rust tests are never executed by any tooling.** No `chore` task and no workflow runs `cargo test`,
so the ~120 existing `#[test]` functions are documentation until someone adds a runner. Write them
if they earn their place, but do not treat one as a regression guard.

## Two Cargo workspaces, deliberately

The root `Cargo.toml` has `members = ["desktop/src-tauri", "crates/meeting-detect"]` and
`exclude = ["server"]`. `server/` is its own workspace because, as the comment says, the engine
optimises for speed and the app for size — the root sets `opt-level = "s"`, `lto`, `panic = "abort"`.

Consequences worth knowing before you go looking for a lint failure:

- `cargo clippy` from the root covers only `desktop/src-tauri` and `crates/meeting-detect`.
  `server/`, `handoff/wasm` and `handoff/probe` are outside it and are never linted by CI.
- `server/` has its own `chorefile`, so `chore list` there shows a different task set.
- `server/` needs `chore fetch-libs` before it will build at all.

## The sidecar is the transcription engine

The desktop app does almost no transcription itself. `vibe-server` is a separate binary, bundled as
a Tauri `externalBin`, versioned independently and pinned by `.server-version`. `chore setup`
downloads that pinned release. Engine fixes only reach users through a new *server* release, which
is why the release skill checks `server/` for changes first.

The desktop reaches it two ways, and both matter when tracing a bug:

- **HTTP** — `start_api_server` spawns it as `serve` on `127.0.0.1` with an OS-assigned port
  (`--port 0`). The live URL is written back into `app_config.json` under `api.baseUrl`. There is
  no auth and no CORS layer on that server.
- **argv passthrough** — `cli.rs` forwards the process's own arguments to the sidecar verbatim.

That second path has a sharp edge: `is_cli_args` treats **any** argv other than `--hidden` as CLI
mode, and `setup.rs` branches on it *before* creating the main window. So a cold start with a
`vibe://` URL or a file path in argv creates no window and hands the argument to the sidecar. This
is why deep links have three separate implementations — `onOpenUrl` (macOS), the `single-instance`
event (Windows/Linux, app already running), and `invoke('get_argv')` (cold start, currently
unreachable for exactly this reason).

## The sidecar in `binaries/` is built from this tree, not downloaded

`.server-version` still says `v0.6.10`, and `chore setup` still knows how to fetch that release,
but the binary staged in `desktop/src-tauri/binaries/` is built from `server/` in this repo. It
carries a patch to `whisper-rs`'s rolling text context that upstream does not have: a window whose
words merely repeat the history no longer extends it, which is what stops a hallucination over a
silent opening from conditioning the rest of a long file into the same repeated line.

So `.server-version` describes the *baseline*, not what ships. To rebuild after touching `server/`:

```bash
cd server
cargo build -p vibe-server --release          # needs libs/lib, see below
cp target/release/vibe-server ../desktop/src-tauri/binaries/vibe-server-aarch64-apple-darwin
chmod 755 ../desktop/src-tauri/binaries/vibe-server-aarch64-apple-darwin
```

`chore server-build` does exactly this and takes the target triple as an argument.

Two traps:

- **`server/libs/lib` is gitignored and starts out absent**, and `ggml-rs-sys/build.rs` panics
  without it rather than skipping. Populate it once with `chore fetch-libs`, or by hand from
  `libraries-ggml-$(cat server/libs/ggml-version)-r$(cat server/libs/revision)` on the upstream
  releases page. Nothing else native is built locally — the ggml static libraries are prebuilt and
  about 1.2 MB.
- **`chore setup` returns early only while both sidecars are present.** Delete `binaries/` and the
  next `chore build` silently restores upstream's binary, and the patch is gone with no warning.

`server/` is also outside every lint and test job: `cargo clippy -p whisper-rs` currently fails on
two pre-existing `chunks_exact` findings in `model.rs` and `model_file.rs` that have nothing to do
with any of this.

## Phone handoff

`handoff/` is a PWA that records on a phone and streams audio to the desktop over **iroh** (QUIC
p2p), which the desktop transcribes and streams back. The endpoint is not bound at startup — the
user opts in, and `handoff.enabled` persists that.

`handoff/devices.rs` is the reference for how this codebase does trust boundaries: tokens SHA-256
hashed at rest, `subtle::ConstantTimeEq` comparisons, invitations that rotate after each pairing and
cannot themselves authorize transcription, and `transfer.rs` bounds every length before allocating
from it. Match this standard when touching anything that parses untrusted input.

The pairing QR encodes `<pwa_origin>/#<endpoint_id>:<token>` — the secret is in the URL *fragment*
so it never reaches the PWA's server.

## i18n

`i18n/` is the source of truth: `desktop/`, `website/`, `changelog/` and `docs/` catalogs, with
`i18n/locales.json` as the roster. Never edit `en-US`. A missing translation falls back to English
per entry, so partial coverage ships safely.

Adding a catalog key needs `pnpm i18n:generate` (paraglide compile) in `desktop/` and `website/`
before type-checking passes — `pnpm build` runs it, `tsc` alone does not. `chore stamp-translations`
writes a source hash into each translation so a later English edit surfaces as stale.

## Config

One store: `app_config.json` via `tauri-plugin-store`, in `app_config_dir()`. Keys are namespaced
and centralised in `desktop/src/lib/config-keys.ts` — add new ones there, not inline.

`config_watcher.rs` watches the file and emits the **entire contents** to every webview as a
`config-changed` event when it is edited externally. The file holds `model.path`, `models_folder`,
`api.baseUrl`, handoff device credentials and the LLM API keys, so treat it as sensitive.

## The updater points at this fork, on purpose

`tauri.conf.json` `plugins.updater.endpoints` is the **fork's** releases URL, not
`thewh1teagle/vibe`. `tauri.conf.json` is strict JSON and cannot carry the comment, so it lives
here.

This fork carries security patches upstream has not taken: the Tauri bump past CVE-2026-42184, the
`on_navigation` guard, the markdown-link interception and the yt-dlp `--` separator. The updater
plugin is still registered (`main.rs:76`) and `providers/updater.tsx:74-87` calls `check()` on
every launch, so with the upstream endpoint an update offer would eventually appear — and would
*install*, because the embedded `pubkey` is upstream's and their artifacts validate against it.
Accepting it replaces a patched build with an unpatched one.

It was latent rather than live: upstream's `latest.json` advertised 3.2.2, the same version as
this tree, so no prompt fired. It would have fired on upstream's next release.

The fork publishes no releases, so `check()` now gets a 404 and the `catch` at `:83` swallows it.
That is the intended steady state. Anyone who later publishes releases from this fork must also
replace `pubkey` — the current one belongs to upstream's signing key, so fork-signed artifacts
would be rejected.

## CI reality

Only `lint_rust.yml` runs on pull requests, and only `fmt` + `clippy`. `pnpm test`, `check-types`
and `check-i18n` have no CI counterpart despite the `chorefile` comment claiming CI calls the same
tasks. Its path filter also lists `.github/workflows/lint.yml` and `cli/src/**`, neither of which
exists — so changes under `handoff/`, `server/` or `crates/` do not trigger clippy either.

Run `chore ci` locally; do not rely on the PR checks to catch you.

## Troubleshooting

Things that cost hours here once, mostly because the failure looks like a result rather than a
failure.

| 問題 | 原因 | 解法 |
|---|---|---|
| 對 sidecar 的轉錄請求回傳空字串 | server 已死，curl 得到的是連線失敗而非空結果 | 檢查 HTTP 狀態碼；讓 `FAIL` 與 `0 bytes` 印出不同字串，否則兩者無法區分 |
| 手動啟動的 `vibe-server` 在下一次指令就消失 | 背景行程會在工具呼叫之間被終止 | 啟動、載入模型、發送請求全部放在同一次呼叫內；取得結果後 `kill -0` 再確認一次 |
| `cargo build` 在 `server/` 直接 panic | `libs/lib` 被 gitignore 且初始不存在，`ggml-rs-sys/build.rs` 選擇 panic 而非略過 | `chore fetch-libs`，或手動取 `libraries-ggml-$(cat server/libs/ggml-version)-r$(cat server/libs/revision)` |
| 自建 sidecar 的修補突然消失 | 刪掉 `binaries/` 後 `chore build` 會無聲還原上游二進位 | 用 `chore server-build`，不要用 `chore setup` |
| `cargo build` 在沙箱下 EPERM | build script 無法寫入專案內的 `target/` | 把 `CARGO_TARGET_DIR` 指向 scratchpad |
| app 自報的 `COMMIT HASH` 落後於 `git HEAD` | `build.rs` 只宣告 `rerun-if-env-changed`，沒把 `.git/HEAD` 列為相依 | 建置前 `touch desktop/src-tauri/build.rs` |

## Repo-local skills

`.claude/skills/` ships three: `translate` (fans one subagent per locale over `i18n/`), `release`
(seven gated steps, including a YubiKey sign server behind a Cloudflare tunnel), and `analytics`
(Aptabase export via `scripts/export_analytics.py`). They load automatically in this repo.

Separately, `cmd/skill.rs` lets the *app* install a skill into the user's home
(`~/.claude/skills/vibe/SKILL.md` or `~/.codex/…`), composed in `desktop/src/lib/skill.ts` from the
sidecar's `/skill` endpoint. That path deliberately bypasses the fs plugin scope, and its body is
whatever `api.baseUrl` served — worth knowing before changing either end.
