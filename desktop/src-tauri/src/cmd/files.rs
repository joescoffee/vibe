use eyre::{ContextCompat, Result};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager};

#[tauri::command]
pub async fn glob_files(folder: String, patterns: Vec<String>, recursive: bool) -> Vec<String> {
    let mut files = Vec::new();

    // The folder name is data, not pattern. `Recordings [2025]` is a character class to glob, so
    // the unescaped form matched nothing and returned an empty vector -- and an empty vector is
    // what "this folder has no media" looks like, so the drop produced no file, no error and no
    // log line. Brackets are ordinary in dated folder names; so are `?` and `*` on filesystems
    // that allow them.
    let escaped_folder = glob::Pattern::escape(&folder);
    let search_pattern = if recursive {
        format!("{}/**/*", escaped_folder)
    } else {
        format!("{}/*", escaped_folder)
    };

    match glob::glob(&search_pattern) {
        Ok(paths) => {
            for entry in paths.filter_map(Result::ok) {
                if entry.is_file() && has_matching_extension(&entry, &patterns) {
                    if let Ok(path_str) = entry.into_os_string().into_string() {
                        files.push(path_str);
                    }
                }
            }
        }
        Err(e) => {
            eprintln!("Failed to read pattern {}: {}", search_pattern, e);
        }
    }

    files
}

/// Recorders and phones write `.MP3` and `.MOV`, so the extension is matched without regard to
/// case — otherwise those files vanish from every folder scan. Patterns may carry a leading dot.
fn has_matching_extension(path: &Path, patterns: &[String]) -> bool {
    let Some(extension) = path.extension().and_then(|extension| extension.to_str()) else {
        return false;
    };

    patterns
        .iter()
        .any(|pattern| pattern.trim_start_matches('.').eq_ignore_ascii_case(extension))
}

#[tauri::command]
pub fn get_path_dst(src: String, suffix: String) -> Result<String> {
    let src = PathBuf::from(src);
    let src_filename = src.file_name().context("filename")?.to_str().context("stostr")?;
    let src_name = src
        .file_stem()
        .map(|name| name.to_str().context("tosstr"))
        .unwrap_or(Ok(src_filename))?;

    let parent = src.parent().context("parent")?;
    let mut dst_path = parent.join(format!("{}{}", src_name, suffix));

    let mut counter = 1;
    while dst_path.exists() {
        dst_path = parent.join(format!("{} ({}){}", src_name, counter, suffix));
        counter += 1;
    }
    Ok(dst_path.to_str().context("tostr")?.into())
}

pub(crate) fn sanitize_filename_stem(input: &str) -> String {
    input
        .trim()
        .chars()
        .map(|c| match c {
            '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            c if c.is_control() => '_',
            c => c,
        })
        .collect::<String>()
        .trim_matches([' ', '.'])
        .to_string()
}

pub(crate) fn available_path(parent: &Path, stem: &str, extension: &str) -> PathBuf {
    let extension = extension.trim_start_matches('.');
    let mut path = parent.join(format!("{stem}.{extension}"));
    let mut counter = 1;

    while path.exists() {
        path = parent.join(format!("{stem} ({counter}).{extension}"));
        counter += 1;
    }

    path
}

#[tauri::command]
pub fn get_save_path(src_path: PathBuf, target_ext: &str) -> Result<Value> {
    let stem = src_path.file_stem().and_then(|s| s.to_str()).unwrap_or_default();
    let mut new_path = src_path.clone();
    // Not `set_file_name(stem)` then `set_extension(ext)`. `set_extension` replaces everything
    // after the *last* dot of the name it is given, and `file_stem` keeps the earlier ones: the
    // pair turned `2026.01.15 standup.mp4` into `2026.01.srt`. Dates in filenames are ordinary.
    new_path.set_file_name(format!("{stem}.{target_ext}"));
    let new_filename = new_path.file_name().map(|s| s.to_str()).unwrap_or(Some("Untitled"));
    let new_path = new_path.to_str().context("to_str")?;
    let named_path = json!({"name": new_filename, "path": new_path});
    Ok(named_path)
}

#[tauri::command]
pub fn get_argv() -> Vec<String> {
    std::env::args().collect()
}

#[tauri::command]
pub fn get_default_projects_path(app_handle: AppHandle) -> Result<String> {
    let path = app_handle
        .path()
        .document_dir()
        .map_err(|e| eyre::eyre!("{e:?}"))?
        .join(crate::config::DOCUMENTS_SUBFOLDER);
    Ok(path.to_string_lossy().to_string())
}

#[tauri::command]
pub async fn open_path(path: PathBuf) -> Result<()> {
    showfile::show_path_in_file_manager(path);
    Ok(())
}

#[tauri::command]
pub fn get_ffmpeg_path() -> String {
    crate::ffmpeg::find_ffmpeg_path()
        .map(|p| p.to_str().unwrap().to_string())
        .unwrap_or_default()
}

