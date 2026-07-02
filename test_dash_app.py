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
    _cell_styles,
    _mark_read,
    _mark_unread,
    _strip_marks,
    get_read_refs,
    has_count_cols,
    to_records,
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


def test_cell_styles_rtl_for_hebrew():
    he_verse = _cell_styles("he")[0]
    assert he_verse["direction"] == "rtl"
    assert he_verse["textAlign"] == "right"
    assert "direction" not in _cell_styles("en")[0]
    assert "direction" not in _cell_styles("el")[0]
