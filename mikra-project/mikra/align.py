"""Stage 2: forced-align known Hebrew text against audio.

Uses stable-ts (stable_whisper) in align mode: the text is authoritative and
the model only places word-level timestamps. Imports are lazy so every other
CLI command works without torch installed.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import hebrew, manifest


def _audio_duration(path: Path) -> float | None:
    try:
        from mutagen.mp3 import MP3

        return float(MP3(str(path)).info.length)
    except Exception:
        return None


def _load_model(model_name: str, device: str | None):
    import stable_whisper  # lazy: torch is heavy

    return stable_whisper.load_model(model_name, device=device)


def align_chapter(
    root: Path, chap_id: str, entry: dict, model, keep_niqqud: bool, model_name: str
) -> str:
    """Align one chapter. Returns the resulting status string."""
    audio_path = root / entry["audio"]
    text_path = root / "text" / f"{chap_id}.txt"
    if not text_path.exists():
        return "missing_text"

    tokens = hebrew.tokenize_chapter(text_path.read_text(encoding="utf-8"))
    if not tokens:
        return "missing_text"
    norm = [hebrew.normalize(t["display"], keep_niqqud) for t in tokens]

    result = model.align(str(audio_path), " ".join(norm), language="he")
    words = [w for seg in result.segments for w in seg.words]

    if len(words) != len(tokens):
        entry["align_error"] = (
            f"token mismatch: text={len(tokens)} aligned={len(words)}"
        )
        return "align_failed"

    out = {
        "book": entry["book"],
        "chapter": entry["chapter"],
        "audio": entry["audio"],
        "duration": _audio_duration(audio_path),
        "model": model_name,
        "keep_niqqud": keep_niqqud,
        "words": [
            {
                "i": i,
                "display": tok["display"],
                "start": round(float(w.start), 3),
                "end": round(float(w.end), 3),
                "conf": round(float(getattr(w, "probability", 0.0) or 0.0), 3),
                "verse": tok["verse"],
            }
            for i, (tok, w) in enumerate(zip(tokens, words))
        ],
    }

    align_dir = root / "alignments"
    align_dir.mkdir(parents=True, exist_ok=True)
    (align_dir / f"{chap_id}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    entry["alignment"] = f"alignments/{chap_id}.json"
    entry.pop("align_error", None)
    return "aligned"


def run(
    root: Path,
    model_name: str = "large-v3",
    device: str | None = None,
    chapters: str | None = None,
    keep_niqqud: bool = False,
    force: bool = False,
) -> int:
    m = manifest.load(root)
    targets = {
        cid: e
        for cid, e in sorted(m["chapters"].items())
        if e.get("audio")
        and (chapters is None or cid.startswith(chapters))
        and (force or e.get("status") not in ("aligned", "validated"))
    }
    if not targets:
        print("align: nothing to do (use --force to re-align)")
        return 0

    print(f"align: loading model {model_name!r} ...")
    model = _load_model(model_name, device)

    counts: dict[str, int] = {}
    for chap_id, entry in targets.items():
        try:
            status = align_chapter(root, chap_id, entry, model, keep_niqqud, model_name)
        except Exception as exc:  # keep the batch going; record the failure
            entry["align_error"] = repr(exc)
            status = "align_failed"
        entry["status"] = status
        counts[status] = counts.get(status, 0) + 1
        print(f"  {chap_id}: {status}")
        manifest.save(root, m)  # checkpoint after every chapter

    print("align:", ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    return 0 if counts.get("aligned", 0) == len(targets) else 1
