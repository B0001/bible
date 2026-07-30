#!/usr/bin/env python3
"""Export a HuggingFace-ready dataset of cross-language Bible book names and
vocabulary-difficulty metrics.

Two deliverables, deliberately in two formats:

  books.csv                       one row per USFM book code, one column per
                                  translation holding that book's native name.
                                  CSV because it is ~70 rows and its whole
                                  point is that people send corrections — a
                                  binary blob nobody can review in a PR would
                                  defeat that.

  data/<id>/train-00000.parquet   per-verse metrics. Parquet because it is the
                                  Hub's native storage (the dataset viewer
                                  converts everything to it anyway), it keeps
                                  int/float dtypes CSV would flatten to text,
                                  and it is UTF-8 by spec — no BOM or quoting
                                  games around Hebrew niqqud, Arabic, or
                                  polytonic Greek.

**No verse text is exported.** Only refs and derived metrics. That keeps
copyrighted translations (NASB) publishable as derived data and is why the
schema stops at counts and ranks.

Only vocabulary-independent columns ship. `comprehension_rate` and
`known_count` in out/*_graded.csv are scored against one specific vocab file —
they describe a reader, not a text, and mean nothing to anyone else. The
publishable metrics are corpus frequency rank and verse difficulty
(parser.corpus_ranks / parser.verse_difficulty with an empty known set).

Input is site/data/ (built by scripts/export_static.py), whose tokens already
come from parser.tokenize_and_stem — so metrics here match the app exactly.

Usage:
    python scripts/export_static.py          # build site/data first
    python scripts/export_dataset.py [--site-dir site] [--out-dir out/dataset]
"""
import argparse
import csv
import json
import os
import sys

import polars as pl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import corpus_ranks, verse_difficulty  # noqa: E402

# ---------------------------------------------------------------- USFM canon

# Protestant 39 + 27 in the order every 66-book translation here uses
# (verified: index 38 is Malachi and index 39 is Matthew in all of them).
OT39 = [
    "GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA",
    "1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST", "JOB", "PSA", "PRO",
    "ECC", "SNG", "ISA", "JER", "LAM", "EZK", "DAN", "HOS", "JOL", "AMO",
    "OBA", "JON", "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL",
]
NT27 = [
    "MAT", "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL", "EPH",
    "PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT", "PHM", "HEB", "JAS",
    "1PE", "2PE", "1JN", "2JN", "3JN", "JUD", "REV",
]
PROT66 = OT39 + NT27

ENGLISH_NAMES = {
    "GEN": "Genesis", "EXO": "Exodus", "LEV": "Leviticus", "NUM": "Numbers",
    "DEU": "Deuteronomy", "JOS": "Joshua", "JDG": "Judges", "RUT": "Ruth",
    "1SA": "1 Samuel", "2SA": "2 Samuel", "1KI": "1 Kings", "2KI": "2 Kings",
    "1CH": "1 Chronicles", "2CH": "2 Chronicles", "EZR": "Ezra",
    "NEH": "Nehemiah", "EST": "Esther", "JOB": "Job", "PSA": "Psalms",
    "PRO": "Proverbs", "ECC": "Ecclesiastes", "SNG": "Song of Songs",
    "ISA": "Isaiah", "JER": "Jeremiah", "LAM": "Lamentations",
    "EZK": "Ezekiel", "DAN": "Daniel", "HOS": "Hosea", "JOL": "Joel",
    "AMO": "Amos", "OBA": "Obadiah", "JON": "Jonah", "MIC": "Micah",
    "NAM": "Nahum", "HAB": "Habakkuk", "ZEP": "Zephaniah", "HAG": "Haggai",
    "ZEC": "Zechariah", "MAL": "Malachi",
    "MAT": "Matthew", "MRK": "Mark", "LUK": "Luke", "JHN": "John",
    "ACT": "Acts", "ROM": "Romans", "1CO": "1 Corinthians",
    "2CO": "2 Corinthians", "GAL": "Galatians", "EPH": "Ephesians",
    "PHP": "Philippians", "COL": "Colossians", "1TH": "1 Thessalonians",
    "2TH": "2 Thessalonians", "1TI": "1 Timothy", "2TI": "2 Timothy",
    "TIT": "Titus", "PHM": "Philemon", "HEB": "Hebrews", "JAS": "James",
    "1PE": "1 Peter", "2PE": "2 Peter", "1JN": "1 John", "2JN": "2 John",
    "3JN": "3 John", "JUD": "Jude", "REV": "Revelation",
    # Deuterocanon, present only in statenvertaling / synodal below.
    "TOB": "Tobit", "JDT": "Judith", "WIS": "Wisdom of Solomon",
    "SIR": "Sirach", "BAR": "Baruch", "LJE": "Letter of Jeremiah",
    "MAN": "Prayer of Manasseh", "1MA": "1 Maccabees", "2MA": "2 Maccabees",
    "3MA": "3 Maccabees", "1ES": "1 Esdras", "2ES": "2 Esdras",
}

