use crate::error::LogError;
use crate::server::ServerEvent;
use crate::setup::ServerState;
use crate::transcript::{Segment, Transcript};
use eyre::Result;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use tauri::{Emitter, Listener, State};
use tokio::sync::Mutex;

use super::{ui::set_progress_bar, CommandError};

#[allow(dead_code)]
#[derive(Deserialize, Serialize, Clone)]
pub struct FfmpegOptions {
    pub normalize_loudness: bool,
    pub custom_command: Option<String>,
}

impl Default for FfmpegOptions {
    fn default() -> Self {
        Self {
            normalize_loudness: true,
            custom_command: None,
        }
    }
}

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct TranscribeOptions {
    pub path: String,
    pub lang: Option<String>,
    pub verbose: Option<bool>,
    pub n_threads: Option<i32>,
    pub init_prompt: Option<String>,
    pub temperature: Option<f32>,
    pub translate: Option<bool>,
    pub max_text_ctx: Option<i32>,
    pub word_timestamps: Option<bool>,
    pub max_sentence_len: Option<i32>,
    pub sampling_strategy: Option<String>,
    pub best_of: Option<i32>,
    pub beam_size: Option<i32>,
    pub diarize_model: Option<String>,
    pub stable_timestamps: Option<bool>,
    pub vad_model: Option<String>,
}

pub(crate) const SERVER_DIED: &str = "vibe-server process died during transcription";

/// Turn a transcription failure into something diagnosable: a send failure or a
/// mid-stream decode error is usually the sidecar dying, and only the child
/// itself knows the exit code, the signal, and what it printed on the way out.
async fn transcribe_error(server_state: &State<'_, Mutex<ServerState>>, error: eyre::Error) -> CommandError {
    if let Some(api_error) = error.downcast_ref::<crate::server::ServerApiError>() {
        return CommandError {
            code: api_error.code.clone(),
            message: api_error.message.clone(),
        };
    }
    match crate::server::death_report(server_state, SERVER_DIED).await {
        Some(message) => CommandError {
            code: "internal_error".to_string(),
            message,
        },
        None => {
            let mut command_error = CommandError::from(error);
            let stderr = crate::server::recent_stderr(server_state).await;
            if !stderr.is_empty() {
                command_error.message.push_str(&format!("\n\nvibe-server stderr: {stderr}"));
            }
            command_error
        }
    }
}

