#!/usr/bin/env python3
"""Download and convert the Westminster Leningrad Codex (Biblical Hebrew OT) to
verse-text -- reference format.

Source: openscriptures/morphhb on GitHub (one OSIS XML file per book).
Output: data/wlc.txt, one line per verse: "<Hebrew text> -- <Book Chapter:Verse>"

Usage:
    python scripts/convert_wlc.py [--out data/wlc.txt]

Requires only stdlib (urllib, xml.etree). Downloads ~4 MB of XML.
"""
import argparse
import os
import time
import urllib.request
import xml.etree.ElementTree as ET

BASE_URL = "https://raw.githubusercontent.com/openscriptures/morphhb/master/wlc/"

BOOKS = [
    ("Gen", "Gen"), ("Exod", "Exod"), ("Lev", "Lev"), ("Num", "Num"), ("Deut", "Deut"),
    ("Josh", "Josh"), ("Judg", "Judg"), ("Ruth", "Ruth"), ("1Sam", "1Sam"), ("2Sam", "2Sam"),
    ("1Kgs", "1Kgs"), ("2Kgs", "2Kgs"), ("1Chr", "1Chr"), ("2Chr", "2Chr"), ("Ezra", "Ezra"),
    ("Neh", "Neh"), ("Esth", "Esth"), ("Job", "Job"), ("Ps", "Ps"), ("Prov", "Prov"),
    ("Eccl", "Eccl"), ("Song", "Song"), ("Isa", "Isa"), ("Jer", "Jer"), ("Lam", "Lam"),
    ("Ezek", "Ezek"), ("Dan", "Dan"), ("Hos", "Hos"), ("Joel", "Joel"), ("Amos", "Amos"),
    ("Obad", "Obad"), ("Jonah", "Jonah"), ("Mic", "Mic"), ("Nah", "Nah"), ("Hab", "Hab"),
    ("Zeph", "Zeph"), ("Hag", "Hag"), ("Zech", "Zech"), ("Mal", "Mal"),
]

OSIS_NS = "http://www.bibletechnologies.net/2003/OSIS/namespace"


def fetch_book(book_id):
    url = f"{BASE_URL}{book_id}.xml"
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8")


def parse_book(xml_text, book_label):
    """Extract (ref, text) pairs from one WLC OSIS book XML."""
    root = ET.fromstring(xml_text)
    verses = []
    for verse_el in root.iter(f"{{{OSIS_NS}}}verse"):
        osisID = verse_el.get("osisID", "")
        # osisID is like "Gen.1.1" — convert to "Gen 1:1"
        parts = osisID.split(".")
        if len(parts) != 3:
            continue
        ref = f"{parts[0]} {parts[1]}:{parts[2]}"
        # Collect all <w> element text (word forms, may include niqqud).
        # morphhb marks morpheme boundaries with "/" (e.g. בְּ/רֵאשִׁ֖ית);
        # strip them so the output reads as natural Hebrew words.
        words = []
        for w in verse_el.iter(f"{{{OSIS_NS}}}w"):
            if w.text:
                words.append(w.text.strip().replace("/", ""))
        if words:
            verses.append((ref, " ".join(words)))
    return verses


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/wlc.txt")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    total = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for book_id, book_label in BOOKS:
            print(f"  {book_id}...", end=" ", flush=True)
            xml_text = fetch_book(book_id)
            verses = parse_book(xml_text, book_label)
            for ref, text in verses:
                f.write(f"{text} -- {ref}\n")
            total += len(verses)
            print(f"{len(verses)} verses")
            time.sleep(0.05)  # polite crawl rate

    print(f"\nWrote {total} verses -> {args.out}")


if __name__ == "__main__":
    main()
