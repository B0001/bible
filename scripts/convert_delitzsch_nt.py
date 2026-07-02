#!/usr/bin/env python3
"""Download and convert the Delitzsch Hebrew NT to verse-text -- reference format.

Source: HebrewNewTestament/HebDelitzsch on GitHub — OSIS XML with inline verse text.
Verse elements look like: <verse osisID="Matt.1.1">Hebrew text here</verse>

Output: data/modern_he_nt.txt, one line per verse:
    "<Hebrew text> -- <Book Chapter:Verse>"

Usage:
    python scripts/convert_delitzsch_nt.py [--out data/modern_he_nt.txt]

Requires only stdlib. Downloads ~600 KB.
"""
import argparse
import os
import re
import urllib.request
import xml.etree.ElementTree as ET

OSIS_URL = (
    "https://raw.githubusercontent.com/HebrewNewTestament/HebDelitzsch/"
    "master/base.osis"
)

OSIS_NS = "http://www.bibletechnologies.net/2003/OSIS/namespace"

# osisID book prefix → display name
BOOK_NAMES = {
    "Matt": "Matt", "Mark": "Mark", "Luke": "Luke", "John": "John",
    "Acts": "Acts", "Rom": "Rom", "1Cor": "1Cor", "2Cor": "2Cor",
    "Gal": "Gal", "Eph": "Eph", "Phil": "Phil", "Col": "Col",
    "1Thess": "1Thess", "2Thess": "2Thess", "1Tim": "1Tim", "2Tim": "2Tim",
    "Titus": "Titus", "Phlm": "Phlm", "Heb": "Heb", "Jas": "Jas",
    "1Pet": "1Pet", "2Pet": "2Pet", "1John": "1John", "2John": "2John",
    "3John": "3John", "Jude": "Jude", "Rev": "Rev",
}

_STRIP_TAGS = re.compile(r"<[^>]+>")


def fetch_osis():
    req = urllib.request.Request(OSIS_URL, headers={"User-Agent": "bible-reader/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def parse_osis(xml_text):
    """Extract (ref, Hebrew-text) pairs from Delitzsch OSIS XML.

    Verse elements carry the Hebrew text directly as element text, unlike WLC
    which uses child <w> elements.
    """
    root = ET.fromstring(xml_text)
    verses = []
    for v_el in root.iter(f"{{{OSIS_NS}}}verse"):
        osisID = v_el.get("osisID", "")
        parts = osisID.split(".")
        if len(parts) != 3:
            continue
        book = BOOK_NAMES.get(parts[0], parts[0])
        ref = f"{book} {parts[1]}:{parts[2]}"

        # Collect all text within the verse (text + tail of child elements)
        chunks = []
        if v_el.text:
            chunks.append(v_el.text)
        for child in v_el:
            if child.text:
                chunks.append(child.text)
            if child.tail:
                chunks.append(child.tail)
        text = " ".join(" ".join(chunks).split())
        if text:
            verses.append((ref, text))

    return verses


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/modern_he_nt.txt")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    print("Fetching Delitzsch Hebrew NT...")
    xml_text = fetch_osis()
    verses = parse_osis(xml_text)

    if not verses:
        print("No verses extracted. Check the source format.")
        return

    with open(args.out, "w", encoding="utf-8") as f:
        for ref, text in verses:
            f.write(f"{text} -- {ref}\n")

    print(f"Wrote {len(verses)} verses -> {args.out}")


if __name__ == "__main__":
    main()
