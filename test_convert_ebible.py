"""Tests for scripts/convert_ebible.py: its texts replace getbible's copies, so
refs must come out spelled exactly as before or saved read verses are lost."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

import pytest
from convert_ebible import CODES, FIXES, TEXTS, apply_fixes, convert_lines


def _lines(codes=CODES, text="Texto [añadido]."):
    return [f"{code} 1:1 {text}" for code in codes]


def test_codes_map_to_the_getbible_book_names():
    assert all(len(names) == len(CODES) == 66 for _, names, _ in TEXTS.values())
    out = convert_lines([*_lines(), "REV 1:2 "], TEXTS["spaRV1909"][1])
    assert out[0] == ("Génesis 1:1", "Texto [añadido].")
    assert out[-1] == ("Apocalipsis 1:1", "Texto [añadido].")  # empty REV 1:2 dropped


def test_brackets_are_stripped_only_when_asked():
    names = TEXTS["spaRV1909"][1]
    assert convert_lines(_lines(), names, strip_brackets=True)[0][1] == "Texto añadido."
    assert convert_lines(_lines(), names)[0][1] == "Texto [añadido]."


def test_text_is_nfc_like_the_getbible_copies():
    # eBible's Van Dyck types shadda before fatha; NFC, like getbible's copy,
    # puts fatha (combining class 30) before shadda (33).
    out = convert_lines(_lines(text="رَّب"), TEXTS["arb-vd"][1])
    assert out[0][1] == "رَّب"


@pytest.mark.parametrize("lines, message", [
    (_lines(CODES[:-1]), "66 books"),                      # a book missing
    (_lines(["TOB", *CODES]), "66 books"),                 # a deuterocanonical canon
    ([*_lines(), *["REV 1:2 "] * 101], "empty verses"),
    (_lines(text="and -- so"), "separator"),
    (["GEN 1 Au commencement"], "unparsed"),
])
def test_bad_sources_are_refused(lines, message):
    with pytest.raises(ValueError, match=message):
        convert_lines(lines, TEXTS["fraLSG"][1])


def test_lsg_fixes_restore_the_1910_division():
    (job, _), (mark, _) = FIXES["fraLSG"]
    src = ["JOB 34:35 a", *job, "JOB 35:1 b", "MAR 10:51 c", *mark, "MAR 11:1 d"]
    refs = [" ".join(ln.split()[:2]) for ln in apply_fixes(src, FIXES["fraLSG"])]
    assert refs == ["JOB 34:35", "JOB 34:36", "JOB 34:37", "JOB 35:1",
                    "MAR 10:51", "MAR 10:52", "MAR 11:1"]
    for old, new in FIXES["fraLSG"]:  # only the verse boundary moves
        words = [" ".join(ln.split(" ", 2)[2] for ln in lines).split() for lines in (old, new)]
        assert words[0] == words[1]


def test_lsg_fixes_fail_loudly_once_ebible_changes_the_verses():
    job, job_1910 = FIXES["fraLSG"][0]
    with pytest.raises(ValueError, match="JOB 34:36 fix matches the source 0 times"):
        apply_fixes(job_1910, FIXES["fraLSG"])  # eBible restored 34:37 itself
    with pytest.raises(ValueError, match="2 times"):
        apply_fixes(job + job, FIXES["fraLSG"])
