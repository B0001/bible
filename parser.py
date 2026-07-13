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
import re
import unicodedata
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
    import spacy as _spacy
    _SPACY_AVAILABLE = True
except ImportError:
    _SPACY_AVAILABLE = False

TOKENIZER = RegexpTokenizer(r"\w+")
STEMMER = SnowballStemmer("english", ignore_stopwords=True)

# ISO 639-1 code → NLTK Snowball stemmer name. Any of these languages gets
# stem-aware matching; he/el have dedicated mark-stripping paths below; any
# other code falls back to plain lowercased \w+ tokens with no stemmer.
SNOWBALL_LANGS = {
    "ar": "arabic", "de": "german", "en": "english",
    "es": "spanish", "fr": "french",
    "it": "italian", "nl": "dutch", "pt": "portuguese",
    "ru": "russian",
}

_STEMMERS = {"en": STEMMER}


def _stemmer_for(lang):
    """Cached SnowballStemmer for ``lang``, or None if Snowball doesn't cover it."""
    if lang not in _STEMMERS:
        name = SNOWBALL_LANGS.get(lang)
        if name is None:
            _STEMMERS[lang] = None
        else:
            try:
                _STEMMERS[lang] = SnowballStemmer(name, ignore_stopwords=True)
            except Exception:  # no NLTK stopword list for this language
                _STEMMERS[lang] = SnowballStemmer(name)
    return _STEMMERS[lang]


# Hebrew: strip niqqud/cantillation (U+0591–U+05C7), then extract consonants (U+05D0–U+05EA).
_HE_STRIP = re.compile(r"[֑-ׇ]")
_HE_TOKEN = re.compile(r"[א-ת]+")

# Greek: NFD-decompose, strip combining diacritics (U+0300–U+036F), then extract letters (α–ω).
_EL_DIACRITIC = re.compile(r"[̀-ͯ]")
_EL_TOKEN = re.compile(r"[α-ω]+")

# Arabic: strip harakat (U+064B–U+0652), superscript alef (U+0670), tatweel (U+0640).
_AR_STRIP = re.compile(r"[ً-ْٰـ]")


def tokenize(text, lang="en"):
    """Return a list of lowercased word tokens for the given language script.

    Hebrew (``he``): strip niqqud/cantillation, extract consonant runs.
    Greek (``el``): NFD-normalize, strip combining diacritics, extract letter runs.
    Arabic (``ar``): strip harakat/tatweel, then ``\\w+`` tokens.
    Everything else: NLTK RegexpTokenizer (``\\w+``) on lowercased text — this
    covers all Latin- and Cyrillic-script languages. (No word segmentation:
    zh/ja/th are not supported.) No stemming here; see ``tokenize_and_stem``.
    """
    if lang == "he":
        return _HE_TOKEN.findall(_HE_STRIP.sub("", text))
    if lang == "el":
        normalized = unicodedata.normalize("NFD", text)
        return _EL_TOKEN.findall(_EL_DIACRITIC.sub("", normalized).lower())
    if lang == "ar":
        return TOKENIZER.tokenize(_AR_STRIP.sub("", text).lower())
    return TOKENIZER.tokenize(text.lower())


def tokenize_and_stem(text, lang="en"):
    """Tokenize text and apply stemming where the language supports it.

    Languages in ``SNOWBALL_LANGS`` are Snowball-stemmed (so "running" and
    "run" share a stem — likewise "corriendo"/"correr" in Spanish, etc.).
    Hebrew/Greek and any language without a Snowball stemmer return bare
    tokens: consonantal Hebrew already conflates most variants; others match
    on exact (lowercased, mark-stripped) forms.
    """
    tokens = tokenize(text, lang)
    stemmer = _stemmer_for(lang)
    if stemmer is not None:
        return [stemmer.stem(tok) for tok in tokens]
    return tokens




def load_vocab(path, lang="en"):
    """Read a whitespace-separated vocabulary file into a set of word forms.

    Returns stems for English (Snowball) or bare tokens for Hebrew/Greek.
    """
    with open(os.path.expanduser(path)) as f:
        return set(tokenize_and_stem(f.read(), lang))


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


def record_review(vocab_path, word, correct, when=None, lang="en"):
    """Append one review event for ``word`` to the profile's review log.

    ``word`` is normalized with the language's tokenizer (stemmed for English,
    niqqud/diacritics stripped for Hebrew/Greek) so log keys match verse tokens.
    Creates the log (with header) on first use. Returns the form recorded, or
    None if ``word`` has no word tokens.
    """
    stems = tokenize_and_stem(word, lang)
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