# Statenvertaling interleaves Baruch between Malachi and Matthew.
STATEN67 = OT39 + ["BAR"] + NT27

# The Russian Synodal canon breaks positional mapping twice over: deuterocanon
# is interleaved through the OT, and the NT runs in Orthodox order (Catholic
# epistles before the Pauline ones). Hence an explicit sequence.
#
# The two Esdras entries are the one judgement call here. getbible labels them
# "1-я Ездры" (immediately after Nehemiah) and "2-я Ездры" (after Maccabees);
# read against the standard Synodal contents those are the books Russian
# Bibles number 2 Ездры and 3 Ездры, i.e. 1 Esdras (1ES) and 2 Esdras (2ES).
# Flagged in the README as the mapping most worth a second opinion.
SYNODAL78 = [
    "GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA",
    "1KI", "2KI", "1CH", "2CH", "MAN", "EZR", "NEH", "1ES", "TOB", "JDT",
    "EST", "JOB", "PSA", "PRO", "ECC", "SNG", "WIS", "SIR", "ISA", "JER",
    "LAM", "LJE", "BAR", "EZK", "DAN", "HOS", "JOL", "AMO", "OBA", "JON",
    "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL", "1MA", "2MA", "3MA",
    "2ES",
    "MAT", "MRK", "LUK", "JHN", "ACT", "JAS", "1PE", "2PE", "1JN", "2JN",
    "3JN", "JUD", "ROM", "1CO", "2CO", "GAL", "EPH", "PHP", "COL", "1TH",
    "2TH", "1TI", "2TI", "TIT", "PHM", "HEB", "REV",
]

# Book order per Bible id. Every entry is positional: the Nth distinct book in
# the text maps to the Nth code. validate_books() enforces the length match and
# that each book occupies one contiguous run, so a source whose order drifts
# fails loudly instead of silently mislabelling.
BOOK_ORDER = {
    "nasb": PROT66, "valera": PROT66, "luther1545": PROT66, "ls1910": PROT66,
    "almeida": PROT66, "riveduta": PROT66, "arabicsv": PROT66,
    "statenvertaling": STATEN67, "synodal": SYNODAL78,
    "wlc": OT39, "gnt": NT27, "modern-he-nt": NT27,
}


def split_ref(ref):
    """'1 Samuel 3:10' -> ('1 Samuel', 3, 10). Book names contain spaces and
    digits, so split from the right: verse after the last ':', chapter after
    the last space before it."""
    head, sep, verse = ref.rpartition(":")
    if not sep:  # no colon at all — the whole string is the book name
        return ref, None, None
    book, _, chapter = head.rpartition(" ")
    try:
        return book, int(chapter), int(verse)
    except ValueError:
        return book, None, None


