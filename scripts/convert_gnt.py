#!/usr/bin/env python3
"""Download and convert the Byzantine Majority Text (Greek NT) to
verse-text -- reference format.

Source: byztxt/byzantine-majority-text on GitHub — CSV files per book under
csv-unicode/ccat/no-variants/, columns: chapter, verse, text.
Output: data/gnt.txt, one line per verse: "<Greek text> -- <Book Chapter:Verse>"

Usage:
    python scripts/convert_gnt.py [--out data/gnt.txt]

Requires only stdlib. Downloads ~2 MB.
"""
import argparse
import csv
import io
import os
import time
import urllib.request

BASE_URL = (
    "https://raw.githubusercontent.com/byztxt/byzantine-majority-text/"
    "master/csv-unicode/ccat/no-variants/"
)

# (file_id, display_book_name) — file_id matches the .csv filename in the repo
BOOKS = [
    ("MAT", "Matt"), ("MAR", "Mark"), ("LUK", "Luke"), ("JOH", "John"),
    ("ACT", "Acts"), ("ROM", "Rom"), ("1CO", "1Cor"), ("2CO", "2Cor"),
    ("GAL", "Gal"), ("EPH", "Eph"), ("PHP", "Phil"), ("COL", "Col"),
    ("1TH", "1Thess"), ("2TH", "2Thess"), ("1TI", "1Tim"), ("2TI", "2Tim"),
    ("TIT", "Titus"), ("PHM", "Phlm"), ("HEB", "Heb"), ("JAM", "Jas"),
    ("1PE", "1Pet"), ("2PE", "2Pet"), ("1JO", "1John"), ("2JO", "2John"),
    ("3JO", "3John"), ("JUD", "Jude"), ("REV", "Rev"),
]


def fetch_book(file_id):
    url = f"{BASE_URL}{file_id}.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "bible-reader/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def parse_book(csv_text, book_label):
    """Parse byztxt CSV: columns chapter, verse, text. Strip leading ¶."""
    verses = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        ch = row.get("chapter", "").strip()
        v = row.get("verse", "").strip()
        text = row.get("text", "").strip().lstrip("¶").strip()
        if ch and v and text:
            ref = f"{book_label} {ch}:{v}"
            verses.append((ref, text))
    return verses


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/gnt.txt")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    total = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for file_id, book_label in BOOKS:
            print(f"  {book_label}...", end=" ", flush=True)
            try:
                csv_text = fetch_book(file_id)
                verses = parse_book(csv_text, book_label)
                for ref, greek_text in verses:
                    f.write(f"{greek_text} -- {ref}\n")
                total += len(verses)
                print(f"{len(verses)} verses")
            except Exception as e:
                print(f"skipped ({e})")
            time.sleep(0.05)

    print(f"\nWrote {total} verses -> {args.out}")


if __name__ == "__main__":
    main()
