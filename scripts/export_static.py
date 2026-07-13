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

With --audio (P10.3), bibles whose bibles.toml entry names an existing
`audio_manifest` also get their per-chapter alignment sidecars copied to
site/data/audio/<id>/ plus an index.json, and an "audio" key in the site
manifest. Only timing JSONs are copied — never audio bytes (FCBH licensing,
PHASE10_DESIGN.md §2); the reader resolves audio files at runtime via the
--audio-base relative path, which only exists locally. The CI deploy runs
without --audio, so the public site carries no audio feature at all.

Usage:
    python scripts/export_static.py [--site-dir site] [--audio]
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


def export_audio(entry, site_data_dir, audio_base):
    """Copy one bible's alignment sidecar JSONs (never audio bytes) into
    site/data/audio/<id>/ and write an index.json mapping "Book Chapter" to
    sidecar file. Returns the index path relative to the site dir, or None
    when the entry has no (readable) audio manifest."""
    manifest_path = entry.get("audio_manifest")
    if not manifest_path or not os.path.exists(manifest_path):
        return None
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    out_dir = os.path.join(site_data_dir, "audio", entry["id"])
    os.makedirs(out_dir, exist_ok=True)
    chapters = {}
    for ch in manifest.get("chapters", []):
        try:
            with open(ch["sidecar"], encoding="utf-8") as f:
                sidecar = json.load(f)
        except (OSError, json.JSONDecodeError):
            print(f"  {entry['id']}: sidecar {ch['sidecar']!r} unreadable — skipping")
            continue
        name = os.path.basename(ch["sidecar"])
        # Re-serialize the parsed JSON rather than copying the file: guarantees
        # nothing but timing data can ever land under site/ (licensing, §2).
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump(sidecar, f, ensure_ascii=False, separators=(",", ":"))
        chapters[f"{sidecar['book']} {sidecar['chapter']}"] = name
    if not chapters:
        return None

    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"audio_base": audio_base, "chapters": chapters},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"  {entry['id']}: audio timings for {len(chapters)} chapter(s) "
          f"(sidecars only, no audio bytes)")
    return f"data/audio/{entry['id']}/index.json"


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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site-dir", default="site")
    ap.add_argument("--audio", action="store_true",
                    help="include alignment timings for local audio mode (P10.3)")
    ap.add_argument("--audio-base", default="../data/audio/serve",
                    help="audio file base path relative to the site root")
    args = ap.parse_args()

    with open("bibles.toml", "rb") as f:
        config = tomllib.load(f)

    site_data_dir = os.path.join(args.site_dir, "data")
    os.makedirs(site_data_dir, exist_ok=True)

    manifest_bibles = []
    for entry in config.get("bibles", []):
        m = export_bible(entry, site_data_dir)
        if m:
            if args.audio:
                audio_index = export_audio(entry, site_data_dir, args.audio_base)
                if audio_index:
                    m["audio"] = audio_index
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
    }
    manifest_path = os.path.join(site_data_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Manifest: {len(manifest_bibles)} bible(s) -> {manifest_path}")


if __name__ == "__main__":
    main()
