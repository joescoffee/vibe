use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Transcript {
    pub processing_time_sec: u64,
    pub segments: Vec<Segment>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Segment {
    pub start: i64,
    pub stop: i64,
    pub text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub speaker: Option<i32>,
}

/// Whether the model looped instead of transcribing.
///
/// Whisper answers audio it cannot decode with the highest-prior text from its training data
/// -- for Chinese, YouTube subscribe boilerplate -- and then keeps answering with it. What
/// identifies that is not the phrase, which drifts ("我们的朋友们" -> "我们的朋友" ->
/// "谢谢大家的朋友们"), and not one line's share of the transcript, which the drift keeps
/// under any useful threshold. It is that the same line comes back *consecutively*. Speech
/// does not repeat itself back to back; a stuck decoder does nothing else.
///
/// Calibrated on real transcripts from this machine rather than on invented ones:
///
/// | consecutive-repeat share | source                          |
/// |--------------------------|---------------------------------|
/// | 1.9% – 15.4%             | six genuine meeting transcripts |
/// | 32.5%                    | a 435-segment looped transcript |
/// | 90.5%                    | a 22-segment looped transcript  |
///
/// The threshold sits in the gap. Two earlier candidates were tried and discarded: the
/// dominant line's share (25.1% on the looped transcript, 12.6% on a genuine one -- too
/// close) and the distinct-text ratio (57.0% vs 73.4% -- likewise).
pub fn consecutive_repeat_share(segments: &[Segment]) -> f64 {
    if segments.len() < 2 {
        return 0.0;
    }
    let repeats = segments
        .windows(2)
        .filter(|pair| {
            let a = pair[0].text.trim();
            !a.is_empty() && a == pair[1].text.trim()
        })
        .count();
    (repeats as f64) / ((segments.len() - 1) as f64)
}

/// Shortest transcript worth judging. Below this a run of identical lines can be real.
pub const MIN_SEGMENTS_TO_JUDGE: usize = 10;

/// Sits between the worst genuine transcript measured (15.4%) and the best looped one (32.5%).
pub const DEGENERATE_REPEAT_SHARE: f64 = 0.25;

/// The looped line and how much of the transcript it took, or `None` if the output looks real.
pub fn degenerate_loop(segments: &[Segment]) -> Option<(String, f64)> {
    if segments.len() < MIN_SEGMENTS_TO_JUDGE {
        return None;
    }
    let share = consecutive_repeat_share(segments);
    if share <= DEGENERATE_REPEAT_SHARE {
        return None;
    }
    let worst = segments
        .windows(2)
        .find(|pair| {
            let a = pair[0].text.trim();
            !a.is_empty() && a == pair[1].text.trim()
        })
        .map(|pair| pair[0].text.trim().to_string())
        .unwrap_or_default();
    Some((worst, share))
}

#[cfg(test)]
mod repetition_tests {
    use super::*;

    fn seg(text: &str) -> Segment {
        Segment {
            start: 0,
            stop: 1,
            text: text.to_string(),
            speaker: None,
        }
    }

    fn segs(texts: &[&str]) -> Vec<Segment> {
        texts.iter().map(|t| seg(t)).collect()
    }

    #[test]
    fn the_sample_that_prompted_this_is_caught() {
        let mut texts: Vec<&str> = vec!["请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"; 3];
        texts.extend(["打赏支持明镜与点点栏目"; 5]);
        texts.extend(["打赏支持明镜与点栏目"; 14]);

        let (line, share) = degenerate_loop(&segs(&texts)).expect("90% consecutive repeats must be caught");
        assert!(share > 0.9, "share was {share}");
        assert!(line.contains("明镜"));
    }

    #[test]
    fn a_drifting_loop_is_caught_even_though_no_single_line_dominates() {
        // Shape of the 435-segment transcript: the phrase mutates, so the most common line
        // held only 25.1% -- under any dominance threshold that spares real speech.
        let mut texts: Vec<&str> = Vec::new();
        for variant in ["我们的朋友们", "我们的朋友", "谢谢大家的朋友们"] {
            texts.extend(std::iter::repeat_n(variant, 6));
        }
        texts.extend(["一", "二", "三", "四"]);

        assert!(degenerate_loop(&segs(&texts)).is_some());
    }

    #[test]
    fn genuine_speech_is_left_alone() {
        // 15.4% consecutive repeats -- the worst of six real transcripts -- must pass.
        let mut texts: Vec<String> = (0..100).map(|i| format!("sentence {i}")).collect();
        for i in (0..15).map(|i| i * 6) {
            texts[i + 1] = texts[i].clone();
        }
        let owned: Vec<&str> = texts.iter().map(String::as_str).collect();

        assert!(
            degenerate_loop(&segs(&owned)).is_none(),
            "a real transcript must not be rejected"
        );
    }

    #[test]
    fn a_short_transcript_is_never_judged() {
        assert!(
            degenerate_loop(&segs(&["yes"; 9])).is_none(),
            "9 identical lines can be a real answer"
        );
    }
}
