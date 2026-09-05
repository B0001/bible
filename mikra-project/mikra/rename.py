"""Stage 1: parse vendor filenames in raw/ and copy into audio/.

Vendor pattern (underscore runs vary):
    B01___01_Genesis_____HBRHMTN1DA.mp3
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import books, manifest

VENDOR_RE = re.compile(
    r"^[AB]?(?P<booknum>\d{1,2})_+(?P<chapter>\d{1,3})_+(?P<name>.+?)_+(?P<setid>[A-Z0-9]+)\.mp3$",
    re.IGNORECASE,
)


def parse_vendor_name(filename: str) -> tuple[str, int] | None:
    """Return (abbrev, chapter) or None if unparseable."""
    m = VENDOR_RE.match(filename)
    if not m:
        return None
    abbrev = books.abbrev_for(m.group("name"))
    if abbrev is None:
        return None
    return abbrev, int(m.group("chapter"))


def run(root: Path) -> int:
    raw_dir, audio_dir = root / "raw", root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    m = manifest.load(root)
    done = skipped = failed = 0

    for src in sorted(raw_dir.glob("*.mp3")):
        parsed = parse_vendor_name(src.name)
        if parsed is None:
            manifest.add_error(m, "rename", f"unparseable filename: {src.name}")
            failed += 1
            continue
        abbrev, chapter = parsed
        chap_id = books.chapter_id(abbrev, chapter)
        dst = audio_dir / f"{chap_id}.mp3"
        entry = manifest.chapter_entry(m, chap_id)

        digest = manifest.sha256_file(src)
        if dst.exists() and entry.get("sha256") == digest:
            skipped += 1
            continue

        shutil.copy2(src, dst)  # copy, never move: raw/ stays pristine
        entry.update(
            book=abbrev,
            chapter=chapter,
            raw=str(src.relative_to(root)),
            audio=str(dst.relative_to(root)),
            sha256=digest,
            status="renamed",
        )
        done += 1

    manifest.save(root, m)
    print(f"rename: {done} copied, {skipped} up-to-date, {failed} unparseable")
    return 0 if failed == 0 else 1
