"""Tests for scripts/transcribe_audio.py's pure parts — no network."""
import json
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

import transcribe_audio as T  # noqa: E402


def test_multipart_body():
    fields = [("model", "whisper-large-v3"),
              ("timestamp_granularities[]", "word"),
              ("timestamp_granularities[]", "segment")]
    body, ctype = T.multipart_body(fields, "file", "Gen_001.opus", b"\x00AUDIO\xff",
                                   boundary="BOUND")
    assert ctype == "multipart/form-data; boundary=BOUND"
    assert body.count(b'name="timestamp_granularities[]"') == 2
    assert b'filename="Gen_001.opus"' in body
    assert b"\x00AUDIO\xff" in body
    assert body.endswith(b"--BOUND--\r\n")


def test_normalize_wraps_top_level_words():
    resp = {"text": "x", "segments": [{"text": "x"}],
            "words": [{"word": "a", "start": 0.0, "end": 0.5}]}
    out = T.normalize_transcript(resp)
    assert out["segments"] == [{"words": resp["words"]}]
    assert resp["segments"] == [{"text": "x"}]  # input not mutated


def test_normalize_passthrough_and_empty():
    has_words = {"segments": [{"words": [{"word": "a", "start": 0, "end": 1}]}]}
    assert T.normalize_transcript(has_words) is has_words
    empty = {"text": "", "segments": []}
    assert T.normalize_transcript(empty) is empty


def test_estimate_seconds(tmp_path):
    wav = tmp_path / "Gen_001.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)  # exactly 1 second
    assert T.estimate_seconds(str(wav)) == 1.0

    opus = tmp_path / "Gen_001.opus"
    opus.write_bytes(b"\x00" * (T.OPUS_BYTES_PER_SEC * 3))
    assert T.estimate_seconds(str(opus)) == 3.0


def test_estimate_cost():
    assert T.estimate_cost(3600, "groq") == 0.111
    assert round(T.estimate_cost(60, "openai"), 3) == 0.006


def test_find_chapters_prefers_opus(tmp_path):
    for name in ("Gen_001.opus", "Gen_001.wav", "Exod_002.wav",
                 "notes.txt", "Gen_1.opus"):
        (tmp_path / name).write_bytes(b"x")
    found = T.find_chapters(str(tmp_path))
    assert [(s, os.path.basename(p)) for s, p in found] == [
        ("Exod_002", "Exod_002.wav"),
        ("Gen_001", "Gen_001.opus"),
    ]


def test_main_writes_and_resumes(tmp_path, monkeypatch, capsys):
    audio_dir, out_dir = tmp_path / "serve", tmp_path / "transcripts"
    audio_dir.mkdir()
    for stem in ("Gen_001", "Gen_002"):
        (audio_dir / f"{stem}.opus").write_bytes(b"\x00" * T.OPUS_BYTES_PER_SEC)

    canned = {"text": "x", "words": [{"word": "בראשית", "start": 0.0, "end": 0.7}]}
    calls = []
    monkeypatch.setattr(T, "transcribe", lambda path, *a, **k: calls.append(path) or dict(canned))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(sys, "argv", ["transcribe_audio.py", "--audio-dir", str(audio_dir),
                                      "--out-dir", str(out_dir), "--limit", "1"])
    T.main()
    assert len(calls) == 1
    saved = json.loads((out_dir / "Gen_001.json").read_text())
    assert saved["segments"][0]["words"][0]["word"] == "בראשית"

    T.main()  # resume: Gen_001 skipped, Gen_002 sent
    assert [os.path.basename(p) for p in calls] == ["Gen_001.opus", "Gen_002.opus"]
    assert "1 already transcribed" in capsys.readouterr().out