def book_sequence(refs):
    """Distinct book names in first-appearance order."""
    books = []
    for ref in refs:
        book = split_ref(ref)[0]
        if not books or books[-1] != book:
            books.append(book)
    return books


def validate_books(bible_id, books):
    """Positional mapping is only safe if the order is what we recorded."""
    codes = BOOK_ORDER.get(bible_id)
    if codes is None:
        raise SystemExit(f"{bible_id}: no book order recorded; add one to BOOK_ORDER")
    if len(books) != len(codes):
        raise SystemExit(
            f"{bible_id}: found {len(books)} books, BOOK_ORDER has {len(codes)}. "
            f"The source text's canon changed — update BOOK_ORDER."
        )
    if len(books) != len(set(books)):
        raise SystemExit(
            f"{bible_id}: a book name appears in more than one run, so verses "
            f"are not grouped by book. Positional mapping is unsafe here."
        )
    return codes


def export_bible(entry, site_dir, out_dir):
    """Write one translation's Parquet. Returns {usfm: native book name}."""
    with open(os.path.join(site_dir, "data", f"{entry['id']}.json"), encoding="utf-8") as f:
        bible = json.load(f)

    bible_id = bible["id"]
    refs, tokens = bible["refs"], bible["tokens"]
    codes = validate_books(bible_id, book_sequence(refs))

    # Vocabulary-independent difficulty: rank every form by corpus frequency,
    # then ask how large a vocabulary makes each verse 95% known.
    ranks = corpus_ranks(tokens)
    names, book_col, chapters, verses, totals, difficulty = {}, [], [], [], [], []
    index = -1
    previous = None
    for ref, toks in zip(refs, tokens):
        book, chapter, verse_num = split_ref(ref)
        if book != previous:
            index += 1
            previous = book
            names[codes[index]] = book
        book_col.append(codes[index])
        chapters.append(chapter)
        verses.append(verse_num)
        totals.append(len(toks))
        difficulty.append(verse_difficulty(toks, ranks))

    table = pl.DataFrame(
        {
            "ref": refs,
            "book_usfm": book_col,
            "chapter": pl.Series(chapters, dtype=pl.Int32),
            "verse": pl.Series(verses, dtype=pl.Int32),
            "total_count": pl.Series(totals, dtype=pl.Int32),
            "difficulty_rank": pl.Series(difficulty, dtype=pl.Int32),
        }
    )
    dest = os.path.join(out_dir, "data", bible_id)
    os.makedirs(dest, exist_ok=True)
    table.write_parquet(os.path.join(dest, "train-00000.parquet"), compression="zstd")
    print(f"  {bible_id:16} {len(refs):6} verses  {len(codes):3} books  "
          f"{len(ranks):6} distinct forms")
    return names


