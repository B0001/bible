"""Hebrew text utilities: normalization and tokenization.

Rules (see SPEC §5.2):
- Cantillation is always stripped for alignment matching.
- Niqqud is stripped by default (keep with keep_niqqud=True).
- Maqqef-joined compounds stay as ONE token.
- Sof pasuq, paseq, and surrounding punctuation are removed.
- Every token keeps its fully pointed `display` form for the UI.
"""
from __future__ import annotations

import re

# Unicode ranges
CANTILLATION_RE = re.compile(r"[\u0591-\u05AF]")
NIQQUD_RE = re.compile(r"[\u05B0-\u05BD\u05BF\u05C1\u05C2\u05C7]")
HEBREW_LETTER_RE = re.compile(r"[\u05D0-\u05EA]")

MAQAF = "\u05BE"       # ־  joins compounds; kept inside tokens
SOF_PASUQ = "\u05C3"   # ׃  verse-end punctuation; removed
PASEQ = "\u05C0"       # ׀  separator; treated as whitespace

_STRIP_CHARS = "()[]{}<>.,;:!?\"'\u2018\u2019\u201C\u201D\u05C3\u05C0*"


def strip_cantillation(text: str) -> str:
    return CANTILLATION_RE.sub("", text)


def strip_niqqud(text: str) -> str:
    return NIQQUD_RE.sub("", text)


def normalize(token: str, keep_niqqud: bool = False) -> str:
    """Normalize a display token for forced alignment."""
    out = strip_cantillation(token)
    if not keep_niqqud:
        out = strip_niqqud(out)
    return out


def tokenize_line(line: str, verse: int) -> list[dict]:
    """Split one verse line into display tokens.

    Returns dicts: {"display": str, "verse": int}. Tokens with no Hebrew
    letters (stray digits, Latin annotations) are dropped.
    """
    line = line.replace(PASEQ, " ").replace(SOF_PASUQ, " ")
    tokens: list[dict] = []
    for raw in line.split():
        display = raw.strip(_STRIP_CHARS)
        if not display or not HEBREW_LETTER_RE.search(display):
            continue
        tokens.append({"display": display, "verse": verse})
    return tokens


def tokenize_chapter(text: str) -> list[dict]:
    """Tokenize a whole chapter (one verse per line, per SPEC §4)."""
    tokens: list[dict] = []
    verse = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        verse += 1
        tokens.extend(tokenize_line(line, verse))
    return tokens