#[tauri::command]
pub async fn transcribe(
    app_handle: tauri::AppHandle,
    mut options: TranscribeOptions,
    server_state: State<'_, Mutex<ServerState>>,
) -> Result<Transcript, CommandError> {
    // Validate file exists before attempting transcription
    let audio_path = PathBuf::from(&options.path);
    if !audio_path.exists() {
        return Err(CommandError {
            code: "invalid_request".to_string(),
            message: format!("Audio file not found: {}", options.path),
        });
    }
    if !audio_path.is_file() {
        return Err(CommandError {
            code: "invalid_request".to_string(),
            message: format!("Path is not a file: {}", options.path),
        });
    }

    // Refuse audio there is nothing to hear in. Asking whisper anyway does not return an
    // empty transcript -- it returns the highest-prior text from its training data, repeated
    // for the length of the file, with nothing in the log to say the audio was the problem.
    // A probe that cannot run returns None and is treated as unknown, never as silent.
    // A pass that begins on non-speech never recovers: whisper locks onto training-data
    // boilerplate and emits it for the whole file. Start where the talking starts instead, and
    // shift every timestamp back by what was skipped so the transcript still lines up with the
    // original recording. `speech_start_secs` returns None unless the head is unambiguous.
    let skipped_secs = crate::ffmpeg::speech_start_secs(&audio_path).unwrap_or(0.0);
    let mut trimmed_path: Option<PathBuf> = None;
    if skipped_secs > 0.0 {
        let extension = audio_path.extension().and_then(|e| e.to_str()).unwrap_or("wav");
        let candidate = crate::ffmpeg::get_vibe_temp_folder().join(format!(
            "{}-from{}s.{extension}",
            crate::ffmpeg::random_string(10),
            skipped_secs as u64
        ));
        match crate::ffmpeg::trim_from(&audio_path, &candidate, skipped_secs) {
            Ok(()) => {
                tracing::info!("skipping {skipped_secs:.0}s of non-speech before transcribing");
                trimmed_path = Some(candidate);
            }
            // Trimming is an optimisation, not a requirement. If it fails, transcribe the
            // original and let the degenerate-output check catch a loop if one happens.
            Err(error) => tracing::warn!("could not trim the non-speech head: {error:#}"),
        }
    }
    let skipped_secs = if trimmed_path.is_some() { skipped_secs } else { 0.0 };

    if let Some(peak) = crate::ffmpeg::peak_dbfs(&audio_path) {
        if peak < crate::ffmpeg::SILENCE_PEAK_DBFS {
            tracing::warn!("refusing to transcribe {}: peak {peak:.1} dBFS", options.path);
            return Err(CommandError {
                code: "silent_audio".to_string(),
                message: format!(
                    "No audible speech in this file (peak {peak:.1} dBFS). Check the input device and recording level, then record again."
                ),
            });
        }
    }

    let (client, base_url) = {
        let state = server_state.lock().await;
        let process = state.process.as_ref().ok_or_else(|| CommandError {
            code: "no_model".to_string(),
            message: "Please load model first".to_string(),
        })?;
        (process.client(), process.base_url())
    }; // lock released here, before any I/O

    let abort_atomic = Arc::new(AtomicBool::new(false));
    let abort_atomic_c = abort_atomic.clone();

    let app_handle_c = app_handle.clone();
    app_handle.listen("abort_transcribe", move |_| {
        let _ = set_progress_bar(&app_handle_c, None);
        abort_atomic_c.store(true, Ordering::Relaxed);
    });

    let start = std::time::Instant::now();

    if let Some(path) = trimmed_path.as_ref() {
        options.path = path.to_string_lossy().into_owned();
    }

    let stream = match crate::server::ServerProcess::transcribe_stream(&client, &base_url, &options).await {
        Ok(stream) => stream,
        Err(e) => return Err(transcribe_error(&server_state, e).await),
    };

    tokio::pin!(stream);

    let mut segments = Vec::new();
    let mut completed = false;

    while let Some(event_result) = stream.next().await {
        if abort_atomic.load(Ordering::Relaxed) {
            tracing::debug!("transcription aborted by user");
            break;
        }

        match event_result {
            Ok(event) => match event {
                ServerEvent::Progress { progress } => {
                    let _ = set_progress_bar(&app_handle, Some(progress.into()));
                }
                ServerEvent::Segment {
                    start,
                    end,
                    text,
                    speaker,
                } => {
                    let segment = Segment {
                        start: centiseconds(start, skipped_secs),
                        stop: centiseconds(end, skipped_secs),
                        text,
                        speaker,
                    };
                    app_handle.emit_to("main", "new_segment", segment.clone()).log_error();
                    segments.push(segment);
                }
                ServerEvent::Result { .. } => {
                    tracing::debug!("transcription complete");
                    completed = true;
                }
                ServerEvent::Error { code, message } => {
                    tracing::error!("vibe-server transcription error: {}", message);
                    let _ = set_progress_bar(&app_handle, None);
                    return Err(CommandError {
                        code: code.unwrap_or_else(|| "internal_error".to_string()),
                        message,
                    });
                }
            },
            Err(e) => {
                tracing::error!("stream error: {:?}", e);
                let _ = set_progress_bar(&app_handle, None);
                return Err(transcribe_error(&server_state, e).await);
            }
        }
    }

    let _ = set_progress_bar(&app_handle, None);

    if !abort_atomic.load(Ordering::Relaxed) && !completed {
        // A stream that just stops is almost always the sidecar dying under it;
        // say how it died rather than reporting a truncated stream.
        let message = crate::server::death_report(&server_state, SERVER_DIED)
            .await
            .unwrap_or_else(|| "vibe-server transcription stream ended before completion".to_string());
        return Err(CommandError {
            code: "internal_error".to_string(),
            message,
        });
    }

    // Report a loop; never withhold the transcript over one. The measure is a heuristic and
    // it has already been wrong in the expensive direction: a genuine interview came in at 45%
    // consecutive repeats -- speakers hesitate and repeat themselves ("有沒有 有沒有"), and
    // short segments of one or two words collide constantly -- and was thrown away, costing the
    // user a two-hour transcription that was fine. Boilerplate is visible on screen the moment
    // it appears and a reader loses nothing by receiving it; a discarded transcript is gone.
    if let Some((text, share)) = crate::transcript::degenerate_loop(&segments) {
        let preview: String = text.chars().take(40).collect();
        tracing::warn!(
            "transcript looks degenerate: {:.0}% consecutive repeats of {preview:?} -- keeping it anyway",
            share * 100.0
        );
    }

    let elapsed = start.elapsed();
    let transcript = Transcript {
        processing_time_sec: elapsed.as_secs(),
        segments,
    };

    Ok(transcript)
}

/// A sidecar timestamp, in centiseconds, shifted back onto the original recording.
///
/// The sidecar sees only the trimmed file and counts from zero within it, so every timestamp
/// it reports is short by whatever was skipped. Getting this wrong does not fail loudly -- it
/// silently slides an entire transcript out of sync with the audio it belongs to.
fn centiseconds(secs: f64, offset_secs: f64) -> i64 {
    ((secs + offset_secs) * 100.0) as i64
}

#[cfg(test)]
mod timestamp_tests {
    use super::centiseconds;

    #[test]
    fn an_untrimmed_file_is_left_exactly_where_it_was() {
        assert_eq!(centiseconds(0.0, 0.0), 0);
        assert_eq!(centiseconds(4.68, 0.0), 468);
    }

    #[test]
    fn a_trimmed_file_is_shifted_by_what_was_skipped() {
        // The interview this was written for skips 700s; its first subtitle must land at
        // 11:40 in the original recording, not at 0:00.
        assert_eq!(centiseconds(0.0, 700.0), 70_000);
        assert_eq!(centiseconds(4.68, 700.0), 70_468);
    }

    #[test]
    fn the_offset_is_added_not_subtracted() {
        // Subtracting compiles just as well and would put every segment before the recording
        // started, where a player shows nothing at all.
        assert!(centiseconds(10.0, 700.0) > centiseconds(10.0, 0.0));
    }

    #[test]
    fn the_far_end_of_a_two_hour_file_still_fits() {
        // 1h51m into a file trimmed at 700s -- the last subtitle of the recording that
        // prompted this. i64 centiseconds has room to spare; f64 keeps the precision.
        assert_eq!(centiseconds(6661.0, 700.0), 736_100);
    }
}
