#!/usr/bin/env python3
"""Vocabulary-graded Bible parser.

Given a Bible text (lines of ``verse text -- reference``) and a user's known-word
vocabulary, score every verse by its *comprehension rate* -- the fraction of its
words the user already knows -- so that verses near the ~95% language-learning
sweet spot can be surfaced.

Canonical scoring (see SPEC.md §3): verse tokens and the vocabulary are both
lowercased and stemmed with the Snowball stemmer, so a vocab word counts all of
its morphological variants as known (vocab "run" -> "running", "ran"). A verse's
comprehension rate is::

    (# verse stems present in the stemmed vocab set) / (total verse stems)

Verses with fewer than ``--min-verse-length`` tokens score 0.

With ``--passage-window N`` (N > 1), also scores every contiguous N-verse
*passage* (sliding window, one verse at a time) as a single unit using the
same formula over the concatenated passage text -- multi-verse passages near
the comprehension sweet spot read more naturally than isolated verses.

With ``--next-words N`` (N > 0), also ranks the top N unknown words by how
many under-threshold verses learning each one (alone) would push to or above
``--known-rate`` -- the highest-leverage "what to learn next" words.

A ``--vocab`` path is just a vocab *profile* -- point it at different files for
different translations/learners. ``--learn WORD [WORD ...]`` appends newly
learned words to that profile file, persisting vocab growth across runs.

Phase 5 personalization (see PHASE5_DESIGN.md): ``--review WORD correct|wrong``
logs a review to the profile's ``<vocab>.reviews.csv``; ``--decay`` then scores
verses by time-decayed recall probability (a half-life model over that log)
instead of a binary known/unknown set. Both are opt-in and off by default.
"""
import argparse
import csv
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

import polars as pl
from nltk.stem.snowball import SnowballStemmer
from nltk.tokenize import RegexpTokenizer

TOKENIZER = RegexpTokenizer(r"\w+")
STEMMER = SnowballStemmer("english", ignore_stopwords=True)


def stem_tokens(text):
    """Lowercase, tokenize, and Snowball-stem ``text`` into a list of stems."""
    return [STEMMER.stem(tok) for tok in TOKENIZER.tokenize(text.lower())]


def load_vocab(path):
    """Read a whitespace-separated vocabulary file into a set of stems."""
    with open(os.path.expanduser(path)) as f:
        return set(stem_tokens(f.read()))


def update_vocab_file(path, new_words):
    """Append newly-learned ``new_words`` to the vocab file at ``path``.

    Persists vocab growth across runs: words already present (case-insensitive)
    are skipped, the file is created if it doesn't exist yet, and each added
    word is written on its own line. Different ``--vocab`` paths are simply
    different vocab profiles, so this is how a profile grows over time. Returns
    the list of words actually appended (deduplicated, lowercased).
    """
    path = os.path.expanduser(path)
    existing = set()
    if os.path.exists(path):
        with open(path) as f:
            existing = {w.lower() for w in f.read().split()}

    to_add = []
    for word in new_words:
        lw = word.lower()
        if lw not in existing:
            to_add.append(lw)
            existing.add(lw)

    if to_add:
        out_dir = os.path.dirname(path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(path, "a") as f:
            for word in to_add:
                f.write(word + "\n")
    return to_add


# --------------------------------------------------------------------------- #
# Phase 5 personalization: per-word review history and half-life recall model.
# See PHASE5_DESIGN.md. Everything here is opt-in; with decay off and a
# seed-only profile, weighted_comprehension_rate() reduces exactly to
# comprehension_rate(), so existing behavior is unchanged.
# --------------------------------------------------------------------------- #

# Half-life model constants (days). Heuristic Leitner/SM-2-lite form.
_H0 = 1.0        # base half-life for a brand-new word
_GROWTH = 2.0    # half-life multiplier per net-correct review
_H_MIN = 0.1     # clamp: ~2.4 hours
_H_MAX = 365.0   # clamp: 1 year


@dataclass
class WordHistory:
    """Replayed review state for one stem. ``last_seen`` is None for a seed word
    that has never been reviewed."""

    n_correct: int = 0
    n_incorrect: int = 0
    last_seen: datetime | None = None


def _reviews_path(vocab_path):
    """The review log co-located with a vocab profile: ``<vocab>.reviews.csv``."""
    return os.path.expanduser(vocab_path) + ".reviews.csv"


def _aware(ts):
    """Coerce a datetime to UTC-aware (assume naive timestamps are UTC)."""
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def record_review(vocab_path, word, correct, when=None):
    """Append one review event for ``word`` to the profile's review log.

    ``word`` is stemmed before storage. Creates the log (with header) on first
    use. Returns the stem recorded, or None if ``word`` has no word tokens.
    """
    stems = stem_tokens(word)
    if not stems:
        return None
    stem = stems[0]
    when = _aware(when or datetime.now(timezone.utc))

    path = _reviews_path(vocab_path)
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["stem", "timestamp", "correct"])
        writer.writerow([stem, when.isoformat(), int(bool(correct))])
    return stem


