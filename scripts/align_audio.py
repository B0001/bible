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

Usage (one chapter):
    python scripts/align_audio.py --bible data/wlc.txt --book 1Chr --chapter 1 \
        --transcript 1chr1_transcript.json --audio data/audio/serve/1Chr_001.m4a \
        --out out/audio/wlc/1Chr_001.json

Usage (whole corpus — every transcript produced by transcribe_audio.py, plus
the manifest that dash_app.py / export_static.py consume; resumable, existing
sidecars are kept but still reported and listed in the manifest):
    python scripts/align_audio.py --bible data/wlc.txt --all
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


def verses_by_chapter(bible_path):
    """{(book, chapter): [(ref, text)] in verse order} for the whole bible."""
    df = load_bible(bible_path)
    by_ch = {}
    for ref, verse in zip(df["ref"], df["verse"]):
        book_ch, vnum = ref.rsplit(":", 1)
        book, ch = book_ch.rsplit(" ", 1)
        by_ch.setdefault((book, int(ch)), []).append((int(vnum), ref, verse))
    return {k: [(r, v) for _, r, v in sorted(rows)] for k, rows in by_ch.items()}


def chapter_of_stem(stem):
    """"1Chr_001" -> ("1Chr", 1); None if the stem isn't <book>_<NNN>."""
    m = re.fullmatch(r"(.+)_(\d+)", stem)
    return (m.group(1), int(m.group(2))) if m else None


def transcript_words(tr):
    return [w for seg in tr.get("segments", []) for w in seg.get("words", [])]


def write_sidecar(path, bible_id, book, chapter, audio, duration, aligned):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"bible_id": bible_id, "book": book, "chapter": chapter,
             "audio": audio, "duration": duration, "verses": aligned},
            f, ensure_ascii=False, indent=1,
        )


def align_corpus(bible_path, transcripts_dir, audio_dir, out_dir, manifest_path,
                 bible_id="wlc", floor=0.5):
    """Align every transcript, write sidecars + manifest, print a confidence
    report. Resumable: existing sidecars are kept, not recomputed."""
    by_ch = verses_by_chapter(bible_path)
    stems = sorted(
        os.path.splitext(n)[0] for n in os.listdir(transcripts_dir)
        if n.endswith(".json") and chapter_of_stem(os.path.splitext(n)[0])
    )

    chapters, aligned_now, by_book = [], 0, {}
    for stem in stems:
        book, chapter = chapter_of_stem(stem)
        verses = by_ch.get((book, chapter))
        if not verses:
            print(f"  {stem}: no verses for {book} {chapter} in {bible_path} — skipping")
            continue
        sidecar_path = os.path.join(out_dir, f"{stem}.json")
        if os.path.exists(sidecar_path):
            with open(sidecar_path, encoding="utf-8") as f:
                aligned = json.load(f)["verses"]
        else:
            with open(os.path.join(transcripts_dir, f"{stem}.json"), encoding="utf-8") as f:
                words = transcript_words(json.load(f))
            aligned = align_chapter(verses, words)
            duration = float(words[-1]["end"]) if words else 0.0
            audio = next(
                (os.path.join(audio_dir, f"{stem}{ext}") for ext in (".opus", ".wav")
                 if os.path.exists(os.path.join(audio_dir, f"{stem}{ext}"))),
                os.path.join(audio_dir, f"{stem}.opus"),
            )
            write_sidecar(sidecar_path, bible_id, book, chapter, audio, duration, aligned)
            aligned_now += 1
        chapters.append({"book": book, "chapter": chapter, "sidecar": sidecar_path})
        stats = by_book.setdefault(book, [0, 0.0, 0])  # verses, conf sum, flagged
        stats[0] += len(aligned)
        stats[1] += sum(v["confidence"] for v in aligned)
        stats[2] += sum(v["confidence"] < floor for v in aligned)

    os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"bible_id": bible_id, "audio_dir": audio_dir, "chapters": chapters},
                  f, ensure_ascii=False, indent=1)

    print(f"\n{len(chapters)} chapters in manifest ({aligned_now} aligned this run) "
          f"-> {manifest_path}")
    total = flagged = 0
    conf_sum = 0.0
    for book in sorted(by_book):
        n, s, fl = by_book[book]
        total, conf_sum, flagged = total + n, conf_sum + s, flagged + fl
        print(f"  {book:5} {n:4} verses, mean confidence {s / n:.2f}, {fl} below {floor}")
    if total:
        print(f"overall: {total} verses, mean {conf_sum / total:.2f}, "
              f"{flagged} ({100 * flagged / total:.1f}%) below {floor}")
    return chapters


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bible", required=True, help="verse -- ref text file")
    ap.add_argument("--bible-id", default="wlc")
    ap.add_argument("--all", action="store_true",
                    help="align every transcript in --transcripts and write the manifest")
    ap.add_argument("--transcripts", default="out/audio/transcripts")
    ap.add_argument("--audio-dir", default="data/audio/serve")
    ap.add_argument("--out-dir", default=None, help="default: out/audio/<bible-id>")
    ap.add_argument("--manifest", default=None, help="default: out/audio/<bible-id>_manifest.json")
    ap.add_argument("--book", help="ref book id, e.g. 1Chr (single-chapter mode)")
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--transcript", help="Whisper JSON (segments with word timestamps)")
    ap.add_argument("--audio", help="audio path to record in the sidecar")
    ap.add_argument("--duration", type=float, default=None, help="audio seconds (default: last word end)")
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.all:
        align_corpus(
            args.bible,
            args.transcripts,
            args.audio_dir,
            args.out_dir or f"out/audio/{args.bible_id}",
            args.manifest or f"out/audio/{args.bible_id}_manifest.json",
            bible_id=args.bible_id,
        )
        return

    if not all([args.book, args.chapter, args.transcript, args.audio, args.out]):
        sys.exit("single-chapter mode needs --book --chapter --transcript --audio --out "
                 "(or use --all)")

    with open(args.transcript, encoding="utf-8") as f:
        words = transcript_words(json.load(f))
    verses = chapter_verses(args.bible, args.book, args.chapter)
    if not verses:
        sys.exit(f"no verses found for {args.book} {args.chapter} in {args.bible}")

    aligned = align_chapter(verses, words, args.duration)
    duration = args.duration or (float(words[-1]["end"]) if words else 0.0)
    write_sidecar(args.out, args.bible_id, args.book, args.chapter, args.audio,
                  duration, aligned)

    flagged = [v for v in aligned if v["confidence"] < 0.5]
    print(f"{args.book} {args.chapter}: {len(aligned)} verses -> {args.out}")
    print(f"  mean confidence {sum(v['confidence'] for v in aligned) / len(aligned):.2f}, "
          f"{len(flagged)} verses below 0.5" + (f" ({', '.join(v['ref'] for v in flagged[:8])}…)" if flagged else ""))


if __name__ == "__main__":
    main()