def write_books_csv(out_dir, bible_ids, names_by_bible):
    """One row per USFM code that any translation carries, ordered by the
    widest canon so deuterocanonical books land in their real position."""
    order, seen = [], set()
    for codes in (SYNODAL78, STATEN67, PROT66):
        for code in codes:
            if code not in seen:
                seen.add(code)
                order.append(code)
    present = [c for c in order if any(c in n for n in names_by_bible.values())]

    path = os.path.join(out_dir, "books.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["usfm", "canon_order", "testament", "english"] + bible_ids)
        for i, code in enumerate(present, 1):
            testament = "OT" if code not in NT27 else "NT"
            writer.writerow(
                [code, i, testament, ENGLISH_NAMES.get(code, "")]
                + [names_by_bible[b].get(code, "") for b in bible_ids]
            )
    print(f"  books.csv        {len(present)} books x {len(bible_ids)} translations")


def write_readme(out_dir, entries):
    configs = "\n".join(
        f"  - config_name: {e['id']}\n"
        f"    data_files: data/{e['id']}/train-*.parquet"
        for e in entries
    )
    rows = "\n".join(
        f"| `{e['id']}` | {e['name']} | `{e['lang']}` | {e['verses']:,} |" for e in entries
    )
    readme = f"""---
license: cc-by-4.0
language:
{chr(10).join(sorted({'- ' + e['lang'] for e in entries}))}
task_categories:
  - text-classification
tags:
  - bible
  - readability
  - language-learning
  - vocabulary
configs:
{configs}
  - config_name: books
    data_files: books.csv
---

# Bible vocabulary-difficulty metrics, {len(entries)} translations

Per-verse reading-difficulty metrics plus a cross-language book-name table
keyed on USFM codes. Produced by [bible-reader](https://github.com/) — see
`scripts/export_dataset.py`.

## No verse text

This dataset contains **references and derived metrics only, never verse
text**. That is deliberate: it keeps translations under copyright (NASB)
publishable as derived data, and it keeps the download small. Fetch the texts
themselves from their own sources.

## Metrics

| column | meaning |
|---|---|
| `ref` | verse reference as it appears in the source text |
| `book_usfm` | USFM book code — the join key across translations |
| `chapter`, `verse` | parsed from `ref` |
| `total_count` | tokens in the verse after language-aware tokenization |
| `difficulty_rank` | **the useful one**, see below |

`difficulty_rank` is the size of the vocabulary you need to read the verse.
Rank every distinct word form by frequency *within its own translation*
(1 = most frequent), then report the smallest N such that knowing the top N
forms covers ≥95% of the verse. Sorting a translation by `difficulty_rank`
ascending gives a learning order. It is computed per translation, so ranks are
comparable within a translation but not across them.

Tokenization is language-aware: Snowball stemming for the 15 supported
European languages, niqqud/cantillation stripping for Hebrew, diacritic
stripping for Greek, harakat stripping for Arabic.

Note what is **not** here: comprehension rate and known-word counts. Those are
scored against one reader's vocabulary, so they describe a person rather than
a text. Everything published here is vocabulary-independent.

## Translations

| config | name | language | verses |
|---|---|---|---|
{rows}

## books.csv

One row per USFM book code, one column per translation with that book's name
in its own language. Canons differ — Statenvertaling carries Baruch, the
Russian Synodal text carries the full Orthodox deuterocanon and orders the NT
with the Catholic epistles before the Pauline ones — so blank cells mean the
book is absent from that translation, not missing data.

Two caveats worth your scepticism:

- `wlc` and `gnt` book names are the Latin abbreviations their source files
  use (`Gen`, `Matt`), **not** Hebrew or Greek book names. Corrections welcome.
- The Synodal `1ES`/`2ES` mapping is inferred from canon position rather than
  from the labels, which read "1-я Ездры"/"2-я Ездры". If you know the Synodal
  numbering well, please check it.

## Licence

CC BY 4.0. Derived metrics only; no scripture text is redistributed.
"""
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site-dir", default="site", help="directory export_static.py wrote")
    ap.add_argument("--out-dir", default="out/dataset", help="where to write the dataset")
    args = ap.parse_args(argv)

    manifest_path = os.path.join(args.site_dir, "data", "manifest.json")
    if not os.path.exists(manifest_path):
        raise SystemExit(f"{manifest_path} not found — run scripts/export_static.py first")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    os.makedirs(args.out_dir, exist_ok=True)
    entries, names_by_bible = [], {}
    print(f"Exporting {len(manifest['bibles'])} translations to {args.out_dir}/")
    for entry in manifest["bibles"]:
        names_by_bible[entry["id"]] = export_bible(entry, args.site_dir, args.out_dir)
        entries.append(entry)

    write_books_csv(args.out_dir, [e["id"] for e in entries], names_by_bible)
    write_readme(args.out_dir, entries)
    print(f"\nDone. Upload with:\n  huggingface-cli upload <user>/<dataset> {args.out_dir} .")


if __name__ == "__main__":
    main()
