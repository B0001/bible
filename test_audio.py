"""Tests for the Phase 10 audio scripts' pure parts (no audio, no network):
scripts/ingest_audio.py (filename parsing, gap inventory) and
scripts/align_audio.py (skeletons, anchor matching, boundary interpolation,
confidence flagging on synthetic token streams)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

import align_audio  # noqa: E402
import ingest_audio  # noqa: E402


# --- ingest_audio -----------------------------------------------------------

def test_book_table_is_masoretic_929():
    assert len(ingest_audio.BOOKS) == 39
    assert sum(n for _, _, n in ingest_audio.BOOKS) == 929
    assert ingest_audio.CHAPTERS["Joel"] == 4  # Masoretic, not English 3
    assert ingest_audio.CHAPTERS["Mal"] == 3  # Masoretic, not English 4


def test_parse_filename():
    assert ingest_audio.parse_filename("1_Chronicles_01.wav") == ("1Chr", 1)
    assert ingest_audio.parse_filename("Psalms_08.wav") == ("Ps", 8)
    assert ingest_audio.parse_filename("2_Chronicles_36.WAV") == ("2Chr", 36)
    assert ingest_audio.parse_filename("Song_of_Solomon_02.wav") == ("Song", 2)


def test_parse_filename_rejects_junk():
    assert ingest_audio.parse_filename("README.txt") is None
    assert ingest_audio.parse_filename("Atlantis_01.wav") is None
    assert ingest_audio.parse_filename("Genesis.wav") is None


def test_compact_ranges():
    assert ingest_audio.compact([7, 9, 10, 17, 18, 19]) == "7, 9-10, 17-19"
    assert ingest_audio.compact([3]) == "3"


def test_inventory_gaps():
    names = ["Joel_01.wav", "Joel_02.wav", "Joel_04.wav", "cover.jpg"]
    present, unrecognized = ingest_audio.inventory(names)
    assert present["Joel"] == {1, 2, 4}
    assert unrecognized == ["cover.jpg"]
    missing = set(range(1, 5)) - present["Joel"]
    assert missing == {3}


# --- align_audio -------------------------------------------------------------

def test_skeleton_drops_matres():
    assert align_audio.skeleton("גיבור") == align_audio.skeleton("גבור")


def _words(tokens, start=0.0):
    return [
        {"word": w, "start": start + i, "end": start + i + 0.9}
        for i, w in enumerate(tokens)
    ]


def _words_at(pairs):
    return [{"word": w, "start": t, "end": t + 0.9} for w, t in pairs]


# Three verses, two tokens each, all unique after skeletonization
# (avoid י/ו so tokens survive matres stripping).
VERSES = [("T 1:1", "אבג דגש"), ("T 1:2", "הקל מנס"), ("T 1:3", "פרק תלם")]
ALL_TOKENS = "אבג דגש הקל מנס פרק תלם".split()


def test_align_clean_chapter():
    aligned = align_audio.align_chapter(VERSES, _words(ALL_TOKENS), duration=6.0)
    assert [v["ref"] for v in aligned] == ["T 1:1", "T 1:2", "T 1:3"]
    starts = [v["start"] for v in aligned]
    assert starts == sorted(starts)
    assert all(v["end"] > v["start"] for v in aligned)
    assert aligned[-1]["end"] == 6.0
    assert all(v["confidence"] == 1.0 for v in aligned)


def test_align_flags_dropped_verse():
    # Whisper skipped verse 2 entirely, but the surviving words keep their true
    # positions in the audio (the narration itself has no gap).
    heard = _words_at([("אבג", 0), ("דגש", 1), ("פרק", 4), ("תלם", 5)])
    aligned = align_audio.align_chapter(VERSES, heard, duration=6.0)
    by_ref = {v["ref"]: v for v in aligned}
    assert by_ref["T 1:2"]["confidence"] < 0.5  # would be flagged
    assert by_ref["T 1:1"]["confidence"] == 1.0
    assert by_ref["T 1:3"]["confidence"] == 1.0


def test_align_tolerates_preamble():
    # Chapter announcement ("ספר ... פרק א") precedes verse 1 in the narration.
    preamble = _words("ספר קדמה".split())
    body = _words(ALL_TOKENS, start=2.0)
    aligned = align_audio.align_chapter(VERSES, preamble + body, duration=10.5)
    assert aligned[0]["start"] == 2.0  # verse 1 starts after the preamble


def test_match_anchors_skips_duplicates_and_crossings():
    anchors = align_audio.match_anchors(["a", "b", "c", "a"], ["b", "c"])
    assert anchors == [(1, 0), (2, 1)]  # "a" is duplicated -> not an anchor
    # crossed pair gets dropped to keep the sequence monotonic
    anchors = align_audio.match_anchors(["a", "b", "c"], ["c", "a", "b"])
    assert anchors == [(0, 1), (1, 2)]


def test_match_anchors_time_filter_rejects_outliers():
    # "b" is unique in both streams but heard at the wrong end of the audio —
    # a coincidental match, not the same word; the plausibility filter drops it.
    anchors = align_audio.match_anchors(
        ["a", "b"], ["a", "b"], times=[0.0, 9.0], duration=10.0
    )
    assert anchors == [(0, 0)]


def test_interp_clamps():
    assert align_audio.interp(-1, [0, 10], [0.0, 5.0]) == 0.0
    assert align_audio.interp(99, [0, 10], [0.0, 5.0]) == 5.0
    assert align_audio.interp(5, [0, 10], [0.0, 5.0]) == 2.5
