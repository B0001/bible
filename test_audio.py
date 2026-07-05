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


def test_align_starts_non_decreasing():
    # A stray late-timed word for an early verse must not push its start past a
    # later verse's; starts stay monotonic and no verse ends before it begins.
    words = _words_at([("אבג", 0), ("דגש", 9), ("הקל", 2), ("מנס", 3),
                       ("פרק", 4), ("תלם", 5)])
    aligned = align_audio.align_chapter(VERSES, words, duration=10.0)
    starts = [v["start"] for v in aligned]
    assert starts == sorted(starts)
    assert all(v["end"] >= v["start"] for v in aligned)


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


# --- align_corpus -------------------------------------------------------------

def test_chapter_of_stem():
    assert align_audio.chapter_of_stem("1Chr_001") == ("1Chr", 1)
    assert align_audio.chapter_of_stem("Ps_150") == ("Ps", 150)
    assert align_audio.chapter_of_stem("readme") is None


def test_verses_by_chapter(tmp_path):
    bible = tmp_path / "bible.txt"
    bible.write_text(
        "אבג דגש -- T 1:2\nהקל מנס -- T 1:1\nפרק תלם -- T 2:1\n", encoding="utf-8"
    )
    by_ch = align_audio.verses_by_chapter(str(bible))
    assert [r for r, _ in by_ch[("T", 1)]] == ["T 1:1", "T 1:2"]  # verse order
    assert [r for r, _ in by_ch[("T", 2)]] == ["T 2:1"]


def _corpus_fixture(tmp_path):
    bible = tmp_path / "bible.txt"
    bible.write_text(
        "אבג דגש -- T 1:1\nהקל מנס -- T 1:2\nפרק תלם -- T 1:3\n", encoding="utf-8"
    )
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    import json
    (transcripts / "T_001.json").write_text(json.dumps(
        {"segments": [{"words": [
            {"word": w, "start": i, "end": i + 0.9} for i, w in enumerate(ALL_TOKENS)
        ]}]}
    ))
    audio_dir = tmp_path / "serve"
    audio_dir.mkdir()
    (audio_dir / "T_001.opus").write_bytes(b"x")
    return bible, transcripts, audio_dir


def test_align_corpus_writes_sidecars_and_manifest(tmp_path, capsys):
    import json
    bible, transcripts, audio_dir = _corpus_fixture(tmp_path)
    out_dir = tmp_path / "out"
    manifest_path = tmp_path / "manifest.json"
    chapters = align_audio.align_corpus(
        str(bible), str(transcripts), str(audio_dir), str(out_dir),
        str(manifest_path), bible_id="test",
    )
    assert [(c["book"], c["chapter"]) for c in chapters] == [("T", 1)]
    sidecar = json.loads((out_dir / "T_001.json").read_text())
    assert sidecar["audio"].endswith("T_001.opus")
    assert [v["ref"] for v in sidecar["verses"]] == ["T 1:1", "T 1:2", "T 1:3"]
    manifest = json.loads(manifest_path.read_text())
    assert manifest["bible_id"] == "test"
    assert manifest["chapters"][0]["sidecar"] == str(out_dir / "T_001.json")
    assert "overall:" in capsys.readouterr().out


def test_align_corpus_resumes(tmp_path, capsys):
    import json
    bible, transcripts, audio_dir = _corpus_fixture(tmp_path)
    out_dir = tmp_path / "out"
    manifest_path = tmp_path / "manifest.json"
    args = (str(bible), str(transcripts), str(audio_dir), str(out_dir), str(manifest_path))
    align_audio.align_corpus(*args)
    before = (out_dir / "T_001.json").stat().st_mtime_ns
    align_audio.align_corpus(*args)
    assert (out_dir / "T_001.json").stat().st_mtime_ns == before  # not recomputed
    assert "(0 aligned this run)" in capsys.readouterr().out
