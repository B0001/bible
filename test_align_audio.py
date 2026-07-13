"""Tests for scripts/align_audio.py (Phase 10 audio-text alignment)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

from align_audio import align_chapter, match_anchors, skeleton  # noqa: E402


def words_for(text, start=0.0, step=1.0):
    """Whisper-style word dicts, one word per second."""
    out = []
    t = start
    for w in text.split():
        out.append({"word": " " + w, "start": t, "end": t + step * 0.8})
        t += step
    return out


VERSES = [
    ("T 1:1", "בראשית ברא אלהים"),
    ("T 1:2", "והארץ היתה תהו"),
    ("T 1:3", "ויאמר אלהים יהי אור"),
]
PERFECT_ASR = words_for("בראשית ברא אלהים והארץ היתה תהו ויאמר אלהים יהי אור")


def test_skeleton_drops_matres():
    assert skeleton("גיבור") == skeleton("גבר")
    assert skeleton("כיתים") == skeleton("כתם")


def test_perfect_reading_aligns_every_verse():
    rows = align_chapter(VERSES, PERFECT_ASR, duration=10.0)
    assert [r["ref"] for r in rows] == ["T 1:1", "T 1:2", "T 1:3"]
    assert rows[0]["start"] == 0.0
    assert rows[-1]["end"] == 10.0
    # boundaries land at the first word of each verse (±1 word slot)
    assert abs(rows[1]["start"] - 3.0) <= 1.0
    assert abs(rows[2]["start"] - 6.0) <= 1.0
    assert all(r["confidence"] == 1.0 for r in rows)


def test_boundaries_are_monotonic_and_contiguous():
    rows = align_chapter(VERSES, PERFECT_ASR, duration=10.0)
    for a, b in zip(rows, rows[1:]):
        assert a["end"] == b["start"]
        assert a["start"] <= a["end"]


def test_garbled_verse_gets_low_confidence_but_nonzero_width():
    # middle verse read as nonsense: no anchors there, spread by interpolation
    asr = words_for("בראשית ברא אלהים בלה בלה בלה ויאמר אלהים יהי אור")
    rows = align_chapter(VERSES, asr, duration=10.0)
    assert rows[1]["confidence"] == 0.0
    assert rows[1]["end"] > rows[1]["start"]
    assert rows[0]["confidence"] == 1.0
    assert rows[2]["confidence"] == 1.0


def test_rate_outlier_anchor_is_rejected():
    # 'צפרדע' is unique in both streams, but the ASR says it at 1s while its
    # proportional position is ~90% through a 100s chapter — a false match.
    verse_skels = ["a"] * 90 + [skeleton("צפרדע")] + ["b"] * 9
    asr_skels = ["x", skeleton("צפרדע"), "y"]
    times = [0.0, 1.0, 2.0]
    kept = match_anchors(verse_skels, asr_skels, times=times, duration=100.0)
    assert kept == []
    # without times the same pair is accepted (documents why the filter exists)
    assert match_anchors(verse_skels, asr_skels) != []


def test_no_asr_tokens_returns_empty():
    assert align_chapter(VERSES, [], duration=10.0) == []


def test_no_anchors_flat_zero_confidence():
    rows = align_chapter(VERSES, words_for("שלום עולם"), duration=10.0)
    assert len(rows) == 3
    assert all(r["confidence"] == 0.0 for r in rows)
