use chrono::Local;
use eyre::{bail, ContextCompat, Result};
use rand::distr::Alphanumeric;
use rand::Rng;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use which::which;

pub fn get_local_time() -> String {
    let now = Local::now();
    now.format("%Y-%m-%d %H-%M-%S").to_string()
}

pub fn random_string(length: usize) -> String {
    rand::rng().sample_iter(&Alphanumeric).take(length).map(char::from).collect()
}

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(not(windows))]
const EXECUTABLE_NAME: &str = "ffmpeg";

#[cfg(windows)]
const EXECUTABLE_NAME: &str = "ffmpeg.exe";

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

/// EBU R128 loudness normalization, matching the filter the settings UI advertises
/// (`sections/audio-processing.tsx`). Constant args only -- never user input, so it is safe
/// to splice into the option position that `normalize`'s doc comment warns about.
///
/// Known floor: loudnorm gates at -70 LUFS. Below that it measures the input as `-inf`
/// and applies no gain at all, so this rescues a quiet capture but not a dead one.
/// Verified on a -60 dB speech sample (lifted to -16.0 LUFS / -1.5 dBTP, on target) and
/// on a -80 dB one (unchanged, `Input Integrated: -inf LUFS`).
pub const LOUDNORM_ARGS: [&str; 2] = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"];

pub fn get_vibe_temp_folder() -> PathBuf {
    use chrono::Local;
    let current_datetime = Local::now();
    let formatted_datetime = current_datetime.format("%Y-%m-%d").to_string();
    let dir = std::env::temp_dir().join(format!("vibe_temp_{}", formatted_datetime));
    if std::fs::create_dir_all(&dir).is_ok() {
        return dir;
    }
    std::env::temp_dir()
}

pub fn find_ffmpeg_path() -> Option<PathBuf> {
    if let Ok(path) = which(EXECUTABLE_NAME) {
        return Some(path);
    }

    let cwd = std::env::current_dir().ok()?;
    let ffmpeg_in_cwd = cwd.join(EXECUTABLE_NAME);
    if ffmpeg_in_cwd.is_file() && ffmpeg_in_cwd.exists() {
        return Some(ffmpeg_in_cwd);
    }

    if let Ok(exe_path) = std::env::current_exe() {
        let exe_folder = exe_path.parent()?;
        let ffmpeg_in_exe_folder = exe_folder.join(EXECUTABLE_NAME);
        if ffmpeg_in_exe_folder.exists() {
            return Some(ffmpeg_in_exe_folder);
        }
        #[cfg(target_os = "macos")]
        {
            let resources_folder = exe_folder.join("../Resources");
            let ffmpeg_in_resources = resources_folder.join(EXECUTABLE_NAME);
            if ffmpeg_in_resources.exists() {
                return Some(ffmpeg_in_resources);
            }
        }
    }

    None
}

/// Re-encode `input` to 16 kHz mono PCM at `output`.
///
/// `additional_ffmpeg_args` lands in an **option position**, between the codec flags and
/// the output path. ffmpeg has no `--` end-of-options separator, so there is no way to
/// mark those tokens as data: whatever goes in is parsed as flags, and an injected `-y`,
/// `-f` or a second output path takes effect. Every caller passes `None` today. Before
/// wiring this to `FfmpegOptions::custom_command` — which the settings UI already
/// collects into `transcription.ffmpegOptions` — the value needs an explicit allowlist,
/// not a split on whitespace.
pub fn normalize(input: PathBuf, output: PathBuf, additional_ffmpeg_args: Option<Vec<String>>) -> Result<()> {
    let ffmpeg_path = find_ffmpeg_path().context("ffmpeg not found")?;
    tracing::debug!("ffmpeg path is {}", ffmpeg_path.display());

    let mut cmd = Command::new(ffmpeg_path);
    let cmd = cmd.stderr(Stdio::piped()).args([
        "-i",
        input.to_str().context("tostr")?,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
    ]);

    cmd.args(additional_ffmpeg_args.unwrap_or_default());

    cmd.args([output.to_str().context("tostr")?, "-hide_banner", "-y", "-loglevel", "error"]);

    tracing::debug!("cmd: {:?}", cmd);

    let cmd = cmd.stdin(Stdio::null());

    #[cfg(windows)]
    let cmd = cmd.creation_flags(CREATE_NO_WINDOW);

    let mut pid = cmd.spawn()?;
    if !pid.wait()?.success() {
        let mut stderr_output = String::new();
        if let Some(ref mut stderr) = pid.stderr {
            stderr.take(1000).read_to_string(&mut stderr_output)?;
        }
        bail!("unable to convert file: {:?} args: {:?}", stderr_output, cmd.get_args());
    }

    if !output.exists() {
        bail!("seems like ffmpeg failed for some reason. output not exists")
    }
    Ok(())
}

