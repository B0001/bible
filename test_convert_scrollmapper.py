"""Tests for scripts/convert_scrollmapper.py: refs must align with the
tushortz texts verse for verse, or rhyme.py's stitch/joint misalign silently."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

import pytest
from convert_scrollmapper import BOOKS, EXPECTED_VERSES, convert_rows


def _rows(n_books=66, total=EXPECTED_VERSES, text="In the beginning.", first="Genesis",
          last="Revelation of John"):
    names = [first] + [f"Book{i}" for i in range(1, n_books - 1)] + [last]
    per, extra = divmod(total, n_books)
    rows = []
    for b, name in enumerate(names):
        for v in range(per + (b < extra)):
            rows.append({"Book": name, "Chapter": "1", "Verse": str(v + 1), "Text": text})
    return rows


def test_books_map_by_position_to_tushortz_spelling():
    out = convert_rows(_rows())
    assert len(out) == EXPECTED_VERSES
    assert out[0] == ("genesis 1:1", "In the beginning.")
    assert out[-1][0].startswith("revelation 1:")
    assert {ref.rpartition(" ")[0] for ref, _ in out} == set(BOOKS)


def test_dash_becomes_em_dash_so_refs_survive():
    # YLT Genesis 3:22 ends "...to the age, --"; written as-is, " -- -- ref"
    # parses to a ref of "-- genesis 3:22".
    out = convert_rows(_rows(text="and lived to the age, --"))
    line = f"{out[0][1]} -- {out[0][0]}"
    assert line.partition(" -- ")[2] == "genesis 1:1"
    assert out[0][1] == "and lived to the age, —"


def test_glued_god_is_split():
    # scrollmapper's Darby: "the Spirit ofGod was hovering", 3,439 times.
    out = convert_rows(_rows(text="the Spirit ofGod, theGodhead; God said"))
    assert out[0][1] == "the Spirit of God, the Godhead; God said"


@pytest.mark.parametrize("kwargs, message", [
    ({"n_books": 78}, "expected 66 books"),            # DRC: Catholic canon
    ({"first": "Tobit"}, "book order"),
    ({"total": EXPECTED_VERSES - 1}, "expected 31102 verses"),
    ({"text": ""}, "empty verses"),                    # Tyndale: mostly empty
])
def test_incomplete_or_other_canons_are_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        convert_rows(_rows(**kwargs))