def load_profile(vocab_path):
    """Load a profile: seed vocab (all stems) plus replayed review history.

    Returns ``dict[stem -> WordHistory]``. Seed words with no reviews get
    ``WordHistory(0, 0, None)``; words seen only in the review log are included
    too. The review log is ``<vocab>.reviews.csv`` (may be absent).
    """
    profile = {stem: WordHistory() for stem in load_vocab(vocab_path)}
    path = _reviews_path(vocab_path)
    if os.path.exists(path):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                stem = row["stem"]
                ts = _aware(datetime.fromisoformat(row["timestamp"]))
                hist = profile.setdefault(stem, WordHistory())
                if int(row["correct"]):
                    hist.n_correct += 1
                else:
                    hist.n_incorrect += 1
                if hist.last_seen is None or ts > hist.last_seen:
                    hist.last_seen = ts
    return profile


def half_life(hist):
    """Estimated recall half-life (days) from review history, clamped."""
    h = _H0 * _GROWTH ** (hist.n_correct - hist.n_incorrect)
    return max(_H_MIN, min(_H_MAX, h))


def recall_prob(hist, now, decay=True):
    """Probability the learner still recalls a stem at time ``now``.

    ``hist is None`` (stem not in profile) -> 0.0. With ``decay`` off, any
    in-profile stem is 1.0 (the classic binary "known" set). With decay on, a
    seed word never reviewed is treated as freshly known (1.0); a reviewed word
    decays as ``2 ** (-elapsed_days / half_life)``.
    """
    if hist is None:
        return 0.0
    if not decay or hist.last_seen is None:
        return 1.0
    elapsed_days = (_aware(now) - hist.last_seen).total_seconds() / 86400.0
    return 2.0 ** (-elapsed_days / half_life(hist))


def weighted_comprehension_rate(verse, profile, now, decay=True, min_verse_length=1):
    """Comprehension rate as the mean recall probability over a verse's stems.

    Generalizes ``comprehension_rate``: with ``decay=False`` and a seed-only
    profile this returns the identical value (known stems contribute 1.0,
    unknown 0.0), so it is a drop-in that adds forgetting when decay is on.
    """
    stems = stem_tokens(verse)
    if not stems or len(stems) < min_verse_length:
        return 0.0
    return sum(recall_prob(profile.get(s), now, decay) for s in stems) / len(stems)


def load_bible(path):
    """Parse a ``verse text -- reference`` file into a polars DataFrame.

    Returns columns ``verse`` and ``ref``; lines without the `` -- `` separator
    are skipped.
    """
    rows = []
    with open(os.path.expanduser(path)) as f:
        for line in f:
            verse, sep, ref = line.rstrip("\n").partition(" -- ")
            if sep:
                rows.append({"verse": verse, "ref": ref})
    return pl.DataFrame(rows, schema={"verse": pl.Utf8, "ref": pl.Utf8})