/// Peak level of `input` in dBFS, via ffmpeg's `volumedetect`.
///
/// `None` when ffmpeg is missing or prints nothing parseable -- the caller must treat that
/// as "unknown", never as "silent", or a probe failure would start rejecting good audio.
/// Digital silence reports `-91.0` (the 16-bit floor) or no line at all.
pub fn peak_dbfs(input: &Path) -> Option<f32> {
    let ffmpeg_path = find_ffmpeg_path()?;
    let mut cmd = Command::new(ffmpeg_path);
    cmd.args([
        "-hide_banner",
        "-i",
        input.to_str()?,
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ])
    .stdin(Stdio::null())
    .stdout(Stdio::null())
    .stderr(Stdio::piped());

    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let output = cmd.output().ok()?;
    parse_max_volume(&String::from_utf8_lossy(&output.stderr))
}

/// Pulls `max_volume: -41.6 dB` out of volumedetect's report.
fn parse_max_volume(stderr: &str) -> Option<f32> {
    stderr.lines().find_map(|line| {
        let rest = line.split("max_volume:").nth(1)?;
        rest.trim().strip_suffix(" dB")?.trim().parse::<f32>().ok()
    })
}

/// Below this peak there is nothing a speech model can work with, and asking it anyway is
/// worse than refusing: whisper answers out-of-distribution input with the highest-prior
/// text in its training data rather than with nothing.
///
/// Set at -50 dBFS, which is far under even a distant or badly-gained voice (the quiet
/// captures that prompted this measured -41 dBFS peak) and far over digital silence.
pub const SILENCE_PEAK_DBFS: f32 = -50.0;

/// Seconds of non-speech at the head of `input`, or `None` when there is none worth skipping.
///
/// A decoding pass that begins on non-speech does not merely produce nothing for that stretch --
/// whisper latches onto the highest-prior text in its training data and keeps emitting it for the
/// rest of the file, however long. Measured on a 2h03m interview whose conversation starts at
/// 11:40: decoding from 0:00 produced 762 bytes of "字幕志愿者 李宗盛" and nothing else across
/// fifteen minutes, including a stretch at 13:40 that transcribes perfectly on its own. Decoding
/// the same file from 11:40 produced 4863 clean subtitle entries over 1h51m.
///
/// So the head has to be found and skipped. The measure is a 10-second RMS envelope: speech sits
/// near the loud end of the file's own distribution, and a quiet lead-in sits far below it.
///
/// Deliberately conservative -- skipping real speech is far worse than transcribing a quiet
/// opening. Three conditions must all hold:
///   * the run of loud windows lasts a full minute, so a door slam or a cough does not count
///   * the head is at least `MIN_SKIP_SECS`, below which the loop does not take hold anyway
///   * the head is `MIN_HEAD_GAP_DB` quieter than the threshold, so a uniformly quiet recording
///     (where the "lead-in" is simply the speech) is left alone
pub fn speech_start_secs(input: &Path) -> Option<f64> {
    let levels = rms_envelope(input)?;
    speech_start_window(&levels).map(|w| (w as f64) * ENVELOPE_WINDOW_SECS)
}

