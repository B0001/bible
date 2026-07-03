#!/usr/bin/env python3
"""Align chapter audio to verse text (PHASE10_DESIGN.md P10.1).

Takes a Whisper transcript with word timestamps and a chapter's verses, and
produces a per-verse timing sidecar JSON (D5 schema): for every verse,
``start``/``end`` seconds into the chapter audio and a ``confidence``.

Method (D2): both sides are normalized with parser.tokenize(_, "he") and then
reduced to consonant "skeletons" (matres lectionis י/ו dropped) to bridge
Whisper's modern plene spelling and WLC's defective spelling. Tokens unique in
both streams become anchors; a longest-increasing-subsequence pass keeps the
order-consistent subset; verse boundaries are linearly interpolated between
anchors. Confidence = fraction of a verse's tokens heard inside its window.

Usage:
    python scripts/align_audio.py --bible data/wlc.txt --book 1Chr --chapter 1 \
        --transcript 1chr1_transcript.json --audio data/audio/serve/1Chr_001.m4a \
        --out out/audio/wlc/1Chr_001.json
"""
import argparse
import bisect
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import load_bible, tokenize  # noqa: E402

MATRES = re.compile(r"[יו]")


def skeleton(token: str) -> str:
    """Consonant skeleton: token minus matres lectionis (י/ו)."""
    return MATRES.sub("", token)


def verse_token_stream(verses):
    """Flatten [(ref, text)] into a skeleton list plus each token's verse index."""
    skels, verse_of = [], []
    for i, (_, text) in enumerate(verses):
        for tok in tokenize(text, "he"):
            s = skeleton(tok)
            if s:
                skels.append(s)
                verse_of.append(i)
    return skels, verse_of


def asr_token_stream(words):
    """Flatten Whisper words [{word,start,end}] into (skeleton, start, end) tuples.

    One ASR "word" can normalize to zero or several tokens; every produced
    token inherits the word's timing.
    """
    out = []
    for w in words:
        for tok in tokenize(w["word"], "he"):
            s = skeleton(tok)
            if s:
                out.append((s, float(w["start"]), float(w["end"])))
    return out


def match_anchors(verse_skels, asr_skels, times=None, duration=None, tol=0.2):
    """Indices (verse_idx, asr_idx) of tokens unique in both streams, restricted
    to an order-consistent (longest increasing) subsequence.

    When ``times`` (per-ASR-token start seconds) and ``duration`` are given,
    pairs whose time deviates from the token's proportional position by more
    than ``tol * duration`` are rejected first: narration is continuous, so a
    rare skeleton coincidentally unique in both streams but belonging to
    different words shows up as a wild speech-rate outlier."""
    def uniques(seq):
        seen, dup = {}, set()
        for i, s in enumerate(seq):
            dup.add(s) if s in seen else seen.setdefault(s, i)
        return {s: i for s, i in seen.items() if s not in dup}

    v_pos, a_pos = uniques(verse_skels), uniques(asr_skels)
    pairs = sorted((v_pos[s], a_pos[s]) for s in v_pos.keys() & a_pos.keys())
    if times is not None and duration:
        pairs = [
            (vi, ai)
            for vi, ai in pairs
            if abs(times[ai] - vi / len(verse_skels) * duration) <= tol * duration
        ]
    # LIS on the asr side keeps only monotonic matches (drops crossed pairs).
    keys, choice = [], []
    for vi, ai in pairs:
        k = bisect.bisect_left(keys, ai)
        keys[k : k + 1] = [ai]
        choice.append((k, vi, ai))
    lis, need = [], len(keys) - 1
    for k, vi, ai in reversed(choice):
        if k == need:
            lis.append((vi, ai))
            need -= 1
    return lis[::-1]