def comprehension_rate(verse, vocab_stems, min_verse_length=1):
    """Fraction of ``verse``'s stems that appear in ``vocab_stems``.

    Returns 0.0 for verses shorter than ``min_verse_length`` tokens.
    """
    stems = stem_tokens(verse)
    # Guard against division by zero for empty/punctuation-only verses, even when
    # min_verse_length is set to 0.
    if not stems or len(stems) < min_verse_length:
        return 0.0
    known = sum(1 for stem in stems if stem in vocab_stems)
    return known / len(stems)


def grade(bible_df, vocab_stems, min_verse_length=1):
    """Add a ``comprehension_rate`` column to ``bible_df``."""
    return bible_df.with_columns(
        pl.col("verse")
        .map_elements(
            lambda v: comprehension_rate(v, vocab_stems, min_verse_length),
            return_dtype=pl.Float64,
        )
        .alias("comprehension_rate")
    )


def grade_passages(bible_df, vocab_stems, window, min_verse_length=1):
    """Score every contiguous ``window``-verse passage in ``bible_df``.

    Slides a window of size ``window`` one verse at a time over the rows (in
    file order), scoring each passage as a single unit via
    ``comprehension_rate`` over its concatenated text. This surfaces readable
    multi-verse *passages* near the comprehension sweet spot, not just single
    verses. Returns a DataFrame with ``start_ref``, ``end_ref``, ``passage``,
    ``comprehension_rate``, ``num_verses``; empty if the corpus is shorter than
    ``window``.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    refs = bible_df["ref"].to_list()
    verses = bible_df["verse"].to_list()
    rows = [
        {
            "start_ref": refs[i],
            "end_ref": refs[i + window - 1],
            "passage": " ".join(verses[i : i + window]),
            "comprehension_rate": comprehension_rate(
                " ".join(verses[i : i + window]), vocab_stems, min_verse_length
            ),
            "num_verses": window,
        }
        for i in range(len(verses) - window + 1)
    ]
    return pl.DataFrame(
        rows,
        schema={
            "start_ref": pl.Utf8,
            "end_ref": pl.Utf8,
            "passage": pl.Utf8,
            "comprehension_rate": pl.Float64,
            "num_verses": pl.Int64,
        },
    )


def next_words_to_learn(bible_df, vocab_stems, known_rate=0.95, min_verse_length=1, top_n=20):
    """Rank unknown stems by how many under-threshold verses learning them alone would unlock.

    A verse is "unlocked" by stem ``w`` if the verse is currently below
    ``known_rate`` but adding every occurrence of ``w`` to the known set would
    push its comprehension rate to or above ``known_rate``. Tallies unlocks per
    unknown stem across the corpus and returns the top ``top_n``, sorted by
    unlock count descending -- the highest-leverage next words to learn.
    """
    unlock_counts = Counter()
    for verse in bible_df["verse"]:
        stems = stem_tokens(verse)
        total = len(stems)
        if total < min_verse_length:
            continue
        counts = Counter(stems)
        known = sum(c for s, c in counts.items() if s in vocab_stems)
        if known / total >= known_rate:
            continue
        for stem, count in counts.items():
            if stem not in vocab_stems and (known + count) / total >= known_rate:
                unlock_counts[stem] += 1

    ranked = sorted(unlock_counts.items(), key=lambda kv: -kv[1])[:top_n]
    return pl.DataFrame(
        {"stem": [s for s, _ in ranked], "verses_unlocked": [c for _, c in ranked]},
        schema={"stem": pl.Utf8, "verses_unlocked": pl.Int64},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bible", required=True, help="path to '<verse> -- <ref>' text")
    parser.add_argument("--vocab", required=True, help="path to whitespace-separated vocab")
    parser.add_argument("--out", required=True, help="output CSV path")
    parser.add_argument(
        "--known-rate",
        type=float,
        default=0.95,
        help="comprehension threshold for the 'easy verses' summary (default 0.95)",
    )
    parser.add_argument(
        "--min-verse-length",
        type=int,
        default=1,
        help="verses with fewer tokens than this score 0 (default 1)",
    )
    parser.add_argument(
        "--passage-window",
        type=int,
        default=1,
        help="contiguous verses per passage; 1 disables passage scoring (default 1)",
    )
    parser.add_argument(
        "--passage-out",
        help="output CSV path for passage-level scoring (required if --passage-window > 1)",
    )
    parser.add_argument(
        "--next-words",
        type=int,
        default=0,
        help="rank top N unknown words by verses they'd unlock; 0 disables (default 0)",
    )
    parser.add_argument(
        "--next-words-out",
        help="output CSV path for the next-words ranking (required if --next-words > 0)",
    )
    parser.add_argument(
        "--learn",
        nargs="+",
        metavar="WORD",
        help="add WORD(s) to the --vocab file, persisting them for this and future runs",
    )
    parser.add_argument(
        "--review",
        nargs=2,
        metavar=("WORD", "OUTCOME"),
        help="record a review of WORD (OUTCOME: correct|wrong) in the profile's log",
    )
    parser.add_argument(
        "--decay",
        action="store_true",
        help="score with time-decayed recall (half-life model) from the review log "
        "instead of a binary known/unknown set",
    )
    args = parser.parse_args()
    if args.passage_window > 1 and not args.passage_out:
        parser.error("--passage-out is required when --passage-window > 1")
    if args.next_words > 0 and not args.next_words_out:
        parser.error("--next-words-out is required when --next-words > 0")
    if args.review and args.review[1] not in ("correct", "wrong"):
        parser.error("--review OUTCOME must be 'correct' or 'wrong'")

    if args.learn:
        added = update_vocab_file(args.vocab, args.learn)
        if added:
            print(f"Learned {len(added)} new word(s) -> {args.vocab}: {', '.join(added)}")

    if args.review:
        word, outcome = args.review
        stem = record_review(args.vocab, word, outcome == "correct")
        if stem:
            print(f"Recorded review of '{word}' ({outcome}) -> stem '{stem}'")

    vocab_stems = load_vocab(args.vocab)
    bible_df = load_bible(args.bible)
    if args.decay:
        now = datetime.now(timezone.utc)
        profile = load_profile(args.vocab)
        graded = bible_df.with_columns(
            pl.col("verse")
            .map_elements(
                lambda v: weighted_comprehension_rate(
                    v, profile, now, decay=True, min_verse_length=args.min_verse_length
                ),
                return_dtype=pl.Float64,
            )
            .alias("comprehension_rate")
        ).select("ref", "verse", "comprehension_rate")
    else:
        graded = grade(bible_df, vocab_stems, args.min_verse_length).select(
            "ref", "verse", "comprehension_rate"
        )

    out_dir = os.path.dirname(os.path.expanduser(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    graded.write_csv(os.path.expanduser(args.out))

    easy = graded.filter(pl.col("comprehension_rate") >= args.known_rate)
    print(
        f"Graded {graded.height} verses -> {args.out}; "
        f"{easy.height} at >= {args.known_rate:.0%} comprehension."
    )

    if args.passage_window > 1:
        passages = grade_passages(bible_df, vocab_stems, args.passage_window, args.min_verse_length)
        passage_out_dir = os.path.dirname(os.path.expanduser(args.passage_out))
        if passage_out_dir:
            os.makedirs(passage_out_dir, exist_ok=True)
        passages.write_csv(os.path.expanduser(args.passage_out))

        easy_passages = passages.filter(pl.col("comprehension_rate") >= args.known_rate)
        print(
            f"Graded {passages.height} passages (window={args.passage_window}) -> "
            f"{args.passage_out}; {easy_passages.height} at >= {args.known_rate:.0%} comprehension."
        )

    if args.next_words > 0:
        next_words = next_words_to_learn(
            bible_df, vocab_stems, args.known_rate, args.min_verse_length, args.next_words
        )
        next_words_out_dir = os.path.dirname(os.path.expanduser(args.next_words_out))
        if next_words_out_dir:
            os.makedirs(next_words_out_dir, exist_ok=True)
        next_words.write_csv(os.path.expanduser(args.next_words_out))
        print(f"Ranked {next_words.height} next words to learn -> {args.next_words_out}")


if __name__ == "__main__":
    main()
