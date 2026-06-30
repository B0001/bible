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
"""
import argparse
import os

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
    if len(stems) < min_verse_length:
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
    args = parser.parse_args()

    vocab_stems = load_vocab(args.vocab)
    bible_df = load_bible(args.bible)
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


if __name__ == "__main__":
    main()