/// Media picker that accepts files *and* folders in one dialog.
///
/// Only macOS' open panel can offer both at once (`NSOpenPanel` takes two independent flags); the
/// dialog plugin — and the cross-platform picker it wraps — is one or the other. Elsewhere this
/// returns `None` so the caller falls back to the plugin's file dialog.
#[tauri::command]
pub async fn pick_media_paths(app_handle: AppHandle, extensions: Vec<String>) -> Result<Option<Vec<String>>> {
    #[cfg(target_os = "macos")]
    {
        let (sender, receiver) = tokio::sync::oneshot::channel();
        app_handle.run_on_main_thread(move || {
            let _ = sender.send(macos_open_panel(&extensions));
        })?;
        Ok(receiver.await?)
    }
    #[cfg(not(target_os = "macos"))]
    {
        // Nothing to open here; the caller falls back to the plugin dialog.
        let _ = (app_handle, extensions);
        Ok(None)
    }
}

/// `None` when the user cancelled. Must run on the main thread — AppKit panels are modal there.
#[cfg(target_os = "macos")]
fn macos_open_panel(extensions: &[String]) -> Option<Vec<String>> {
    use objc2::rc::Retained;
    use objc2_app_kit::NSOpenPanel;
    use objc2_foundation::{MainThreadMarker, NSArray, NSString};

    const NS_MODAL_RESPONSE_OK: isize = 1;

    let mtm = MainThreadMarker::new()?;
    let panel = NSOpenPanel::openPanel(mtm);
    panel.setCanChooseFiles(true);
    panel.setCanChooseDirectories(true);
    panel.setAllowsMultipleSelection(true);

    // The filter applies to files only; folders stay selectable whatever it says.
    if !extensions.is_empty() {
        let types: Vec<Retained<NSString>> = extensions.iter().map(|extension| NSString::from_str(extension)).collect();
        let refs: Vec<&NSString> = types.iter().map(|value| value.as_ref()).collect();
        // Deprecated in favour of UTTypes, but still honoured and it keeps the extension list simple.
        #[allow(deprecated)]
        panel.setAllowedFileTypes(Some(&NSArray::from_slice(&refs)));
    }

    if panel.runModal() != NS_MODAL_RESPONSE_OK {
        return None;
    }

    let mut paths = Vec::new();
    for url in panel.URLs().iter() {
        if let Some(path) = url.path() {
            paths.push(path.to_string());
        }
    }
    Some(paths)
}

#[cfg(test)]
mod tests {
    use super::has_matching_extension;
    use std::path::Path;

    #[test]
    fn matches_the_extension_whatever_its_case() {
        let patterns = vec!["mp3".to_string(), "mov".to_string()];
        assert!(has_matching_extension(Path::new("/tmp/interview.mp3"), &patterns));
        assert!(has_matching_extension(Path::new("/tmp/interview.MP3"), &patterns));
        assert!(has_matching_extension(Path::new("/tmp/call 17.8.2026.MOV"), &patterns));
    }

    #[test]
    fn ignores_names_that_only_end_with_the_pattern() {
        let patterns = vec!["mp3".to_string()];
        assert!(!has_matching_extension(Path::new("/tmp/notesmp3"), &patterns));
        assert!(!has_matching_extension(Path::new("/tmp/notes.pdf"), &patterns));
    }
}

#[cfg(test)]
mod save_path_tests {
    use super::get_save_path;
    use std::path::PathBuf;

    fn save_path(name: &str, ext: &str) -> String {
        let value = get_save_path(PathBuf::from(format!("/tmp/{name}")), ext).expect("get_save_path");
        value["name"].as_str().expect("name").to_string()
    }

    #[test]
    fn keeps_every_dot_that_is_part_of_the_name() {
        // The measured case: `set_file_name(stem)` + `set_extension` truncated at the first dot.
        assert_eq!(save_path("2026.01.15 standup.mp4", "srt"), "2026.01.15 standup.srt");
    }

    #[test]
    fn still_replaces_a_single_extension() {
        assert_eq!(save_path("meeting.mp4", "srt"), "meeting.srt");
    }

    #[test]
    fn a_name_with_no_extension_gains_one() {
        assert_eq!(save_path("meeting", "srt"), "meeting.srt");
    }
}

#[cfg(test)]
mod glob_files_tests {
    use super::glob_files;

    /// A folder whose name contains glob metacharacters must still be scanned.
    #[tokio::test]
    async fn finds_media_in_a_folder_named_like_a_character_class() {
        let root = std::env::temp_dir().join(format!("vibe-glob-test-{}", std::process::id()));
        let folder = root.join("Recordings [2025]");
        std::fs::create_dir_all(&folder).expect("create");
        std::fs::write(folder.join("a.mp3"), b"x").expect("write");
        std::fs::write(folder.join("b.txt"), b"x").expect("write");

        let found = glob_files(folder.to_string_lossy().into_owned(), vec!["mp3".into()], false).await;

        // The negative control is the point: before the escape this returned 0, which is
        // indistinguishable from an empty folder everywhere downstream.
        assert_eq!(found.len(), 1, "expected the mp3, got {found:?}");
        assert!(found[0].ends_with("a.mp3"));

        std::fs::remove_dir_all(&root).ok();
    }
}
