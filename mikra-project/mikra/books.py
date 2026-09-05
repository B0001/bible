"""Canonical book table for the Hebrew Bible (39 books, OSIS-style abbrevs)."""
from __future__ import annotations

import re

# (abbrev, canonical order). Keys are lowercased with non-alphanumerics removed,
# so "Song_of_Songs", "SongofSongs", "1Samuel", "1 Samuel" all resolve.
_BOOKS: list[tuple[str, str, list[str]]] = [
    ("Gen", "Genesis", []),
    ("Exod", "Exodus", []),
    ("Lev", "Leviticus", []),
    ("Num", "Numbers", []),
    ("Deut", "Deuteronomy", []),
    ("Josh", "Joshua", []),
    ("Judg", "Judges", []),
    ("Ruth", "Ruth", []),
    ("1Sam", "1Samuel", ["1stSamuel", "FirstSamuel"]),
    ("2Sam", "2Samuel", ["2ndSamuel", "SecondSamuel"]),
    ("1Kgs", "1Kings", ["1stKings", "FirstKings"]),
    ("2Kgs", "2Kings", ["2ndKings", "SecondKings"]),
    ("1Chr", "1Chronicles", ["1stChronicles", "FirstChronicles"]),
    ("2Chr", "2Chronicles", ["2ndChronicles", "SecondChronicles"]),
    ("Ezra", "Ezra", []),
    ("Neh", "Nehemiah", []),
    ("Esth", "Esther", []),
    ("Job", "Job", []),
    ("Ps", "Psalms", ["Psalm"]),
    ("Prov", "Proverbs", []),
    ("Eccl", "Ecclesiastes", ["Qoheleth"]),
    ("Song", "SongofSongs", ["SongofSolomon", "Canticles"]),
    ("Isa", "Isaiah", []),
    ("Jer", "Jeremiah", []),
    ("Lam", "Lamentations", []),
    ("Ezek", "Ezekiel", []),
    ("Dan", "Daniel", []),
    ("Hos", "Hosea", []),
    ("Joel", "Joel", []),
    ("Amos", "Amos", []),
    ("Obad", "Obadiah", []),
    ("Jonah", "Jonah", []),
    ("Mic", "Micah", []),
    ("Nah", "Nahum", []),
    ("Hab", "Habakkuk", []),
    ("Zeph", "Zephaniah", []),
    ("Hag", "Haggai", []),
    ("Zech", "Zechariah", []),
    ("Mal", "Malachi", []),
]

BOOK_ORDER: list[str] = [abbrev for abbrev, _, _ in _BOOKS]

_NORM_RE = re.compile(r"[^a-z0-9]")


def _norm(name: str) -> str:
    return _NORM_RE.sub("", name.lower())


NAME_TO_ABBREV: dict[str, str] = {}
for abbrev, canonical, variants in _BOOKS:
    for name in [canonical, abbrev, *variants]:
        NAME_TO_ABBREV[_norm(name)] = abbrev


def abbrev_for(name: str) -> str | None:
    return NAME_TO_ABBREV.get(_norm(name))


def chapter_id(abbrev: str, chapter: int) -> str:
    """Gen_01 ... but Psalms are 3-digit (Ps_001..Ps_150) so they sort."""
    width = 3 if abbrev == "Ps" else 2
    return f"{abbrev}_{chapter:0{width}d}"
