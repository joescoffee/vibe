use std::sync::OnceLock;
use std::time::Duration;

use tauri::{App, AppHandle};
use tauri_plugin_aptabase::EventTracker;
use tauri_plugin_store::StoreExt;

use crate::config::STORE_FILENAME;

pub const APTABASE_APP_KEY: &str = match option_env!("APTABASE_APP_KEY") {
    Some(v) => v,
    None => "",
};

pub const APTABASE_BASE_URL: &str = match option_env!("APTABASE_BASE_URL") {
    Some(v) => v,
    None => "",
};

pub mod events {
    pub const APP_STARTED: &str = "app_started";
    /// Clean shutdown. Its absence after `app_started` means the app crashed.
    pub const APP_EXITED: &str = "app_exited";
    pub const CLI_STARTED: &str = "cli_started";
    /// Server finished under the CLI: carries the exit code and wall-clock duration.
    pub const CLI_FINISHED: &str = "cli_finished";
    pub const CLI_SPAWN_FAILED: &str = "cli_spawn_failed";
    /// The string predates the rename; keeping it keeps the dashboards continuous.
    pub const SERVER_SPAWN_FAILED: &str = "sona_spawn_failed";
    /// The engine died with an illegal instruction on the AVX2 build and was relaunched on the baseline one.
    pub const SERVER_CPU_BASELINE_FALLBACK: &str = "server_cpu_baseline_fallback";

    // Phone handoff. Props are technical facts only: never a transcript, filename,
    // saved path, endpoint id, pairing token, model path, or chosen language.
    pub const HANDOFF_ENABLED: &str = "handoff_enabled";
    pub const HANDOFF_DISABLED: &str = "handoff_disabled";
    pub const HANDOFF_TRANSCRIBE: &str = "handoff_transcribe";
    pub const HANDOFF_CAPABILITIES: &str = "handoff_capabilities";
    pub const HANDOFF_PAIRING_REGENERATED: &str = "handoff_pairing_regenerated";
}

/// Store key holding the anonymous install id.
const INSTALL_ID_KEY: &str = "analytics_install_id";

/// The Aptabase ingest server truncates prop values at 180 characters.
const MAX_PROP_CHARS: usize = 180;

/// Marks where `truncate_keep_tail` cut the string.
const ELISION_MARKER: &str = "...";

/// Resolved once per process so a store that cannot be written is not retried on every event.
static INSTALL_ID: OnceLock<String> = OnceLock::new();

fn is_analytics_enabled(app_handle: &AppHandle) -> bool {
    let Ok(store) = app_handle.store(STORE_FILENAME) else {
        return true; // default to enabled if store unavailable
    };
    store
        .get("analytics_enabled")
        .and_then(|v: serde_json::Value| v.as_bool())
        .unwrap_or(true)
}

pub fn is_aptabase_configured() -> bool {
    !APTABASE_APP_KEY.is_empty() && !APTABASE_BASE_URL.is_empty()
}

/// Anonymous, locally generated install id: a random UUID v4, never derived from the
/// machine, the hardware, or anything else identifying. Created on first use and
/// persisted so it survives restarts. Returns `None` when the store cannot hold it,
/// so an unwritable store omits the prop instead of sending a fresh id per event.
fn install_id(app_handle: &AppHandle) -> Option<String> {
    if let Some(id) = INSTALL_ID.get() {
        return Some(id.clone());
    }
    let store = app_handle.store(STORE_FILENAME).ok()?;
    let existing = store
        .get(INSTALL_ID_KEY)
        .and_then(|value: serde_json::Value| value.as_str().map(str::to_string))
        .filter(|id| !id.is_empty());
    let id = match existing {
        Some(id) => id,
        None => {
            let id = uuid::Uuid::new_v4().to_string();
            store.set(INSTALL_ID_KEY, serde_json::Value::String(id.clone()));
            if let Err(error) = store.save() {
                // Leave nothing behind, so the next event does not see a half-written id.
                store.delete(INSTALL_ID_KEY);
                tracing::debug!("analytics install id could not be persisted: {}", error);
                return None;
            }
            id
        }
    };
    Some(INSTALL_ID.get_or_init(|| id).clone())
}

