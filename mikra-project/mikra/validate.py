"""Stage 3: sanity-check every alignment. Nothing bad ships silently."""
from __future__ import annotations

import json
from pathlib import Path

from . import hebrew, manifest

MAX_WORD_SECONDS = 5.0
MIN_MEAN_CONF = 0.70
DURATION_SLACK = 0.5


def check_chapter(root: Path, chap_id: str, entry: dict) -> list[str]:
    problems: list[str] = []
    align_path = root / entry.get("alignment", f"alignments/{chap_id}.json")
    if not align_path.exists():
        return ["alignment file missing"]

    data = json.loads(align_path.read_text(encoding="utf-8"))
    words = data.get("words", [])
    if not words:
        return ["no words in alignment"]

    text_path = root / "text" / f"{chap_id}.txt"
    if text_path.exists():
        n_text = len(hebrew.tokenize_chapter(text_path.read_text(encoding="utf-8")))
        if n_text != len(words):
            problems.append(f"word count mismatch: text={n_text} aligned={len(words)}")
    else:
        problems.append("text file missing")

    prev_end = -1.0
    for w in words:
        if w["start"] < prev_end - 1e-3:
            problems.append(f"non-monotonic timestamp at word {w['i']}")
            break
        prev_end = w["end"]

    longest = max(w["end"] - w["start"] for w in words)
    if longest > MAX_WORD_SECONDS:
        problems.append(f"word longer than {MAX_WORD_SECONDS}s ({longest:.2f}s)")

    mean_conf = sum(w.get("conf", 0.0) for w in words) / len(words)
    if mean_conf < MIN_MEAN_CONF:
        problems.append(f"mean confidence {mean_conf:.2f} < {MIN_MEAN_CONF}")

    duration = data.get("duration")
    if duration and words[-1]["end"] > duration + DURATION_SLACK:
        problems.append(
            f"last word ends at {words[-1]['end']:.1f}s past audio {duration:.1f}s"
        )
    return problems


def run(root: Path) -> int:
    m = manifest.load(root)
    passed = failed = 0
    for chap_id, entry in sorted(m["chapters"].items()):
        if entry.get("status") not in ("aligned", "validated", "needs_review"):
            continue
        problems = check_chapter(root, chap_id, entry)
        if problems:
            entry["status"] = "needs_review"
            entry["validation_errors"] = problems
            failed += 1
            print(f"  {chap_id}: NEEDS REVIEW — {'; '.join(problems)}")
        else:
            entry["status"] = "validated"
            entry.pop("validation_errors", None)
            passed += 1
    manifest.save(root, m)
    print(f"validate: {passed} passed, {failed} need review")
    return 0 if failed == 0 else 1
