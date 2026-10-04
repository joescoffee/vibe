#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Measure the one transcription failure this repo has actually suffered.

Whisper answers audio it cannot decode with the highest-prior text from its training
data -- for Chinese, YouTube subscribe boilerplate -- and then keeps answering with it.
`0de9a164` patched `whisper-rs` so a window whose words merely repeat the history no
longer extends it. Nothing executable guards that patch: delete
`desktop/src-tauri/binaries/` and `chore setup` silently restores upstream's unpatched
binary (see CLAUDE.md), and no test would notice.

The metric is the **longest run of consecutive boilerplate segments**, not boilerplate's
share of the transcript. The distinction is the whole point:

    share            0.06% .. 4.37% positive, 0.00% negative  -- one real mention of
                     "订阅" in genuine speech is indistinguishable from a short loop
    consecutive run  6, 16, 19 positive;  0 for all 22 negatives  -- no overlap

That second measurement is why this can gate and `transcript.rs`'s repetition share
cannot: there, genuine speech reaches 34-45% and a stuck decoder 99%, points on one
scale rather than two populations. Measured over the 25 judgeable transcripts on the
author's machine, 2026-10-04.

Usage:
    transcript_quality.py <dir>...        # report every transcript, exit 1 on any hit
    transcript_quality.py --self-test     # prove the measure can fail, then exit
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

# Training-data boilerplate Whisper falls back on, from transcripts produced here.
# Substring match on purpose: the phrase drifts ("我们的朋友们" -> "我们的朋友") while
# the tokens do not.
BOILERPLATE = ("明镜", "订阅", "點贊", "点赞", "打赏", "转发", "字幕志愿者", "请不吝", "Amara.org")

# Anything at or above this is a decoder loop. The observed gap is 0 against 6, so the
# threshold sits inside a real gap rather than on a continuum -- unlike
# DEGENERATE_REPEAT_SHARE, which does not and therefore only warns.
MAX_CONSECUTIVE = 3

# Below this a run of identical lines can be real, matching MIN_SEGMENTS_TO_JUDGE in
# desktop/src-tauri/src/transcript.rs.
MIN_SEGMENTS = 10


def longest_run(segments: list[dict]) -> int:
    run = best = 0
    for segment in segments:
        text = segment.get("text") or ""
        if any(token in text for token in BOILERPLATE):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def measure(path: str) -> tuple[int, int] | None:
    """`(longest_run, segment_count)`, or None when the transcript is too short to judge."""
    try:
        with open(path, encoding="utf-8") as handle:
            segments = json.load(handle).get("segments") or []
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED  {path} -- unreadable ({exc})", file=sys.stderr)
        return None
    if len(segments) < MIN_SEGMENTS:
        return None
    return longest_run(segments), len(segments)


def self_test() -> int:
    """Prove the measure separates, and that it fails when it should.

    A check nobody has watched fail is not a check. These fixtures encode the two
    populations the real data showed, including the false positive a share-based
    measure would produce.
    """
    cases = [
        ("clean speech", [{"text": t} for t in ("今天天氣不錯", "對啊", "那我們開始吧") * 5], 0),
        ("one real mention", [{"text": "記得訂閱我們的頻道"}] + [{"text": "好的"}] * 12, 0),
        ("a loop", [{"text": "好的"}] * 5 + [{"text": "请不吝点赞 订阅 转发"}] * 8 + [{"text": "好的"}] * 3, 8),
    ]
    failures = 0
    for name, segments, expected in cases:
        got = longest_run(segments)
        verdict = "ok" if got == expected else "MISMATCH"
        if got != expected:
            failures += 1
        print(f"  {verdict:9} {name:20} longest_run={got} expected={expected}")

    # The measure must also *not* fire on the clean cases at the gate threshold.
    gated = [n for n, s, _ in cases if longest_run(s) >= MAX_CONSECUTIVE]
    print(f"  gate at >={MAX_CONSECUTIVE} fires on: {gated}")
    if gated != ["a loop"]:
        print("  MISMATCH  the gate must fire on the loop and nothing else", file=sys.stderr)
        failures += 1
    print("self-test: " + ("PASS" if not failures else f"FAIL ({failures})"))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="*", help="directories holding project folders")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.roots:
        parser.error("give at least one directory, or --self-test")

    records = [p for root in args.roots
               for p in sorted(glob.glob(os.path.join(os.path.expanduser(root), "*", "transcript.vibe.json")))]
    if not records:
        # Not a pass. An empty sweep and a clean sweep must not read the same.
        print(f"BLOCKED  no transcript.vibe.json under {args.roots}", file=sys.stderr)
        return 2

    hits, judged = [], 0
    for path in records:
        result = measure(path)
        if result is None:
            continue
        judged += 1
        run, count = result
        if run >= MAX_CONSECUTIVE:
            hits.append((run, count, path))

    for run, count, path in sorted(hits, reverse=True):
        print(f"FAIL     longest consecutive boilerplate = {run} of {count} segments -- {path}")
    print(f"judged {judged} of {len(records)} transcripts ({len(records) - judged} too short); {len(hits)} over the threshold")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