def load_profile(vocab_path, lang="en"):
    """Load a profile: seed vocab (all stems) plus replayed review history.

    Returns ``dict[stem -> WordHistory]``. Seed words with no reviews get
    ``WordHistory(0, 0, None)``; words seen only in the review log are included
    too. The review log is ``<vocab>.reviews.csv`` (may be absent).
    """
    profile = {stem: WordHistory() for stem in load_vocab(vocab_path, lang)}
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


def weighted_comprehension_rate(verse, profile, now, decay=True, min_verse_length=1, semantic_model=None, lang="en"):
    """Comprehension rate as the mean effective recall probability over a verse's tokens.

    Generalizes ``comprehension_rate``: with ``decay=False``, ``semantic_model=None``,
    and a seed-only profile this returns the identical value (known stems 1.0, unknown
    0.0). With ``decay=True``, recall decays over time. With a ``SemanticModel``,
    unknown tokens that are semantically similar to a known vocab word receive partial
    credit: ``p_effective = max(recall_prob, semantic_credit)``.
    """
    tokens = tokenize(verse, lang)
    if not tokens or len(tokens) < min_verse_length:
        return 0.0
    total = 0.0
    for token in tokens:
        key = STEMMER.stem(token) if lang == "en" else token
        p = recall_prob(profile.get(key), now, decay)
        if semantic_model is not None and p < 1.0:
            p = max(p, semantic_model.credit(token))
        total += p
    return total / len(tokens)


_wordfreq_warned = False


