"""Tests for scripts/convert_ebible.py: its texts replace getbible's copies, so
refs must come out spelled exactly as before or saved read verses are lost."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

import pytest
from convert_ebible import CODES, TEXTS, convert_lines


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
