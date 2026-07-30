"""Tests for scripts/export_dataset.py — the HuggingFace dataset export.

The property that matters: `book_usfm` is a trustworthy join key across
translations whose canons differ in size *and* order. Mapping is positional,
so these tests pin the canon sequences and prove the guard rails fire when a
source text's book order drifts.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

import pytest  # noqa: E402

from export_dataset import (  # noqa: E402
    BOOK_ORDER,
    ENGLISH_NAMES,
    GREEK_NAMES,
    HEBREW_NAMES,
    NT27,
    OT39,
    PROT66,
    STATEN67,
    SYNODAL78,
    book_sequence,
    split_ref,
    validate_books,
)


def _in_block(text, lo, hi):
    """Every letter of `text` sits in the Unicode block [lo, hi]."""
    return all(lo <= ord(c) <= hi for c in text if c.isalpha())


# ------------------------------------------------------------ ref parsing

@pytest.mark.parametrize("ref, expected", [
    ("genesis 1:1", ("genesis", 1, 1)),
    ("Gen 1:1", ("Gen", 1, 1)),
    ("1 Samuel 3:10", ("1 Samuel", 3, 10)),          # space inside the book name
    ("1. Mose 2:4", ("1. Mose", 2, 4)),              # Luther's numbered books
    ("1-я Царств 3:10", ("1-я Царств", 3, 10)),      # Cyrillic + hyphen
    ("Song of Songs 8:14", ("Song of Songs", 8, 14)),
    ("psalms 119:176", ("psalms", 119, 176)),
])
def test_split_ref(ref, expected):
    assert split_ref(ref) == expected


def test_split_ref_tolerates_malformed():
    """A ref with no chapter:verse must not raise, and must not swallow part of
    the book name — the whole string is the book."""
    assert split_ref("weird ref") == ("weird ref", None, None)
    assert split_ref("Obadiah") == ("Obadiah", None, None)


# ------------------------------------------------------------ canon integrity

def test_canon_lengths():
    assert (len(OT39), len(NT27), len(PROT66)) == (39, 27, 66)
    assert len(STATEN67) == 67
    assert len(SYNODAL78) == 78


@pytest.mark.parametrize("name, codes", [
    ("PROT66", PROT66), ("STATEN67", STATEN67), ("SYNODAL78", SYNODAL78),
])
def test_no_duplicate_codes(name, codes):
    """A repeated code would silently merge two different books on join."""
    assert len(codes) == len(set(codes)), name


def test_every_code_has_an_english_name():
    for codes in (SYNODAL78, STATEN67, PROT66):
        for code in codes:
            assert ENGLISH_NAMES.get(code), code


# ------------------------------------------------------------ Hebrew / Greek names

def test_hebrew_covers_the_whole_protestant_canon():
    """Both testaments: Tanakh names for the OT, Delitzsch names for the NT."""
    missing = [c for c in PROT66 if not HEBREW_NAMES.get(c)]
    assert missing == []


def test_greek_covers_the_whole_protestant_canon():
    missing = [c for c in PROT66 if not GREEK_NAMES.get(c)]
    assert missing == []


def test_hebrew_names_are_actually_hebrew():
    """Guards against a Latin abbreviation slipping back in — the bug that
    prompted these columns (wlc shipped 'Gen', not 'בראשית')."""
    for code, name in HEBREW_NAMES.items():
        assert _in_block(name, 0x0590, 0x05FF), (code, name)


def test_greek_names_are_actually_greek():
    for code, name in GREEK_NAMES.items():
        assert _in_block(name, 0x0370, 0x1FFF), (code, name)


def test_hebrew_names_are_unvocalized():
    """Deliberate choice: niqqud (U+0591-U+05C7) is omitted, so no partially
    or wrongly pointed name can creep in."""
    for code, name in HEBREW_NAMES.items():
        assert not any(0x0591 <= ord(c) <= 0x05C7 for c in name), (code, name)


def test_no_duplicate_hebrew_or_greek_names():
    """Two books sharing a name means one is mislabelled."""
    for label, names in (("hebrew", HEBREW_NAMES), ("greek", GREEK_NAMES)):
        values = list(names.values())
        assert len(values) == len(set(values)), (label, "duplicate name")


def test_septuagint_kingdoms_and_esdras_conventions():
    """The two LXX conventions most likely to be 'corrected' into being wrong."""
    assert GREEK_NAMES["1SA"] == "Βασιλειῶν Α΄"
    assert GREEK_NAMES["2KI"] == "Βασιλειῶν Δ΄"
    # 4 Ezra survives in Latin/Syriac, not Greek — a title here would be invented.
    assert "2ES" not in GREEK_NAMES


def test_only_sirach_has_a_hebrew_deuterocanonical_name():
    deutero = set(SYNODAL78) - set(PROT66)
    assert {c for c in deutero if HEBREW_NAMES.get(c)} == {"SIR"}


def test_deuterocanon_is_additive():
    """The wider canons must contain all 66 Protestant books — otherwise a
    join against them would drop rows rather than just leave blanks."""
    assert set(PROT66) <= set(STATEN67)
    assert set(PROT66) <= set(SYNODAL78)


def test_statenvertaling_inserts_baruch_between_malachi_and_matthew():
    assert STATEN67[38:41] == ["MAL", "BAR", "MAT"]


def test_synodal_esdras_mapping():
    """The source renumbers the Esdras books, so its labels are off by one:
    "Ездры"=Ezra, "1-я Ездры"=1 Esdras (Greek), "2-я Ездры"=2 Esdras/4 Ezra.
    Trusting the labels would put 1ES and 2ES one book too early."""
    # Ezra and Nehemiah stay adjacent and canonical, right after Manasseh.
    assert SYNODAL78[14:18] == ["MAN", "EZR", "NEH", "1ES"]
    # 1 Esdras sits between Nehemiah and Tobit; 4 Ezra trails the Maccabees.
    assert SYNODAL78[SYNODAL78.index("1ES") + 1] == "TOB"
    assert SYNODAL78[SYNODAL78.index("3MA") + 1] == "2ES"
    # 2ES is the last book before the NT opens.
    assert SYNODAL78[SYNODAL78.index("2ES") + 1] == "MAT"


def test_synodal_orders_catholic_epistles_before_pauline():
    """Orthodox NT order — this is exactly why Synodal cannot use PROT66."""
    nt = SYNODAL78[SYNODAL78.index("MAT"):]
    assert nt.index("JAS") < nt.index("ROM")
    assert nt.index("JUD") < nt.index("ROM")


def test_protestant_translations_share_one_order():
    """Seven translations rely on the same positional mapping."""
    shared = [b for b, codes in BOOK_ORDER.items() if codes is PROT66]
    assert set(shared) == {
        "nasb", "valera", "luther1545", "ls1910", "almeida", "riveduta", "arabicsv",
    }


# ------------------------------------------------------------ book extraction

def test_book_sequence_collapses_runs_in_order():
    refs = ["genesis 1:1", "genesis 1:2", "exodus 1:1", "exodus 2:1", "leviticus 1:1"]
    assert book_sequence(refs) == ["genesis", "exodus", "leviticus"]


# ------------------------------------------------------------ validation guards

def test_validate_books_accepts_a_matching_canon():
    assert validate_books("gnt", [f"B{i}" for i in range(27)]) is NT27


def test_validate_books_rejects_a_wrong_count():
    """A source gaining or losing a book must fail loudly, not mislabel every
    book after the insertion point."""
    with pytest.raises(SystemExit, match="found 26 books"):
        validate_books("gnt", [f"B{i}" for i in range(26)])


def test_validate_books_rejects_non_contiguous_books():
    """If verses aren't grouped by book, position means nothing."""
    with pytest.raises(SystemExit, match="more than one run"):
        validate_books("gnt", [f"B{i}" for i in range(26)] + ["B0"])


def test_validate_books_rejects_an_unknown_bible():
    with pytest.raises(SystemExit, match="no book order recorded"):
        validate_books("not-a-bible", ["Gen"])


def test_every_configured_bible_has_a_book_order():
    """bibles.toml and BOOK_ORDER must not drift apart."""
    import tomllib

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "bibles.toml"), "rb") as f:
        configured = {b["id"] for b in tomllib.load(f)["bibles"]}
    assert configured <= set(BOOK_ORDER), configured - set(BOOK_ORDER)
