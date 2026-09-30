#!/usr/bin/env python3
"""Download a public-domain English Bible from scrollmapper/bible_databases
and convert it to verse-text -- reference format, with refs that match the
tushortz texts (``data/kjv.txt``, ``data/nasb.txt``) verse for verse.

Usage:
    python scripts/convert_scrollmapper.py --translation YLT   # -> data/ylt.txt
    python scripts/convert_scrollmapper.py --translation BBE --out data/bbe.txt

Translations verified complete (31,102 verses, 66 books): ASV, BBE, Darby,
KJV, Webster, YLT. Tyndale is mostly empty and DRC carries the Catholic canon,
so both are refused rather than silently misaligned.

Why this exists: ``rhyme.py stitch/joint`` align translations by ref string,
so every text must spell refs identically. Book names are mapped by
*position* onto the tushortz spelling ("psalms", "song of solomon",
"revelation"), guarded by book-count, book-order and verse-count checks.
Verses empty in the source are kept as empty-text lines rather than dropped
(unlike convert_getbible.py), so every converted file carries the same refs;
rhyme.py never picks a blank rendering. Requires only stdlib.
"""
import argparse
import csv
import io
import os
import re
import urllib.request

URL = "https://raw.githubusercontent.com/scrollmapper/bible_databases/master/formats/csv/{}.csv"

# The 66-book Protestant order, spelled as the tushortz texts spell refs.
BOOKS = [
    "genesis", "exodus", "leviticus", "numbers", "deuteronomy", "joshua", "judges",
    "ruth", "1 samuel", "2 samuel", "1 kings", "2 kings", "1 chronicles",
    "2 chronicles", "ezra", "nehemiah", "esther", "job", "psalms", "proverbs",
    "ecclesiastes", "song of solomon", "isaiah", "jeremiah", "lamentations",
    "ezekiel", "daniel", "hosea", "joel", "amos", "obadiah", "jonah", "micah",
    "nahum", "habakkuk", "zephaniah", "haggai", "zechariah", "malachi", "matthew",
    "mark", "luke", "john", "acts", "romans", "1 corinthians", "2 corinthians",
    "galatians", "ephesians", "philippians", "colossians", "1 thessalonians",
    "2 thessalonians", "1 timothy", "2 timothy", "titus", "philemon", "hebrews",
    "james", "1 peter", "2 peter", "1 john", "2 john", "3 john", "jude", "revelation",
]
EXPECTED_VERSES = 31102
MAX_EMPTY = 100  # ASV/BBE have 16 empty verses; Tyndale has 23,214


def convert_rows(rows):
    """``Book,Chapter,Verse,Text`` dicts -> ``[(ref, text)]`` in tushortz spelling.

    ``--`` dashes become em dashes (see below). Raises ValueError when the
    source is not a complete 66-book text: wrong
    book count (a different canon would shift every later book's name), a
    first/last book that is not Genesis/Revelation, a verse total other than
    31,102, or more than MAX_EMPTY empty verses.
    """
    order = list(dict.fromkeys(r["Book"] for r in rows))
    if len(order) != len(BOOKS):
        raise ValueError(f"expected {len(BOOKS)} books, got {len(order)}")
    if not order[0].lower().startswith("genesis") or \
            not order[-1].lower().startswith("revelation"):
        raise ValueError(f"book order starts {order[0]!r}, ends {order[-1]!r}")
    if len(rows) != EXPECTED_VERSES:
        raise ValueError(f"expected {EXPECTED_VERSES} verses, got {len(rows)}")
    empty = sum(1 for r in rows if not r["Text"].strip())
    if empty > MAX_EMPTY:
        raise ValueError(f"{empty} empty verses: not a complete translation")
    name = dict(zip(order, BOOKS, strict=True))
    out = []
    for r in rows:
        # YLT (5,456 verses) and BBE (41) write a dash as "--"; 135 YLT verses
        # end in " --", which fuses with the " -- " separator every loader
        # splits on and corrupts the ref. An em dash is what "--" means there.
        text = " ".join(r["Text"].replace("--", "\u2014").split())
        # scrollmapper's Darby glues every "God" to the word before it
        # ("the Spirit ofGod", 3,439 times, "theGodhead" included); the other
        # five texts have no lowercase-then-"God" at all, so this is a no-op there.
        text = re.sub(r"(?<=[a-z])(?=God)", " ", text)
        if " -- " in f" {text} ":
            raise ValueError(f"separator inside verse text: {text!r}")
        out.append((f"{name[r['Book']]} {int(r['Chapter'])}:{int(r['Verse'])}", text))
    return out


def fetch_rows(translation):
    req = urllib.request.Request(URL.format(translation),
                                 headers={"User-Agent": "bible-reader/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return list(csv.DictReader(io.TextIOWrapper(r, encoding="utf-8")))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--translation", required=True, help="e.g. ASV, BBE, Darby, KJV, Webster, YLT")
    ap.add_argument("--out", help="output path (default data/<translation>.txt, lowercased)")
    args = ap.parse_args()
    out = args.out or f"data/{args.translation.lower()}.txt"
    verses = convert_rows(fetch_rows(args.translation))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.writelines(f"{text} -- {ref}\n" for ref, text in verses)
    print(f"Wrote {len(verses)} verses -> {out}. Grade with: parser.py --lang en")


if __name__ == "__main__":
    main()