/// Length of one envelope window. 160000 samples at 16 kHz.
const ENVELOPE_WINDOW_SECS: f64 = 10.0;
/// Windows that must stay loud before the run counts as speech.
const SUSTAINED_WINDOWS: usize = 6;
/// Shorter heads than this are not worth trimming.
const MIN_SKIP_SECS: f64 = 60.0;
/// How far below the threshold the head must sit before it is called non-speech.
const MIN_HEAD_GAP_DB: f32 = 5.0;
/// How far under the file's own loud end a window may sit and still count as speech.
const SPEECH_MARGIN_DB: f32 = 10.0;

/// Per-window RMS in dBFS, one entry per `ENVELOPE_WINDOW_SECS`.
fn rms_envelope(input: &Path) -> Option<Vec<f32>> {
    let ffmpeg_path = find_ffmpeg_path()?;
    let mut cmd = Command::new(ffmpeg_path);
    cmd.args([
        "-hide_banner",
        "-i",
        input.to_str()?,
        "-af",
        "aresample=16000,asetnsamples=n=160000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f",
        "null",
        "-",
    ])
    .stdin(Stdio::null())
    .stdout(Stdio::null())
    .stderr(Stdio::piped());

    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let output = cmd.output().ok()?;
    Some(parse_rms_levels(&String::from_utf8_lossy(&output.stderr)))
}

/// Pulls `lavfi.astats.Overall.RMS_level=-28.8` values out of ametadata's print output.
/// `-inf` windows are digital silence and are kept as a very low number so they read as quiet.
fn parse_rms_levels(stderr: &str) -> Vec<f32> {
    stderr
        .lines()
        .filter_map(|line| line.split("RMS_level=").nth(1))
        .map(|value| value.trim().parse::<f32>().unwrap_or(f32::NEG_INFINITY))
        .map(|value| if value.is_finite() { value } else { -120.0 })
        .collect()
}

/// Index of the first window where speech begins, or `None` to transcribe from the top.
fn speech_start_window(levels: &[f32]) -> Option<usize> {
    let min_windows = (MIN_SKIP_SECS / ENVELOPE_WINDOW_SECS) as usize;
    if levels.len() < min_windows + SUSTAINED_WINDOWS {
        return None;
    }
    let mut sorted: Vec<f32> = levels.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let loud = sorted[sorted.len() * 3 / 4];
    let threshold = loud - SPEECH_MARGIN_DB;

    let start = levels
        .windows(SUSTAINED_WINDOWS)
        .position(|run| run.iter().all(|level| *level >= threshold))?;

    if (start as f64) * ENVELOPE_WINDOW_SECS < MIN_SKIP_SECS {
        return None;
    }
    // The head must be clearly quieter than the speech, or this is not a lead-in at all.
    let head_median = median(&levels[..start]);
    (head_median <= threshold - MIN_HEAD_GAP_DB).then_some(start)
}

fn median(levels: &[f32]) -> f32 {
    let mut sorted: Vec<f32> = levels.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    sorted.get(sorted.len() / 2).copied().unwrap_or(f32::NEG_INFINITY)
}

/// Copy `input` from `start_secs` onward into `output`, without re-encoding.
pub fn trim_from(input: &Path, output: &Path, start_secs: f64) -> Result<()> {
    let ffmpeg_path = find_ffmpeg_path().context("ffmpeg not found")?;
    let mut cmd = Command::new(ffmpeg_path);
    cmd.args([
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        &format!("{start_secs}"),
        "-i",
        input.to_str().context("tostr")?,
        "-c",
        "copy",
        "-y",
        output.to_str().context("tostr")?,
    ])
    .stdin(Stdio::null());

    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    if !cmd.status()?.success() || !output.exists() {
        bail!("failed to trim {start_secs}s from {}", input.display());
    }
    Ok(())
}

