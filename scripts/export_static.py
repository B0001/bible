#!/usr/bin/env python3
"""Export pre-tokenized per-Bible JSON for the static reader (site/).

For each Bible in bibles.toml whose `source` text exists, writes
site/data/<id>.json:

    {"id", "name", "lang", "refs": [...], "verses": [...], "tokens": [[...], ...]}

Tokens come from parser.tokenize_and_stem, so browser-side scoring against a
user vocab is definitionally identical to the Python pipeline. Also writes
site/data/manifest.json listing the available Bibles plus the NLTK English
stopword list (the browser's Snowball stemmer must skip the same stopwords
that SnowballStemmer(ignore_stopwords=True) skips, or stems won't match).

Usage:
    python scripts/export_static.py [--site-dir site]
"""
import argparse
import json
import os
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import load_bible, tokenize_and_stem  # noqa: E402


def export_bible(entry, site_data_dir):
    """Write one Bible's JSON; returns its manifest entry or None if source is absent."""
    source = entry.get("source")
    if not source or not os.path.exists(source):
        print(f"  {entry['id']}: source {source!r} not found — skipping")
        return None

    df = load_bible(source)
    verses = df["verse"].to_list()
    refs = df["ref"].to_list()
    lang = entry.get("lang", "en")
    tokens = [tokenize_and_stem(v, lang) for v in verses]

    out_path = os.path.join(site_data_dir, f"{entry['id']}.json")
    payload = {
        "id": entry["id"],
        "name": entry["name"],
        "lang": lang,
        "refs": refs,
        "verses": verses,
        "tokens": tokens,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(out_path) / 1e6
    print(f"  {entry['id']}: {len(refs)} verses -> {out_path} ({size_mb:.1f} MB)")
    return {
        "id": entry["id"],
        "name": entry["name"],
        "lang": lang,
        "verses": len(refs),
        "file": f"data/{entry['id']}.json",
    }


def stopwords_for_langs(langs):
    """Per-language NLTK stopword lists (the sets SnowballStemmer(ignore_stopwords=True)
    leaves unstemmed) for every exported language that has one. The browser stemmer
    must skip the same words or its stems won't match the exported verse tokens."""
    from nltk.corpus import stopwords

    from parser import SNOWBALL_LANGS

    out = {}
    for lang in sorted(set(langs)):
        name = SNOWBALL_LANGS.get(lang)
        if name:
            try:
                out[lang] = sorted(stopwords.words(name))
            except OSError:  # no list for this language
                out[lang] = []
    return out


def english_stopwords():
    """Backward-compat helper (see stopwords_for_langs)."""
    return stopwords_for_langs(["en"])["en"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site-dir", default="site")
    args = ap.parse_args()

    with open("bibles.toml", "rb") as f:
        config = tomllib.load(f)

    site_data_dir = os.path.join(args.site_dir, "data")
    os.makedirs(site_data_dir, exist_ok=True)

    manifest_bibles = []
    for entry in config.get("bibles", []):
        m = export_bible(entry, site_data_dir)
        if m:
            manifest_bibles.append(m)

    from parser import SNOWBALL_LANGS

    langs = [b["lang"] for b in manifest_bibles]
    # stemmers: lang -> vendored JS module (null means no browser stemmer;
    # he/el rely on mark-stripping alone, matching the Python pipeline)
    manifest = {
        "bibles": manifest_bibles,
        "stopwords": stopwords_for_langs(langs),
        "stemmers": {
            lang: (f"vendor/{SNOWBALL_LANGS[lang]}-stemmer.js" if lang in SNOWBALL_LANGS else None)
            for lang in sorted(set(langs))
        },
        "rtl": ["he", "ar"],
        "en_stopwords": english_stopwords(),
    }
    manifest_path = os.path.join(site_data_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Manifest: {len(manifest_bibles)} bible(s) -> {manifest_path}")


if __name__ == "__main__":
    main()