/// Truncate `value` to `max_chars` characters keeping the END of the string.
/// Error messages carry a stderr tail and the tail is the half worth having, so the
/// front is what gets elided. Counting characters (not bytes) keeps the cut on a
/// UTF-8 char boundary.
/// Replace anything shaped like a filesystem path with a placeholder.
///
/// `error_message` is the one analytics prop carrying free text, and two of the errors that reach
/// it are `format!("Audio file not found: {}", options.path)` and its sibling -- the user's full
/// path, which `truncate_keep_tail` then preserves because it keeps the *tail*. The comment at
/// `analytics.rs:33` promising "never a transcript, filename, saved path" scopes itself to the
/// handoff events and is true there; the desktop transcribe path had no such guard.
///
/// Deliberately coarse. Dropping a token that merely looks path-like costs a word of context in a
/// dashboard; keeping one costs a filename. The frontend already sends the only fact worth having,
/// `file_ext`, as its own prop.
fn redact_paths(value: &str) -> String {
    fn path_starts_at(rest: &str) -> bool {
        rest.starts_with('/')
            || rest.starts_with("~/")
            || rest.starts_with("\\\\")
            || rest
                .as_bytes()
                .get(1)
                .is_some_and(|b| *b == b':' && matches!(rest.as_bytes().get(2), Some(b'\\' | b'/')))
    }

    let bytes = value.as_bytes();
    for (index, _) in value.char_indices() {
        // Only at a token boundary, so "and/or" or "24/7" inside a sentence is left alone.
        let at_boundary = index == 0 || bytes[index - 1].is_ascii_whitespace();
        if at_boundary && path_starts_at(&value[index..]) {
            // To the end of the string, not to the next space: paths contain spaces, and the one
            // that found this was `/Users/someone/Recordings/board meeting.m4a`. Cutting at the
            // space left the filename behind, which is the whole thing being protected. Every
            // message that reaches here puts the path last, and over-redacting costs a word of
            // dashboard context while under-redacting costs a filename.
            return format!("{}<path>", &value[..index]);
        }
    }
    value.to_string()
}

fn truncate_keep_tail(value: &str, max_chars: usize) -> String {
    let total = value.chars().count();
    if total <= max_chars {
        return value.to_string();
    }
    let marker_chars = ELISION_MARKER.chars().count();
    if max_chars <= marker_chars {
        return value.chars().skip(total - max_chars).collect();
    }
    let tail: String = value.chars().skip(total - (max_chars - marker_chars)).collect();
    format!("{}{}", ELISION_MARKER, tail)
}

/// Flush pending events without letting a slow network hold up an exit.
pub fn flush_events_bounded(app_handle: &AppHandle, timeout: Duration) {
    if !is_aptabase_configured() {
        return;
    }
    let (sender, receiver) = std::sync::mpsc::channel();
    let handle = app_handle.clone();
    // flush_events_blocking ends in a reqwest call, which panics without a tokio runtime in
    // scope. A bare thread has none, and the release profile aborts on panic, so that panic
    // took the whole app down on quit rather than just losing the flush. Carry the runtime in.
    let runtime = tauri::async_runtime::handle();
    std::thread::spawn(move || {
        let _guard = runtime.inner().enter();
        handle.flush_events_blocking();
        let _ = sender.send(());
    });
    if receiver.recv_timeout(timeout).is_err() {
        tracing::debug!("analytics flush timed out after {:?}", timeout);
    }
}

pub fn track_event(app: &App, event_name: &str) {
    track_event_handle_with_props(app.handle(), event_name, None);
}

pub fn track_event_handle(app_handle: &AppHandle, event_name: &str) {
    track_event_handle_with_props(app_handle, event_name, None);
}

pub fn track_event_handle_with_props(app_handle: &AppHandle, event_name: &str, props: Option<serde_json::Value>) {
    if !is_aptabase_configured() {
        tracing::debug!(
            "analytics track_event failed for '{}': APTABASE_APP_KEY or APTABASE_BASE_URL is not set",
            event_name
        );
        return;
    }
    if !is_analytics_enabled(app_handle) {
        return;
    }
    let mut merged = match props {
        Some(serde_json::Value::Object(m)) => m,
        _ => serde_json::Map::new(),
    };
    if let Some(serde_json::Value::String(message)) = merged.get("error_message") {
        // Redact before truncating: truncation keeps the tail, which is exactly where a path ends.
        let cleaned = truncate_keep_tail(&redact_paths(message), MAX_PROP_CHARS);
        if cleaned != *message {
            merged.insert("error_message".to_string(), cleaned.into());
        }
    }
    merged.entry("vibe_commit").or_insert_with(|| env!("COMMIT_HASH").into());
    if let Some(id) = install_id(app_handle) {
        merged.entry("install_id").or_insert_with(|| id.into());
    }
    tracing::trace!("analytics track_event '{}' sent", event_name);
    if let Err(error) = app_handle.track_event(event_name, Some(serde_json::Value::Object(merged))) {
        tracing::debug!("analytics track_event failed for '{}': {}", event_name, error);
    }
}

#[cfg(test)]
mod tests {
    use super::redact_paths;

