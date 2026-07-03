"""Tests for dash_app's pure logic (no Dash test client needed).

Env vars must be set before importing dash_app: the module loads Bible CSVs and
resolves the reads DB path at import time. A minimal fallback CSV guarantees the
import succeeds even when out/*.csv don't exist (e.g. in CI), and READS_DB is
pointed at a temp file so tests never touch real user state.
"""
import os
import tempfile

import polars as pl

_tmp = tempfile.mkdtemp()
_csv = os.path.join(_tmp, "graded.csv")
with open(_csv, "w") as f:
    f.write("ref,verse,comprehension_rate,known_count,total_count\n")
    f.write("Gen 1:1,the cat sat,1.0,3,3\n")
os.environ["BIBLE_GRADED_CSV"] = _csv
os.environ["READS_DB"] = os.path.join(_tmp, "reads.db")

from dash_app import (  # noqa: E402
    MAX_TABLE_ROWS,
    _cell_styles,
    _mark_read,
    _mark_unread,
    _strip_marks,
    get_read_refs,
    has_count_cols,
    to_records,
    update_table,
)


# --------------------------------------------------------------------------- #
# _strip_marks: nikudim-insensitive search
# --------------------------------------------------------------------------- #

def test_strip_marks_hebrew_niqqud():
    assert _strip_marks("שָׁלוֹם") == "שלום"


def test_strip_marks_hebrew_cantillation():
    # Gen 1:1 first word with cantillation + niqqud
    assert _strip_marks("בְּרֵאשִׁ֖ית") == "בראשית"


def test_strip_marks_greek_diacritics():
    assert _strip_marks("ἀρχῇ") == "αρχη"


def test_strip_marks_english_unchanged():
    assert _strip_marks("In the beginning") == "In the beginning"


# --------------------------------------------------------------------------- #
# to_records: Read column
# --------------------------------------------------------------------------- #

def test_to_records_marks_read_refs():
    frame = pl.DataFrame(
        {"ref": ["a", "b"], "verse": ["x", "y"], "comprehension_rate": [1.0, 0.5]}
    )
    records = to_records(frame, read_refs={"a"})
    assert records[0]["read"] == "✓"
    assert records[1]["read"] == ""


def test_to_records_percentage_conversion():
    frame = pl.DataFrame(
        {"ref": ["a"], "verse": ["x"], "comprehension_rate": [0.25]}
    )
    assert to_records(frame, set())[0]["comprehension_%"] == 25.0


# --------------------------------------------------------------------------- #
# Read tracking round-trip (against the tmp READS_DB)
# --------------------------------------------------------------------------- #

def test_reads_round_trip():
    _mark_read("testbible", ["Gen 1:1", "Gen 1:2"])
    assert get_read_refs("testbible") == {"Gen 1:1", "Gen 1:2"}
    _mark_unread("testbible", ["Gen 1:1"])
    assert get_read_refs("testbible") == {"Gen 1:2"}
    _mark_unread("testbible", ["Gen 1:2"])
    assert get_read_refs("testbible") == set()


def test_mark_read_idempotent():
    _mark_read("idempotent", ["Gen 1:1"])
    _mark_read("idempotent", ["Gen 1:1"])
    assert get_read_refs("idempotent") == {"Gen 1:1"}
    _mark_unread("idempotent", ["Gen 1:1"])


def test_reads_scoped_per_bible():
    _mark_read("bible-a", ["Gen 1:1"])
    assert get_read_refs("bible-b") == set()
    _mark_unread("bible-a", ["Gen 1:1"])


# --------------------------------------------------------------------------- #
# Misc helpers
# --------------------------------------------------------------------------- #

def test_has_count_cols():
    with_counts = pl.DataFrame(
        {"ref": ["a"], "verse": ["x"], "comprehension_rate": [1.0],
         "known_count": [1], "total_count": [1]}
    )
    without = with_counts.drop("known_count", "total_count")
    assert has_count_cols(with_counts)
    assert not has_count_cols(without)


def test_update_table_caps_payload():
    """A wide-open filter never ships more than MAX_TABLE_ROWS rows."""
    import dash_app as _da
    bible_id = next(iter(_da.BIBLES))
    records, count, _progress, _styles = update_table(bible_id, [0, 100], None, "", [], 0)
    assert len(records) <= MAX_TABLE_ROWS
    total = _da.BIBLES[bible_id]["df"].height
    if total > MAX_TABLE_ROWS:
        assert f"first {MAX_TABLE_ROWS}" in count


def test_to_records_unknown_column():
    """When count columns exist, each record carries unknown = total - known."""
    frame = pl.DataFrame(
        {"ref": ["a"], "verse": ["x y z"], "comprehension_rate": [2 / 3],
         "known_count": [2], "total_count": [3]}
    )
    assert to_records(frame, set())[0]["unknown"] == 1


def test_update_table_max_unknown_filter():
    """max_unknown keeps only verses with <= N unknown words."""
    import dash_app as _da
    bible_id = next(iter(_da.BIBLES))
    df = _da.BIBLES[bible_id]["df"]
    if not has_count_cols(df):
        return  # fallback CSV without counts: filter is a no-op by design
    records, _count, _progress, _styles = update_table(bible_id, [0, 100], 1, "", [], 0)
    assert all(r["unknown"] <= 1 for r in records)


