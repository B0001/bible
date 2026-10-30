#!/usr/bin/env python3
"""Download and convert a Greek NT to verse-text -- reference format.

--edition byz (default): the Byzantine Majority Text (public domain), from
byztxt/byzantine-majority-text on GitHub — CSV files per book under
csv-unicode/ccat/no-variants/, columns: chapter, verse, text. -> data/gnt.txt

--edition sblgnt: the SBL Greek New Testament (CC BY 4.0, attribution in
bibles.toml), from LogosBible/SBLGNT on GitHub — data/sblgnt/text/<Book>.txt,
a title line then "Matt 1:1<TAB>text". Apparatus sigla are removed.
-> data/sblgnt.txt

Output: one line per verse: "<Greek text> -- <Book Chapter:Verse>"

Usage:
    python scripts/convert_gnt.py [--edition byz|sblgnt] [--out PATH]

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
SBLGNT_URL = "https://raw.githubusercontent.com/LogosBible/SBLGNT/master/data/sblgnt/text/"

# SBLGNT text-critical sigla (⸀ ⸁ ⸂ ⸃ ⸄ ⸅) point into its apparatus, which is not
# shipped, so they would only be noise inside words.
_SIGLA = str.maketrans("", "", "\u2e00\u2e01\u2e02\u2e03\u2e04\u2e05")

# (file_id, display_book_name) — file_id matches the byztxt .csv filename; the
# display name is also the SBLGNT file name and ref label ("Matt.txt", "Matt 1:1").
BOOKS = [
    ("MAT", "Matt"), ("MAR", "Mark"), ("LUK", "Luke"), ("JOH", "John"),
    ("ACT", "Acts"), ("ROM", "Rom"), ("1CO", "1Cor"), ("2CO", "2Cor"),
    ("GAL", "Gal"), ("EPH", "Eph"), ("PHP", "Phil"), ("COL", "Col"),
    ("1TH", "1Thess"), ("2TH", "2Thess"), ("1TI", "1Tim"), ("2TI", "2Tim"),
    ("TIT", "Titus"), ("PHM", "Phlm"), ("HEB", "Heb"), ("JAM", "Jas"),
    ("1PE", "1Pet"), ("2PE", "2Pet"), ("1JO", "1John"), ("2JO", "2John"),
    ("3JO", "3John"), ("JUD", "Jude"), ("REV", "Rev"),
]


def fetch_book(file_id, base=BASE_URL, ext=".csv"):
    url = f"{base}{file_id}{ext}"
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


def parse_sblgnt(text):
    """Parse an SBLGNT book file: skip the title line, keep 'Ref<TAB>text' lines."""
    verses = []
    for line in text.splitlines():
        ref, sep, greek = line.partition("\t")
        greek = " ".join(greek.translate(_SIGLA).split())
        if sep and greek:
            verses.append((ref.strip(), greek))
    return verses


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--edition", choices=["byz", "sblgnt"], default="byz")
    ap.add_argument("--out", help="default data/gnt.txt (byz) or data/sblgnt.txt")
    args = ap.parse_args()
    sblgnt = args.edition == "sblgnt"
    args.out = args.out or ("data/sblgnt.txt" if sblgnt else "data/gnt.txt")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    total = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for file_id, book_label in BOOKS:
            print(f"  {book_label}...", end=" ", flush=True)
            try:
                if sblgnt:
                    verses = parse_sblgnt(fetch_book(book_label, SBLGNT_URL, ".txt"))
                else:
                    verses = parse_book(fetch_book(file_id), book_label)
                f.writelines(f"{greek_text} -- {ref}\n" for ref, greek_text in verses)
                total += len(verses)
                print(f"{len(verses)} verses")
            # Deliberately broad: one malformed book upstream must not abort a
            # 27-book download. Whatever went wrong, report it and carry on.
            except Exception as e:  # noqa: BLE001
                print(f"skipped ({e})")
            time.sleep(0.05)

    print(f"\nWrote {total} verses -> {args.out}")


if __name__ == "__main__":
    main()
