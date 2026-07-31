#!/usr/bin/env python3
"""Ingest the Hebrew OT chapter-audio corpus (PHASE10_DESIGN.md P10.0).

Takes a local copy of the Drive folder of FCBH-derived chapter WAVs (named
like ``1_Chronicles_01.wav``, Masoretic chapter numbering) and:

1. inventories it against the 929-chapter Masoretic OT table, printing
   per-book coverage and a compact gap list;
2. stages files as ``data/audio/raw/<osis>_<NNN>.wav`` (hardlink when the
   source is on the same volume, copy otherwise);
3. transcodes to ``data/audio/serve/<osis>_<NNN>.opus`` (mono Opus 40 kbps
   via ffmpeg) — skip-if-exists, so re-runs resume.

Usage:
    python scripts/ingest_audio.py --src ~/Downloads/hebrew_ot_audio
    python scripts/ingest_audio.py --src ... --dry-run   # inventory only

The audio is not redistributable (PHASE10_DESIGN.md §2); everything lands
under data/, which is gitignored.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

# (drive folder name, ref book id as used in data/wlc.txt, Masoretic chapter
# count). The single place the name mapping and chapter counts live (D6).
# Joel has 4 chapters and Malachi 3 in Masoretic numbering; counts sum to 929.
BOOKS = [
    ("Genesis", "Gen", 50), ("Exodus", "Exod", 40), ("Leviticus", "Lev", 27),
    ("Numbers", "Num", 36), ("Deuteronomy", "Deut", 34), ("Joshua", "Josh", 24),
    ("Judges", "Judg", 21), ("Ruth", "Ruth", 4), ("1_Samuel", "1Sam", 31),
    ("2_Samuel", "2Sam", 24), ("1_Kings", "1Kgs", 22), ("2_Kings", "2Kgs", 25),
    ("1_Chronicles", "1Chr", 29), ("2_Chronicles", "2Chr", 36), ("Ezra", "Ezra", 10),
    ("Nehemiah", "Neh", 13), ("Esther", "Esth", 10), ("Job", "Job", 42),
    ("Psalms", "Ps", 150), ("Proverbs", "Prov", 31), ("Ecclesiastes", "Eccl", 12),
    ("Song_of_Songs", "Song", 8), ("Isaiah", "Isa", 66), ("Jeremiah", "Jer", 52),
    ("Lamentations", "Lam", 5), ("Ezekiel", "Ezek", 48), ("Daniel", "Dan", 12),
    ("Hosea", "Hos", 14), ("Joel", "Joel", 4), ("Amos", "Amos", 9),
    ("Obadiah", "Obad", 1), ("Jonah", "Jonah", 4), ("Micah", "Mic", 7),
    ("Nahum", "Nah", 3), ("Habakkuk", "Hab", 3), ("Zephaniah", "Zeph", 3),
    ("Haggai", "Hag", 2), ("Zechariah", "Zech", 14), ("Malachi", "Mal", 3),
]
# Alternate drive spellings (the Song files are absent from the current upload,
# so their exact name is unverified — accept the common variants).
ALIASES = {"Song_of_Solomon": "Song", "Canticles": "Song"}

DRIVE_TO_OSIS = {name.lower(): osis for name, osis, _ in BOOKS}
DRIVE_TO_OSIS.update({name.lower(): osis for name, osis in ALIASES.items()})
CHAPTERS = {osis: n for _, osis, n in BOOKS}

FILE_RE = re.compile(r"^(?P<book>.+?)_(?P<ch>\d+)\.wav$", re.IGNORECASE)


def parse_filename(name):
    """``1_Chronicles_01.wav`` -> ("1Chr", 1); None if unrecognized."""
    m = FILE_RE.match(name)
    if not m:
        return None
    osis = DRIVE_TO_OSIS.get(m.group("book").lower())
    if osis is None:
        return None
    return osis, int(m.group("ch"))


def compact(nums):
    """[7, 9, 10, 17, 18, 19] -> "7, 9-10, 17-19"."""
    nums, runs = sorted(nums), []
    for n in nums:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def inventory(names):
    """File names -> ({osis: {chapters}}, [unrecognized names])."""
    present, unrecognized = {}, []
    for name in names:
        parsed = parse_filename(name)
        if parsed is None:
            unrecognized.append(name)
        else:
            osis, ch = parsed
            present.setdefault(osis, set()).add(ch)
    return present, unrecognized


def report(present, unrecognized):
    """Print per-book coverage; return total missing-chapter count."""
    total_have = total_missing = 0
    for _, osis, n in BOOKS:
        have = {c for c in present.get(osis, set()) if 1 <= c <= n}
        extra = present.get(osis, set()) - have
        missing = set(range(1, n + 1)) - have
        total_have += len(have)
        total_missing += len(missing)
        line = f"  {osis:5} {len(have):3}/{n}"
        if missing and len(missing) < n:
            line += f"  missing {compact(missing)}"
        elif missing:
            line += "  ALL MISSING"
        if extra:
            line += f"  UNEXPECTED chapters {compact(extra)}"
        print(line)
    print(f"\n{total_have}/929 chapters present, {total_missing} missing")
    if unrecognized:
        print(f"{len(unrecognized)} unrecognized files: {', '.join(sorted(unrecognized)[:10])}"
              + ("…" if len(unrecognized) > 10 else ""))
    return total_missing


def stage_raw(src, dst):
    """Hardlink (same volume) or copy src -> dst; skip if dst exists."""
    if os.path.exists(dst):
        return False
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return True


def transcode(src, dst):
    """WAV -> mono 40 kbps Opus; skip if dst exists. Writes via a temp name so
    an interrupted run never leaves a truncated file behind."""
    if os.path.exists(dst):
        return False
    tmp = dst + ".part.opus"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src,
         "-ac", "1", "-c:a", "libopus", "-b:a", "40k", tmp],
        check=True,
    )
    os.replace(tmp, dst)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, help="local copy of the Drive audio folder")
    ap.add_argument("--raw", default="data/audio/raw")
    ap.add_argument("--serve", default="data/audio/serve")
    ap.add_argument("--dry-run", action="store_true", help="inventory only; move no bytes")
    args = ap.parse_args()

    names = sorted(n for n in os.listdir(args.src) if not n.startswith("."))
    present, unrecognized = inventory(names)
    report(present, unrecognized)
    if args.dry_run:
        return

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found — install it first (brew install ffmpeg)")
    os.makedirs(args.raw, exist_ok=True)
    os.makedirs(args.serve, exist_ok=True)

    staged = coded = 0
    for name in names:
        parsed = parse_filename(name)
        if parsed is None:
            continue
        osis, ch = parsed
        stem = f"{osis}_{ch:03}"
        src = os.path.join(args.src, name)
        staged += stage_raw(src, os.path.join(args.raw, f"{stem}.wav"))
        coded += transcode(src, os.path.join(args.serve, f"{stem}.opus"))
    print(f"staged {staged} raw, transcoded {coded} (existing files skipped)")


if __name__ == "__main__":
    main()