def test_cell_styles_rtl_for_hebrew():
    he_verse = _cell_styles("he")[0]
    assert he_verse["direction"] == "rtl"
    assert he_verse["textAlign"] == "right"
    assert "direction" not in _cell_styles("en")[0]
    assert "direction" not in _cell_styles("el")[0]


# --------------------------------------------------------------------------- #
# Audio playback (P10.2): manifest loading, click lookup, /audio route
# --------------------------------------------------------------------------- #

import json  # noqa: E402

import dash_app  # noqa: E402


def _audio_fixture(tmp_path, with_audio_file=True):
    """Write a D5 manifest + sidecar (+ fake opus) under tmp_path; return the
    manifest path (absolute paths inside, so _HERE-joining is a no-op)."""
    serve = tmp_path / "serve"
    serve.mkdir(exist_ok=True)
    if with_audio_file:
        (serve / "Gen_001.opus").write_bytes(b"OpusHead-fake")
    sidecar = tmp_path / "Gen_001.json"
    sidecar.write_text(json.dumps({
        "bible_id": "wlc", "book": "Gen", "chapter": 1,
        "audio": "data/audio/serve/Gen_001.opus", "duration": 10.0,
        "verses": [
            {"ref": "Gen 1:1", "start": 0.5, "end": 4.0, "confidence": 0.9},
            {"ref": "Gen 1:2", "start": 4.0, "end": 9.5, "confidence": 0.8},
        ],
    }))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "bible_id": "wlc", "audio_dir": str(serve),
        "chapters": [{"book": "Gen", "chapter": 1, "sidecar": str(sidecar)}],
    }))
    return str(manifest)


def test_load_audio_manifest(tmp_path):
    audio = dash_app.load_audio_manifest(_audio_fixture(tmp_path))
    assert audio["by_ref"]["Gen 1:2"] == {"file": "Gen_001.opus", "start": 4.0}
    assert [v["ref"] for v in audio["chapters"]["Gen_001.opus"]] == ["Gen 1:1", "Gen 1:2"]


def test_load_audio_manifest_degrades_to_none(tmp_path):
    assert dash_app.load_audio_manifest(str(tmp_path / "absent.json")) is None
    # sidecar present but audio file missing -> chapter skipped -> no audio
    assert dash_app.load_audio_manifest(_audio_fixture(tmp_path, with_audio_file=False)) is None


def test_load_audio_manifest_skips_broken_sidecar(tmp_path):
    manifest_path = _audio_fixture(tmp_path)
    manifest = json.loads(open(manifest_path).read())
    manifest["chapters"].append({"book": "Gen", "chapter": 2, "sidecar": str(tmp_path / "nope.json")})
    open(manifest_path, "w").write(json.dumps(manifest))
    audio = dash_app.load_audio_manifest(manifest_path)
    assert list(audio["chapters"]) == ["Gen_001.opus"]  # good chapter survives


def _install_audio_bible(tmp_path):
    dash_app.BIBLES["audiotest"] = {
        "name": "t", "lang": "he", "df": None, "df_ord": None,
        "audio": dash_app.load_audio_manifest(_audio_fixture(tmp_path)),
    }


def test_audio_for_click(tmp_path):
    _install_audio_bible(tmp_path)
    try:
        chapter = dash_app.audio_for_click("audiotest", "Gen 1:2")
        assert chapter["src"] == "/audio/audiotest/Gen_001.opus"
        assert chapter["seek"] == 4.0
        assert len(chapter["verses"]) == 2
        assert dash_app.audio_for_click("audiotest", "Exod 1:1") is None  # unaligned verse
        assert dash_app.audio_for_click("nasb", "Gen 1:1") is None  # bible without audio
        assert dash_app.audio_for_click("nope", "Gen 1:1") is None
    finally:
        del dash_app.BIBLES["audiotest"]


def test_audio_route_serves_and_guards(tmp_path):
    _install_audio_bible(tmp_path)
    (tmp_path / "secret.txt").write_text("private")  # outside the audio dir
    try:
        client = dash_app.server.test_client()
        ok = client.get("/audio/audiotest/Gen_001.opus")
        assert ok.status_code == 200
        assert ok.data == b"OpusHead-fake"
        assert client.get("/audio/audiotest/../secret.txt").status_code == 404
        assert client.get("/audio/audiotest/%2e%2e/secret.txt").status_code == 404
        assert client.get("/audio/nasb/Gen_001.opus").status_code == 404
        assert client.get("/audio/unknown/Gen_001.opus").status_code == 404
    finally:
        del dash_app.BIBLES["audiotest"]


def test_to_records_row_id_is_ref():
    frame = pl.DataFrame(
        {"ref": ["Gen 1:1"], "verse": ["x"], "comprehension_rate": [1.0]}
    )
    assert to_records(frame, set())[0]["id"] == "Gen 1:1"