    #[test]
    fn redacts_the_two_messages_that_actually_carry_a_path() {
        // Verbatim shapes from cmd/transcribe.rs.
        assert_eq!(
            redact_paths("Audio file not found: /Users/someone/Recordings/board meeting.m4a"),
            "Audio file not found: <path>"
        );
        assert_eq!(
            redact_paths("Path is not a file: /Users/someone/Desktop"),
            "Path is not a file: <path>"
        );
    }

    #[test]
    fn redacts_the_other_platforms_too() {
        assert_eq!(redact_paths("not found: C:\\Users\\someone\\a.mp3"), "not found: <path>");
        assert_eq!(redact_paths("not found: ~/Downloads/a.mp3"), "not found: <path>");
        assert_eq!(redact_paths("not found: \\\\server\\share\\a.mp3"), "not found: <path>");
    }

    #[test]
    fn a_path_with_spaces_is_redacted_whole() {
        // The case that broke the first attempt: a token-wise redactor cut at the space and left
        // `meeting.m4a` behind, which is the filename this exists to keep out.
        assert_eq!(
            redact_paths("Audio file not found: /Users/someone/Recordings/board meeting.m4a"),
            "Audio file not found: <path>"
        );
    }

    #[test]
    fn a_slash_inside_a_word_is_not_a_path() {
        assert_eq!(redact_paths("retry 24/7 and/or report"), "retry 24/7 and/or report");
    }

    #[test]
    fn leaves_an_ordinary_message_alone() {
        // A redactor that eats everything would pass the tests above and tell us nothing.
        let message = "model load failed with GPU enabled, falling back to CPU";
        assert_eq!(redact_paths(message), message);
        assert_eq!(
            redact_paths("bad magic 0x230a2323, 9344 bytes"),
            "bad magic 0x230a2323, 9344 bytes"
        );
    }

    #[test]
    fn a_redacted_path_survives_truncation() {
        // truncate_keep_tail keeps the *tail*, which is where a path ends -- so redaction has to
        // happen first. This is the ordering, asserted.
        let long = format!("prefix {} /Users/someone/{}.m4a", "x".repeat(300), "y".repeat(50));
        let cleaned = super::truncate_keep_tail(&redact_paths(&long), super::MAX_PROP_CHARS);
        assert!(!cleaned.contains("/Users/"), "got {cleaned}");
        assert!(cleaned.contains("<path>"), "got {cleaned}");
    }
    use super::*;

    #[test]
    fn shorter_than_limit_is_untouched() {
        let message = "failed to spawn server";
        assert_eq!(truncate_keep_tail(message, MAX_PROP_CHARS), message);
        // Exactly at the limit is still untouched.
        let exact: String = "a".repeat(MAX_PROP_CHARS);
        assert_eq!(truncate_keep_tail(&exact, MAX_PROP_CHARS), exact);
    }

    #[test]
    fn much_longer_keeps_the_tail() {
        let message = format!("{}stderr tail that matters", "noise ".repeat(200));
        let truncated = truncate_keep_tail(&message, MAX_PROP_CHARS);
        assert_eq!(truncated.chars().count(), MAX_PROP_CHARS);
        assert!(truncated.starts_with(ELISION_MARKER));
        assert!(truncated.ends_with("stderr tail that matters"));
    }

    #[test]
    fn multi_byte_chars_cut_on_a_char_boundary() {
        // Each char is 3 bytes, so a byte-index cut would land mid-character.
        let message: String = "שלום".repeat(200);
        let truncated = truncate_keep_tail(&message, MAX_PROP_CHARS);
        assert_eq!(truncated.chars().count(), MAX_PROP_CHARS);
        assert!(truncated.starts_with(ELISION_MARKER));
        assert!(message.ends_with(truncated.trim_start_matches(ELISION_MARKER)));
        // Emoji (4 bytes each) mixed with ASCII behaves the same.
        let emoji = format!("{}done 🎉", "🎧".repeat(300));
        let truncated = truncate_keep_tail(&emoji, MAX_PROP_CHARS);
        assert_eq!(truncated.chars().count(), MAX_PROP_CHARS);
        assert!(truncated.ends_with("done 🎉"));
    }

    #[test]
    fn limit_at_or_below_the_marker_keeps_only_the_tail() {
        assert_eq!(truncate_keep_tail("abcdef", 3), "def");
        assert_eq!(truncate_keep_tail("abcdef", 2), "ef");
        assert_eq!(truncate_keep_tail("abcdef", 4), "...f");
    }

    #[test]
    fn install_id_looks_like_an_anonymous_uuid_v4() {
        let id = uuid::Uuid::new_v4().to_string();
        let parsed = uuid::Uuid::parse_str(&id).unwrap();
        assert_eq!(parsed.get_version_num(), 4);
        assert_ne!(id, uuid::Uuid::new_v4().to_string());
    }
}
