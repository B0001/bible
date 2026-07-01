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
instead of a binary known/unknown set. ``--study N --study-out PATH`` produces a
combined study queue of up to N items: due reviews (recall prob below threshold)
first, then new-word unlock recommendations. All Phase 5 flags are opt-in.
"""
import argparse
import csv
import os
import warnings
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import polars as pl
from nltk.stem.snowball import SnowballStemmer
from nltk.tokenize import RegexpTokenizer

try:
    from wordfreq import zipf_frequency as _zipf_frequency
    _WORDFREQ_AVAILABLE = True
except ImportError:
    _WORDFREQ_AVAILABLE = False

try:
    import numpy as _np
    import spacy as _spacy
    _SPACY_AVAILABLE = True
except ImportError:
    _SPACY_AVAILABLE = False

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
_REVIEW_P = 0.5  # recall probability below which a word is due for review
_SIM_TAU = 0.6   # minimum cosine similarity to grant semantic credit
_SIM_WEIGHT = 0.8  # maximum semantic credit (capped below 1.0)


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


def weighted_comprehension_rate(verse, profile, now, decay=True, min_verse_length=1, semantic_model=None):
    """Comprehension rate as the mean effective recall probability over a verse's tokens.

    Generalizes ``comprehension_rate``: with ``decay=False``, ``semantic_model=None``,
    and a seed-only profile this returns the identical value (known stems 1.0, unknown
    0.0). With ``decay=True``, recall decays over time. With a ``SemanticModel``,
    unknown tokens that are semantically similar to a known vocab word receive partial
    credit: ``p_effective = max(recall_prob, semantic_credit)``.
    """
    tokens = TOKENIZER.tokenize(verse.lower())
    if not tokens or len(tokens) < min_verse_length:
        return 0.0
    total = 0.0
    for token in tokens:
        stem = STEMMER.stem(token)
        p = recall_prob(profile.get(stem), now, decay)
        if semantic_model is not None and p < 1.0:
            p = max(p, semantic_model.credit(token))
        total += p
    return total / len(tokens)


_wordfreq_warned = False


def _word_difficulty(surface_word):
    """Difficulty score d(w) ∈ [0,1]: 1 = very rare, 0 = extremely common.

    Uses the Zipf frequency from ``wordfreq`` when the ``[lexical]`` extra is
    installed: ``d = clamp(1 - zipf / 8, 0, 1)``. Falls back to ``d = 1`` with
    a one-time warning when the extra is absent.
    """
    global _wordfreq_warned
    if not _WORDFREQ_AVAILABLE:
        if not _wordfreq_warned:
            warnings.warn(
                "wordfreq is not installed; pip install 'bible-reader[lexical]' for "
                "lexical effort scores. Falling back to d(w)=1 for all words.",
                ImportWarning,
                stacklevel=3,
            )
            _wordfreq_warned = True
        return 1.0
    zipf = _zipf_frequency(surface_word.lower(), "en")
    return max(0.0, min(1.0, 1.0 - zipf / 8.0))


def verse_effort(verse, profile, now, decay=True):
    """Lexical effort of the unknown/forgotten words in a verse.

    ``effort = Σ_i d(surface_token_i) * (1 - p_i)`` where ``p_i`` is each
    token's recall probability and ``d`` is the difficulty from
    ``_word_difficulty``. Fully known words (``p = 1``) contribute zero; fully
    unknown words contribute ``d(w)`` in full. With ``decay=False`` this reduces
    to the sum of ``d(w)`` over strictly unknown stems, so two verses at the
    same comprehension rate can be ranked by how *hard* their unknown words are.

    Requires the ``[lexical]`` extra (``wordfreq``); degrades to ``d(w) = 1``
    (i.e. effort = unknown-word count) when the extra is absent.
    """
    tokens = TOKENIZER.tokenize(verse.lower())
    effort = 0.0
    for token in tokens:
        stem = STEMMER.stem(token)
        p = recall_prob(profile.get(stem), _aware(now), decay=decay)
        effort += _word_difficulty(token) * (1.0 - p)
    return effort


_semantic_warned = False


class SemanticModel:
    """Pre-computed semantic model for a profile's surface vocabulary.

    Embeds the vocab's surface words (not Snowball stems) using a spaCy model so
    that unknown verse tokens can receive credit when they are close synonyms of
    known words (see PHASE5_DESIGN.md §4). Constructed via ``load_semantic_model``.
    """

    def __init__(self, nlp, profile_surface_words):
        self._nlp = nlp
        self._known_vecs = []
        for word in profile_surface_words:
            doc = nlp(word.lower())
            if doc.has_vector:
                self._known_vecs.append(doc.vector)

    def credit(self, surface_token):
        """Semantic credit for a surface token: ``SIM_WEIGHT * max_cosine`` if the
        best cosine similarity to any known word ≥ ``SIM_TAU``, else 0."""
        doc = self._nlp(surface_token.lower())
        if not doc.has_vector or not self._known_vecs:
            return 0.0
        token_vec = doc.vector
        norm = _np.linalg.norm(token_vec)
        if norm == 0.0:
            return 0.0
        max_sim = 0.0
        for kv in self._known_vecs:
            kn = _np.linalg.norm(kv)
            if kn > 0.0:
                max_sim = max(max_sim, float(_np.dot(token_vec, kv) / (norm * kn)))
        return _SIM_WEIGHT * max_sim if max_sim >= _SIM_TAU else 0.0


def load_semantic_model(vocab_path):
    """Load the spaCy model and pre-compute profile embeddings.

    Returns a ``SemanticModel`` if spaCy and ``en_core_web_md`` are available;
    returns ``None`` with a one-time warning otherwise. Surface words (not stems)
    from the vocab file are embedded, per PHASE5_DESIGN.md §4's critical constraint.
    """
    global _semantic_warned

    if not _SPACY_AVAILABLE:
        if not _semantic_warned:
            warnings.warn(
                "spacy is not installed; pip install 'bible-reader[semantic]' for "
                "semantic similarity scores. Falling back to credit=0.",
                ImportWarning,
                stacklevel=2,
            )
            _semantic_warned = True
        return None

    try:
        nlp = _spacy.load("en_core_web_md")
    except OSError:
        if not _semantic_warned:
            warnings.warn(
                "spaCy model en_core_web_md not found; run "
                "'python -m spacy download en_core_web_md'. Falling back to credit=0.",
                ImportWarning,
                stacklevel=2,
            )
            _semantic_warned = True
        return None

    with open(os.path.expanduser(vocab_path)) as f:
        surface_words = f.read().split()
    return SemanticModel(nlp, surface_words)


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


def study_queue(bible_df, profile, now, known_rate=0.95, review_p=_REVIEW_P, min_verse_length=1, top_n=20):
    """Combined study queue: due reviews first, then new-word unlock ranking.

    Due reviews: profile words whose recall_prob (with decay=True) is below
    ``review_p``, sorted ascending by probability (most-forgotten first).
    New words: the ``next_words_to_learn`` unlock ranking for stems absent from
    the profile.

    Returns a DataFrame with columns ``stem``, ``action`` (``"review"`` or
    ``"learn"``), ``score`` (recall probability for reviews, unlock count for
    learns), ``reason``; total rows capped at ``top_n``.
    """
    # Part 1: due reviews
    review_rows = []
    for stem, hist in profile.items():
        p = recall_prob(hist, _aware(now), decay=True)
        if p < review_p:
            if hist.last_seen is not None:
                elapsed = (_aware(now) - hist.last_seen).total_seconds() / 86400.0
                reason = f"recall p={p:.2f}, {elapsed:.1f}d since last seen"
            else:
                reason = f"recall p={p:.2f}"
            review_rows.append({"stem": stem, "action": "review", "score": p, "reason": reason})
    review_rows.sort(key=lambda r: r["score"])  # ascending: most-forgotten first

    # Part 2: new words — unknown stems ranked by verse-unlock count
    vocab_stems = set(profile.keys())
    new_words_df = next_words_to_learn(bible_df, vocab_stems, known_rate, min_verse_length, top_n)
    learn_rows = [
        {
            "stem": row["stem"],
            "action": "learn",
            "score": float(row["verses_unlocked"]),
            "reason": f"unlocks {row['verses_unlocked']} verse(s)",
        }
        for row in new_words_df.iter_rows(named=True)
    ]

    combined = (review_rows + learn_rows)[:top_n]
    if not combined:
        return pl.DataFrame(
            schema={"stem": pl.Utf8, "action": pl.Utf8, "score": pl.Float64, "reason": pl.Utf8}
        )
    return pl.DataFrame(
        combined,
        schema={"stem": pl.Utf8, "action": pl.Utf8, "score": pl.Float64, "reason": pl.Utf8},
    )


@contextmanager
def _open_write(path):
    """Open a local or s3:// path for binary writing; create local parent dirs."""
    if path.startswith("s3://"):
        try:
            import fsspec
        except ImportError:
            raise ImportError("pip install 'bible-reader[s3]' to write to S3") from None
        with fsspec.open(path, "wb") as f:
            yield f
    else:
        expanded = os.path.expanduser(path)
        d = os.path.dirname(expanded)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(expanded, "wb") as f:
            yield f


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
    parser.add_argument(
        "--study",
        type=int,
        default=0,
        help="produce a study queue of top N items (due reviews + new words); 0 disables (default 0)",
    )
    parser.add_argument(
        "--study-out",
        help="output CSV path for the study queue (required if --study > 0)",
    )
    parser.add_argument(
        "--effort",
        action="store_true",
        help="add a lexical-effort column to the graded output (requires [lexical] extra)",
    )
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="grant partial credit to verse words similar to known vocab words "
        "(requires [semantic] extra: spacy + en_core_web_md)",
    )
    args = parser.parse_args()
    if args.passage_window > 1 and not args.passage_out:
        parser.error("--passage-out is required when --passage-window > 1")
    if args.next_words > 0 and not args.next_words_out:
        parser.error("--next-words-out is required when --next-words > 0")
    if args.study > 0 and not args.study_out:
        parser.error("--study-out is required when --study > 0")
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

    now = None
    profile = None
    if args.decay or args.study > 0 or args.effort or args.semantic:
        now = datetime.now(timezone.utc)
        profile = load_profile(args.vocab)

    semantic_model = None
    if args.semantic:
        semantic_model = load_semantic_model(args.vocab)

    if args.decay or args.semantic:
        graded = bible_df.with_columns(
            pl.col("verse")
            .map_elements(
                lambda v: weighted_comprehension_rate(
                    v, profile, now, decay=args.decay,
                    min_verse_length=args.min_verse_length,
                    semantic_model=semantic_model,
                ),
                return_dtype=pl.Float64,
            )
            .alias("comprehension_rate")
        ).select("ref", "verse", "comprehension_rate")
    else:
        graded = grade(bible_df, vocab_stems, args.min_verse_length).select(
            "ref", "verse", "comprehension_rate"
        )

    if args.effort:
        graded = graded.with_columns(
            pl.col("verse")
            .map_elements(
                lambda v: verse_effort(v, profile, now, decay=args.decay),
                return_dtype=pl.Float64,
            )
            .alias("effort")
        )

    with _open_write(args.out) as f:
        graded.write_csv(f)

    easy = graded.filter(pl.col("comprehension_rate") >= args.known_rate)
    print(
        f"Graded {graded.height} verses -> {args.out}; "
        f"{easy.height} at >= {args.known_rate:.0%} comprehension."
    )

    if args.passage_window > 1:
        passages = grade_passages(bible_df, vocab_stems, args.passage_window, args.min_verse_length)
        with _open_write(args.passage_out) as f:
            passages.write_csv(f)

        easy_passages = passages.filter(pl.col("comprehension_rate") >= args.known_rate)
        print(
            f"Graded {passages.height} passages (window={args.passage_window}) -> "
            f"{args.passage_out}; {easy_passages.height} at >= {args.known_rate:.0%} comprehension."
        )

    if args.next_words > 0:
        next_words = next_words_to_learn(
            bible_df, vocab_stems, args.known_rate, args.min_verse_length, args.next_words
        )
        with _open_write(args.next_words_out) as f:
            next_words.write_csv(f)
        print(f"Ranked {next_words.height} next words to learn -> {args.next_words_out}")

    if args.study > 0:
        queue = study_queue(
            bible_df, profile, now,
            known_rate=args.known_rate,
            min_verse_length=args.min_verse_length,
            top_n=args.study,
        )
        with _open_write(args.study_out) as f:
            queue.write_csv(f)
        review_count = queue.filter(pl.col("action") == "review").height
        learn_count = queue.filter(pl.col("action") == "learn").height
        print(
            f"Study queue: {review_count} due review(s), {learn_count} new word(s) -> {args.study_out}"
        )


if __name__ == "__main__":
    main()
