use crate::config::STORE_FILENAME;
use crate::ffmpeg;
use eyre::{Context, Result};

/// Return true if there's internet connection
/// timeout in ms
#[tauri::command]
pub async fn is_online(timeout: Option<u64>) -> Result<bool> {
    let timeout = std::time::Duration::from_millis(timeout.unwrap_or(2000));
    let targets = ["1.1.1.1:80", "1.1.1.1:53", "8.8.8.8:53", "8.8.8.8:80"];

    let tasks = targets.iter().map(|addr| async move {
        tokio::time::timeout(timeout, tokio::net::TcpStream::connect(addr))
            .await
            .map(|res| res.is_ok())
            .unwrap_or(false)
    });

    Ok(futures::future::join_all(tasks).await.into_iter().any(|res| res))
}
use serde_json::Value;
use std::path::PathBuf;
use tauri::Manager;
use tauri_plugin_store::StoreExt;

#[tauri::command]
pub fn get_commit_hash() -> String {
    env!("COMMIT_HASH").to_string()
}

#[tauri::command]
pub fn is_avx2_enabled() -> bool {
    // A real check on every x86 machine, Intel Macs included: a hardcoded `true` there once
    // sent a bug report down the wrong path (#1499).
    #[cfg(any(target_arch = "x86", target_arch = "x86_64"))]
    {
        is_x86_feature_detected!("avx2")
    }
    #[cfg(not(any(target_arch = "x86", target_arch = "x86_64")))]
    {
        true
    }
}

#[tauri::command]
pub fn track_analytics_event(app_handle: tauri::AppHandle, name: String, props: Option<Value>) -> Result<()> {
    crate::analytics::track_event_handle_with_props(&app_handle, &name, props);
    Ok(())
}

#[tauri::command]
pub fn get_logs_folder(app_handle: tauri::AppHandle) -> Result<PathBuf> {
    Ok(app_handle.path().app_config_dir()?)
}

#[tauri::command]
pub async fn show_log_path(app_handle: tauri::AppHandle) -> Result<()> {
    let log_path = crate::logging::get_log_path(&app_handle)?;
    if log_path.exists() {
        showfile::show_path_in_file_manager(log_path);
    } else if let Some(parent) = log_path.parent() {
        showfile::show_path_in_file_manager(parent);
    }
    Ok(())
}

#[tauri::command]
pub async fn show_temp_path() -> Result<()> {
    let temp_path = ffmpeg::get_vibe_temp_folder();
    showfile::show_path_in_file_manager(temp_path);
    Ok(())
}

#[tauri::command]
pub fn get_models_folder(app_handle: tauri::AppHandle) -> Result<PathBuf> {
    let store = app_handle.store(STORE_FILENAME)?;

    let models_folder = store.get("models_folder").and_then(|p| p.as_str().map(PathBuf::from));
    if let Some(models_folder) = models_folder {
        tracing::debug!("models folder: {:?}", models_folder);
        return Ok(models_folder);
    }
    let path = app_handle.path().app_local_data_dir().context("Can't get data directory")?;
    Ok(path)
}

/// Record a frontend failure in the log file the user can actually send us.
///
/// The webview's console is a dead end: nothing bridges it to `tracing`, and `get_logs` reads
/// only the tracing file. So every `console.warn` in transcripts-store.ts -- including the one
/// that fires when saving a finished transcript fails and deletes its own half-built folder --
/// is invisible in a bug report. A transcription that succeeded and then vanished left no
/// evidence anywhere on the machine.
///
/// Message only, no paths interpreted, no side effects beyond the log line.
#[tauri::command]
pub fn log_frontend_error(scope: String, message: String) {
    // Bound both: a stack trace or a serialized record could otherwise be megabytes.
    let scope = bounded(&scope, 64);
    let message = bounded(&message, 2000);
    tracing::error!(target: "vibe::frontend", "{scope}: {message}");
}

/// Truncation for `log_frontend_error`, split out so the bounds can be tested.
fn bounded(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

#[cfg(test)]
mod frontend_log_tests {
    use super::bounded;

    #[test]
    fn a_short_message_is_untouched() {
        assert_eq!(bounded("failed to save transcript", 2000), "failed to save transcript");
    }

    #[test]
    fn a_long_message_is_cut_to_the_limit() {
        let long = "x".repeat(5000);
        assert_eq!(bounded(&long, 2000).chars().count(), 2000);
    }

    #[test]
    fn truncation_counts_characters_not_bytes() {
        // A path or an error can be entirely CJK. Cutting on bytes would split a character and
        // produce mojibake in the log, or panic on a slice boundary.
        let cjk = "檔案寫入失敗".repeat(500);
        let cut = bounded(&cjk, 10);
        assert_eq!(cut.chars().count(), 10);
        assert_eq!(cut, "檔案寫入失敗檔案寫入");
    }
}

#[tauri::command]
pub fn get_logs(app_handle: tauri::AppHandle) -> Result<String> {
    let path = crate::logging::get_log_path(&app_handle)?;
    let content = std::fs::read_to_string(path)?;
    Ok(content)
}

#[tauri::command]
pub fn is_crashed_recently() -> bool {
    tracing::debug!("checking path {}", ffmpeg::get_vibe_temp_folder().join("crash.txt").display());
    ffmpeg::get_vibe_temp_folder().join("crash.txt").exists()
}

#[tauri::command]
pub fn rename_crash_file() -> Result<()> {
    std::fs::rename(
        ffmpeg::get_vibe_temp_folder().join("crash.txt"),
        ffmpeg::get_vibe_temp_folder().join("crash.1.txt"),
    )
    .context("Can't delete file")
}

#[tauri::command]
pub fn type_text(text: String) -> Result<()> {
    use enigo::{Enigo, Keyboard, Settings};
    let mut enigo = Enigo::new(&Settings::default()).map_err(|e| eyre::eyre!("Failed to create enigo: {}", e))?;
    // Small delay to let the user's key release propagate
    std::thread::sleep(std::time::Duration::from_millis(100));
    enigo.text(&text).map_err(|e| eyre::eyre!("Failed to type text: {}", e))?;
    Ok(())
}

#[tauri::command]
pub fn get_cargo_features() -> Vec<String> {
    Vec::new()
}