def _word_difficulty(surface_word, lang="en"):
    """Difficulty score d(w) ∈ [0,1]: 1 = very rare, 0 = extremely common.

    Uses the Zipf frequency from ``wordfreq`` when the ``[lexical]`` extra is
    installed: ``d = clamp(1 - zipf / 8, 0, 1)``. wordfreq ships Hebrew (``he``)
    and Greek (``el``) wordlists, so the score is meaningful for all supported
    languages. Falls back to ``d = 1`` with a one-time warning when absent.
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
    try:
        zipf = _zipf_frequency(surface_word.lower(), lang)
    except LookupError:  # wordfreq has no wordlist for this language
        return 1.0
    return max(0.0, min(1.0, 1.0 - zipf / 8.0))


def verse_effort(verse, profile, now, decay=True, lang="en"):
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
    effort = 0.0
    for token in tokenize(verse, lang):
        key = STEMMER.stem(token) if lang == "en" else token
        p = recall_prob(profile.get(key), _aware(now), decay=decay)
        effort += _word_difficulty(token, lang) * (1.0 - p)
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
        self._known_docs = [doc for word in profile_surface_words
                            for doc in [nlp(word.lower())]
                            if doc.has_vector and doc.vector_norm > 0]

    def credit(self, surface_token):
        """Semantic credit for a surface token: ``SIM_WEIGHT * max_cosine`` if the
        best cosine similarity to any known word ≥ ``SIM_TAU``, else 0."""
        doc = self._nlp(surface_token.lower())
        if not doc.has_vector or doc.vector_norm == 0 or not self._known_docs:
            return 0.0
        max_sim = max(float(doc.similarity(kd)) for kd in self._known_docs)
        return _SIM_WEIGHT * max_sim if max_sim >= _SIM_TAU else 0.0


def load_semantic_model(vocab_path, lang="en"):
    """Load the spaCy model and pre-compute profile embeddings.

    Returns a ``SemanticModel`` if spaCy and ``en_core_web_md`` are available;
    returns ``None`` with a one-time warning otherwise. Surface words (not stems)
    from the vocab file are embedded, per PHASE5_DESIGN.md §4's critical constraint.

    English only: spaCy ships no usable Hebrew/Greek vector models, so
    ``lang != "en"`` returns ``None`` with a warning rather than silently
    scoring with the wrong language's embeddings.
    """
    global _semantic_warned

    if lang != "en":
        if not _semantic_warned:
            warnings.warn(
                f"--semantic is English-only (no spaCy vectors for {lang!r}); "
                "falling back to credit=0.",
                ImportWarning,
                stacklevel=2,
            )
            _semantic_warned = True
        return None

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


def comprehension_rate(verse, vocab_stems, min_verse_length=1, lang="en"):
    """Fraction of ``verse``'s word forms that appear in ``vocab_stems``.

    Returns 0.0 for verses shorter than ``min_verse_length`` tokens.
    ``vocab_stems`` should be produced by ``load_vocab(path, lang)`` so that
    the same tokenization/stemming is applied to both sides.
    """
    forms = tokenize_and_stem(verse, lang)
    # Guard against division by zero for empty/punctuation-only verses, even when
    # min_verse_length is set to 0.
    if not forms or len(forms) < min_verse_length:
        return 0.0
    known = sum(1 for f in forms if f in vocab_stems)
    return known / len(forms)


def grade_passages(bible_df, vocab_stems, window, min_verse_length=1, lang="en"):
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
                " ".join(verses[i : i + window]), vocab_stems, min_verse_length, lang
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


def longest_span(known, total, min_rate):
    """Longest contiguous index span ``[i, j)`` with combined ``known/total >= min_rate``.

    O(n) via prefix sums + monotone stack: the combined rate for [i, j) is
        (sum known[i..j-1]) / (sum total[i..j-1]) >= min_rate
    iff  P[j] - P[i] >= 0  where P is the prefix sum of a[k] = known[k] - min_rate*total[k].
    Left endpoints only need considering where P hits a new minimum (decreasing
    stack); a right-to-left sweep pops matches while tracking the widest span.

    Returns ``(start, end)`` half-open indices, or ``None`` if no span qualifies.
    Shared by ``grade_longest_passage`` and the Dash "Find longest passage" button.
    """
    n = len(known)
    P = [0.0] * (n + 1)
    for i in range(n):
        P[i + 1] = P[i] + known[i] - min_rate * total[i]

    stack = []
    for i in range(n + 1):
        if not stack or P[i] < P[stack[-1]]:
            stack.append(i)

    best_len = best_i = best_j = 0
    j = n
    while j >= 0 and stack:
        while stack and P[j] >= P[stack[-1]]:
            if j - stack[-1] > best_len:
                best_len, best_i, best_j = j - stack[-1], stack[-1], j
            stack.pop()
        j -= 1

    return (best_i, best_j) if best_len > 0 else None


def grade_longest_passage(bible_df, vocab_stems, min_rate=0.95, lang="en", min_verse_length=1):
    """Find the single longest contiguous verse sequence whose combined comprehension
    rate is >= ``min_rate`` (see ``longest_span`` for the algorithm).

    Returns a single-row DataFrame (start_ref, end_ref, passage, n_verses,
    comprehension_rate), or an empty DataFrame if no passage meets the threshold.
    """
    _SCHEMA = {
        "start_ref": pl.Utf8, "end_ref": pl.Utf8, "passage": pl.Utf8,
        "n_verses": pl.Int64, "comprehension_rate": pl.Float64,
    }
    refs = bible_df["ref"].to_list()
    verses = bible_df["verse"].to_list()

    # Per-verse known/total counts (same logic as comprehension_rate)
    known_arr = []
    total_arr = []
    for verse in verses:
        forms = tokenize_and_stem(verse, lang)
        total = len(forms)
        known = sum(1 for f in forms if f in vocab_stems) if total >= min_verse_length else 0
        known_arr.append(known)
        total_arr.append(total)

    span = longest_span(known_arr, total_arr, min_rate)
    if span is None:
        return pl.DataFrame(schema=_SCHEMA)

    i, j = span
    total_known = sum(known_arr[i:j])
    total_words = sum(total_arr[i:j])
    return pl.DataFrame(
        [{
            "start_ref": refs[i],
            "end_ref": refs[j - 1],
            "passage": " ".join(verses[i:j]),
            "n_verses": j - i,
            "comprehension_rate": total_known / total_words if total_words > 0 else 0.0,
        }],
        schema=_SCHEMA,
    )


def next_words_to_learn(bible_df, vocab_stems, known_rate=0.95, min_verse_length=1, top_n=20, lang="en"):
    """Rank unknown word forms by how many under-threshold verses learning them alone would unlock.

    A verse is "unlocked" by form ``w`` if the verse is currently below
    ``known_rate`` but adding every occurrence of ``w`` to the known set would
    push its comprehension rate to or above ``known_rate``. Tallies unlocks per
    unknown form across the corpus and returns the top ``top_n``, sorted by
    unlock count descending -- the highest-leverage next words to learn.
    """
    unlock_counts = Counter()
    for verse in bible_df["verse"]:
        forms = tokenize_and_stem(verse, lang)
        total = len(forms)
        if total < min_verse_length:
            continue
        counts = Counter(forms)
        known = sum(c for s, c in counts.items() if s in vocab_stems)
        if known / total >= known_rate:
            continue
        for form, count in counts.items():
            if form not in vocab_stems and (known + count) / total >= known_rate:
                unlock_counts[form] += 1

    ranked = sorted(unlock_counts.items(), key=lambda kv: -kv[1])[:top_n]
    return pl.DataFrame(
        {"stem": [s for s, _ in ranked], "verses_unlocked": [c for _, c in ranked]},
        schema={"stem": pl.Utf8, "verses_unlocked": pl.Int64},
    )


def study_queue(bible_df, profile, now, known_rate=0.95, review_p=_REVIEW_P, min_verse_length=1, top_n=20, lang="en"):
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
    new_words_df = next_words_to_learn(bible_df, vocab_stems, known_rate, min_verse_length, top_n, lang)
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
    """Open a path for binary writing; create parent dirs."""
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
        "--lang",
        default="en",
        help="ISO 639-1 language of the Bible text (default en). Stem-aware for "
        f"{', '.join(sorted(SNOWBALL_LANGS))}; he/el get mark-stripping; any other "
        "code falls back to plain lowercased word tokens.",
    )
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
    parser.add_argument(
        "--longest-passage-out",
        help="output CSV path for the longest readable passage at --known-rate",
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
        stem = record_review(args.vocab, word, outcome == "correct", lang=args.lang)
        if stem:
            print(f"Recorded review of '{word}' ({outcome}) -> stem '{stem}'")

    vocab_stems = load_vocab(args.vocab, args.lang)
    bible_df = load_bible(args.bible)

    now = None
    profile = None
    if args.decay or args.study > 0 or args.effort or args.semantic:
        now = datetime.now(timezone.utc)
        profile = load_profile(args.vocab, args.lang)

    semantic_model = None
    if args.semantic:
        semantic_model = load_semantic_model(args.vocab, args.lang)

    # One tokenization pass computes rate + known/total counts. The counts are
    # always written so the Dash app can run the longest-passage algorithm
    # without re-parsing the Bible.
    verses = bible_df["verse"].to_list()
    knowns, totals = [], []
    for verse in verses:
        forms = tokenize_and_stem(verse, args.lang)
        knowns.append(sum(1 for f in forms if f in vocab_stems))
        totals.append(len(forms))

    if args.decay or args.semantic:
        rates = [
            weighted_comprehension_rate(
                v, profile, now, decay=args.decay,
                min_verse_length=args.min_verse_length,
                semantic_model=semantic_model,
                lang=args.lang,
            )
            for v in verses
        ]
    else:
        rates = [
            k / t if t and t >= args.min_verse_length else 0.0
            for k, t in zip(knowns, totals)
        ]

    graded = bible_df.with_columns(
        pl.Series("comprehension_rate", rates, dtype=pl.Float64)
    ).select("ref", "verse", "comprehension_rate")

    if args.effort:
        graded = graded.with_columns(
            pl.Series(
                "effort",
                [verse_effort(v, profile, now, decay=args.decay, lang=args.lang) for v in verses],
                dtype=pl.Float64,
            )
        )

    graded = graded.with_columns(
        pl.Series("known_count", knowns, dtype=pl.Int64),
        pl.Series("total_count", totals, dtype=pl.Int64),
    )

    with _open_write(args.out) as f:
        graded.write_csv(f)

    easy = graded.filter(pl.col("comprehension_rate") >= args.known_rate)
    print(
        f"Graded {graded.height} verses -> {args.out}; "
        f"{easy.height} at >= {args.known_rate:.0%} comprehension."
    )

    if args.passage_window > 1:
        passages = grade_passages(bible_df, vocab_stems, args.passage_window, args.min_verse_length, args.lang)
        with _open_write(args.passage_out) as f:
            passages.write_csv(f)

        easy_passages = passages.filter(pl.col("comprehension_rate") >= args.known_rate)
        print(
            f"Graded {passages.height} passages (window={args.passage_window}) -> "
            f"{args.passage_out}; {easy_passages.height} at >= {args.known_rate:.0%} comprehension."
        )

    if args.next_words > 0:
        next_words = next_words_to_learn(
            bible_df, vocab_stems, args.known_rate, args.min_verse_length, args.next_words, args.lang
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
            lang=args.lang,
        )
        with _open_write(args.study_out) as f:
            queue.write_csv(f)
        review_count = queue.filter(pl.col("action") == "review").height
        learn_count = queue.filter(pl.col("action") == "learn").height
        print(
            f"Study queue: {review_count} due review(s), {learn_count} new word(s) -> {args.study_out}"
        )

    if args.longest_passage_out:
        lp = grade_longest_passage(
            bible_df, vocab_stems, args.known_rate, args.lang, args.min_verse_length
        )
        with _open_write(args.longest_passage_out) as f:
            lp.write_csv(f)
        if lp.height > 0:
            row = lp.row(0, named=True)
            print(
                f"Longest passage: {row['start_ref']}–{row['end_ref']} "
                f"({row['n_verses']} verses, {row['comprehension_rate']:.1%}) "
                f"-> {args.longest_passage_out}"
            )
        else:
            print(f"No passage >= {args.known_rate:.0%} found -> {args.longest_passage_out}")


if __name__ == "__main__":
    main()
