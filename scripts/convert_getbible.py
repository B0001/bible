#!/usr/bin/env python3
"""Download any of ~117 Bible translations (63 languages) from getbible.net
and convert to verse-text -- reference format.

Usage:
    python scripts/convert_getbible.py --list                 # show translations
    python scripts/convert_getbible.py --translation valera   # Spanish RV
    python scripts/convert_getbible.py --translation luther1545 --out data/luther.txt

The API reports each translation's ISO language code; the script prints the
matching `parser.py --lang` value to use when grading. Requires only stdlib.
"""
import argparse
import json
import os
import urllib.request

API = "https://api.getbible.net/v2/"


def fetch_json(path):
    req = urllib.request.Request(API + path, headers={"User-Agent": "bible-reader/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def list_translations():
    data = fetch_json("translations.json")
    rows = sorted(
        (v.get("language", "?"), k, v.get("translation", ""))
        for k, v in data.items()
    )
    for language, abbrev, name in rows:
        print(f"  {abbrev:20s} {language:15s} {name}")
    print(f"\n{len(rows)} translations. Use --translation <abbrev>.")


def convert(translation, out_path):
    print(f"Fetching {translation} from getbible.net...")
    data = fetch_json(f"{translation}.json")
    lang = data.get("lang", "?")
    direction = data.get("direction", "LTR")

    verses = []
    for book in data["books"]:
        book_name = book["name"]
        for chapter in book["chapters"]:
            for v in chapter["verses"]:
                text = " ".join(v["text"].split())
                if text:
                    verses.append((f"{book_name} {v['chapter']}:{v['verse']}", text))

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for ref, text in verses:
            f.write(f"{text} -- {ref}\n")

    print(f"Wrote {len(verses)} verses -> {out_path}")
    print(f"Language: {data.get('language')} ({lang}, {direction}). "
          f"Grade with: parser.py --lang {lang}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="list available translations")
    ap.add_argument("--translation", help="translation abbreviation (see --list)")
    ap.add_argument("--out", help="output path (default data/<translation>.txt)")
    args = ap.parse_args()

    if args.list:
        list_translations()
        return
    if not args.translation:
        ap.error("--translation is required (or use --list)")
    convert(args.translation, args.out or f"data/{args.translation}.txt")


if __name__ == "__main__":
    main()