def interp(x, xs, ys):
    """Piecewise-linear y(x) through (xs, ys), clamped at the ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    j = bisect.bisect_right(xs, x)
    x0, x1, y0, y1 = xs[j - 1], xs[j], ys[j - 1], ys[j]
    return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def align_chapter(verses, words, duration=None):
    """Per-verse [{ref, start, end, confidence}] for one chapter.

    verses: [(ref, text)]; words: Whisper word dicts; duration: audio length
    in seconds (defaults to the last word's end).
    """
    verse_skels, verse_of = verse_token_stream(verses)
    asr = asr_token_stream(words)
    if not verse_skels or not asr:
        return []
    duration = duration if duration is not None else asr[-1][2]

    anchors = match_anchors(
        verse_skels, [s for s, _, _ in asr], times=[t for _, t, _ in asr], duration=duration
    )
    if not anchors:
        return [
            {"ref": ref, "start": 0.0, "end": round(duration, 2), "confidence": 0.0}
            for ref, _ in verses
        ]
    xs = [vi for vi, _ in anchors]
    ys = [asr[ai][1] for _, ai in anchors]
    # Synthetic boundary anchors: spread unanchored leading/trailing verses
    # proportionally over the audio instead of collapsing them to zero width
    # (Whisper garbles name lists, leaving long anchor-free runs — see §2).
    if xs[0] > 0 and ys[0] > 0:
        xs.insert(0, 0)
        ys.insert(0, 0.0)
    if xs[-1] < len(verse_skels) - 1 and ys[-1] < duration:
        xs.append(len(verse_skels) - 1)
        ys.append(duration)

    # Verse boundary = interpolated time of its first token; a verse ends where
    # the next begins (the narrator reads continuously).
    first_tok = {}
    for tok_i, v in enumerate(verse_of):
        first_tok.setdefault(v, tok_i)
    starts = [interp(first_tok[v], xs, ys) for v in range(len(verses))]
    ends = starts[1:] + [duration]

    out = []
    asr_starts = [t for _, t, _ in asr]
    for v, (ref, _) in enumerate(verses):
        lo = bisect.bisect_left(asr_starts, starts[v] - 0.01)
        hi = bisect.bisect_left(asr_starts, ends[v])
        heard = {s for s, _, _ in asr[lo:hi]}
        mine = [verse_skels[i] for i, vv in enumerate(verse_of) if vv == v]
        conf = sum(s in heard for s in mine) / len(mine) if mine else 0.0
        out.append(
            {
                "ref": ref,
                "start": round(starts[v], 2),
                "end": round(ends[v], 2),
                "confidence": round(conf, 2),
            }
        )
    return out


def chapter_verses(bible_path, book, chapter):
    df = load_bible(bible_path)
    want = re.compile(rf"^{re.escape(book)} {chapter}:\d+$")
    rows = [(r, v) for r, v in zip(df["ref"], df["verse"]) if want.match(r)]
    return sorted(rows, key=lambda rv: int(rv[0].rsplit(":", 1)[1]))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bible", required=True, help="verse -- ref text file")
    ap.add_argument("--book", required=True, help="ref book id, e.g. 1Chr")
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--transcript", required=True, help="Whisper JSON (segments with word timestamps)")
    ap.add_argument("--audio", required=True, help="audio path to record in the sidecar")
    ap.add_argument("--bible-id", default="wlc")
    ap.add_argument("--duration", type=float, default=None, help="audio seconds (default: last word end)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.transcript, encoding="utf-8") as f:
        tr = json.load(f)
    words = [w for seg in tr["segments"] for w in seg.get("words", [])]
    verses = chapter_verses(args.bible, args.book, args.chapter)
    if not verses:
        sys.exit(f"no verses found for {args.book} {args.chapter} in {args.bible}")

    aligned = align_chapter(verses, words, args.duration)
    sidecar = {
        "bible_id": args.bible_id,
        "book": args.book,
        "chapter": args.chapter,
        "audio": args.audio,
        "duration": args.duration or (float(words[-1]["end"]) if words else 0.0),
        "verses": aligned,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, ensure_ascii=False, indent=1)

    flagged = [v for v in aligned if v["confidence"] < 0.5]
    print(f"{args.book} {args.chapter}: {len(aligned)} verses -> {args.out}")
    print(f"  mean confidence {sum(v['confidence'] for v in aligned) / len(aligned):.2f}, "
          f"{len(flagged)} verses below 0.5" + (f" ({', '.join(v['ref'] for v in flagged[:8])}…)" if flagged else ""))


if __name__ == "__main__":
    main()