pub fn merge_wav_files(a: PathBuf, b: PathBuf, dst: PathBuf) -> Result<()> {
    let ffmpeg_path = find_ffmpeg_path().context("ffmpeg not found")?;
    let output = dst.to_str().context("tostr")?;

    let mut cmd = Command::new(ffmpeg_path);
    cmd.args([
        "-i",
        a.to_str().context("tostr")?,
        "-i",
        b.to_str().context("tostr")?,
        "-filter_complex",
        // amix averages by default, which costs 6 dB on a two-input mix, and `-ac 2` used to
        // add another 3 dB of mono->stereo rematrix on top -- 9 dB thrown away on every
        // recording that had system audio selected. Sum instead, and let normalize() do the
        // single downmix to mono rather than producing stereo here just to collapse it later.
        // Summing can clip when the mic and the meeting are both loud, so cap the peaks.
        "amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.97",
        output,
        "-hide_banner",
        "-y",
        "-loglevel",
        "error",
    ])
    .stdin(Stdio::null());

    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let mut pid = cmd.spawn()?;
    if !pid.wait()?.success() {
        bail!("unable to merge files");
    }
    Ok(())
}

#[cfg(test)]
mod peak_tests {
    use super::*;

    /// Verbatim from `ffmpeg -af volumedetect` on this machine, so the parser is tested
    /// against what ffmpeg actually prints rather than against what it was assumed to.
    /// The two lines differ by 18.0 dB, which is what makes the next test able to fail.
    const REAL_OUTPUT: &str = "[Parsed_volumedetect_0 @ 0x6000036e40c0] mean_volume: -60.1 dB\n[Parsed_volumedetect_0 @ 0x6000036e40c0] max_volume: -42.1 dB";

    #[test]
    fn the_peak_is_read_from_real_ffmpeg_output() {
        assert_eq!(parse_max_volume(REAL_OUTPUT), Some(-42.1));
    }

    #[test]
    fn mean_volume_is_not_mistaken_for_the_peak() {
        // mean_volume is -60.1 and comes first in ffmpeg's report; taking it would be wrong
        // by 18.0 dB, which is the difference between refusing this file and accepting it.
        assert_eq!(parse_max_volume(REAL_OUTPUT), Some(-42.1));
        assert_ne!(parse_max_volume(REAL_OUTPUT), Some(-60.1));
    }

    #[test]
    fn output_without_the_line_is_unknown_not_silent() {
        assert_eq!(parse_max_volume("ffmpeg version 8.0\nnothing useful here"), None);
        assert_eq!(parse_max_volume(""), None);
    }

    #[test]
    fn a_loud_file_is_above_the_gate() {
        let loud = "[Parsed_volumedetect_0 @ 0x0] max_volume: 0.0 dB";
        assert!(parse_max_volume(loud).unwrap() > SILENCE_PEAK_DBFS);
    }

    #[test]
    fn quiet_speech_stays_above_the_gate() {
        // The captures that prompted the gate measured -41.6 dBFS and must not be refused.
        let quiet = "[Parsed_volumedetect_0 @ 0x0] max_volume: -41.6 dB";
        assert!(parse_max_volume(quiet).unwrap() > SILENCE_PEAK_DBFS);
    }
}

#[cfg(test)]
mod speech_start_tests {
    use super::*;

    /// The first 130 windows (21 minutes) of a real 2h03m interview, measured with the same
    /// filter chain `rms_envelope` uses. The conversation starts at 11:40; before that the
    /// room sits near -43 dB with a few short spikes that must not be mistaken for speech.
    const REAL_ENVELOPE: [f32; 130] = [
        -37.7, -43.4, -43.2, -42.9, -43.7, -44.0, -42.9, -38.8, -43.5, -43.5, -43.6, -40.4, -44.0, -43.8, -43.9, -40.4, -40.8,
        -44.0, -44.2, -44.0, -44.2, -44.4, -44.1, -44.0, -43.7, -43.7, -43.9, -32.0, -42.0, -42.0, -43.5, -36.4, -43.5, -43.3,
        -37.4, -41.1, -43.8, -43.6, -25.0, -27.5, -42.6, -43.9, -41.0, -42.3, -44.0, -44.3, -43.7, -44.1, -44.0, -43.7, -43.6,
        -44.0, -43.8, -43.7, -43.7, -43.9, -43.8, -44.0, -44.0, -43.9, -43.4, -43.2, -44.2, -44.2, -44.0, -43.6, -44.3, -44.3,
        -44.0, -35.3, -21.9, -22.6, -18.5, -20.9, -23.6, -19.5, -20.7, -20.7, -28.7, -29.4, -27.9, -21.3, -17.9, -19.2, -23.1,
        -27.8, -28.4, -28.2, -27.5, -29.2, -29.1, -26.8, -28.9, -24.3, -19.3, -19.8, -31.1, -28.9, -30.0, -22.8, -27.5, -17.8,
        -26.8, -22.4, -26.4, -29.1, -29.0, -29.2, -30.6, -21.5, -27.7, -29.4, -19.3, -19.5, -20.4, -21.7, -21.3, -31.6, -29.4,
        -26.2, -30.4, -29.9, -31.3, -28.7, -27.7, -20.9, -20.3, -28.0, -26.5, -28.3,
    ];

    #[test]
    fn the_lead_in_of_a_real_recording_is_found() {
        let start = speech_start_window(&REAL_ENVELOPE).expect("an 11-minute lead-in must be found");
        let secs = (start as f64) * ENVELOPE_WINDOW_SECS;
        assert!(
            (690.0..=720.0).contains(&secs),
            "expected the 11:30-12:00 transition, got {secs}s"
        );
    }

    #[test]
    fn a_spike_in_the_lead_in_is_not_speech() {
        // 4:30 and 6:30 rise to -32 and -27.5 for a single window each. A detector that
        // triggered on one loud window would cut the recording at 4:30 and lose seven minutes.
        let start = speech_start_window(&REAL_ENVELOPE).unwrap();
        assert!((start as f64) * ENVELOPE_WINDOW_SECS > 400.0, "stopped at an isolated spike");
    }

    #[test]
    fn a_recording_that_starts_talking_is_left_alone() {
        let levels: Vec<f32> = (0..200).map(|i| -25.0 + ((i % 7) as f32)).collect();
        assert_eq!(speech_start_window(&levels), None);
    }

    #[test]
    fn a_head_that_is_merely_a_little_quieter_is_not_cut() {
        // Reaches the head-gap check rather than being stopped by the length floor: the first
        // twelve windows dip and rise either side of the threshold so no sustained run starts
        // there, but their median sits close under it. That is a recording whose opening is
        // simply softer -- someone further from the microphone -- not a dead lead-in, and
        // cutting it would throw away two minutes of speech.
        let mut levels: Vec<f32> = (0..12).map(|i| if i % 2 == 0 { -38.0 } else { -41.0 }).collect();
        levels.extend(std::iter::repeat_n(-30.0, 188));

        assert_eq!(
            speech_start_window(&levels),
            None,
            "a head only ~9 dB down is speech, not silence"
        );
    }

    #[test]
    fn a_short_file_is_never_trimmed() {
        let levels: Vec<f32> = (0..8).map(|i| if i < 2 { -60.0 } else { -20.0 }).collect();
        assert_eq!(speech_start_window(&levels), None);
    }

    #[test]
    fn inf_windows_read_as_silence_not_as_parse_failure() {
        let out = "lavfi.astats.Overall.RMS_level=-inf\nlavfi.astats.Overall.RMS_level=-28.8";
        assert_eq!(parse_rms_levels(out), vec![-120.0, -28.8]);
    }
}
