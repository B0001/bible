"""Tests for the comprehension-scoring core."""
import polars as pl

from parser import comprehension_rate, grade, load_bible, stem_tokens


def test_all_known_is_one():
    vocab = set(stem_tokens("the cat sat"))
    assert comprehension_rate("the cat sat", vocab) == 1.0


def test_none_known_is_zero():
    vocab = set(stem_tokens("alpha beta"))
    assert comprehension_rate("the cat sat", vocab) == 0.0


def test_partial_rate():
    vocab = set(stem_tokens("the cat"))
    # "the", "cat" known of "the cat sat" -> 2/3
    assert comprehension_rate("the cat sat", vocab) == 2 / 3


def test_empty_verse_scores_zero():
    vocab = set(stem_tokens("the cat"))
    assert comprehension_rate("", vocab) == 0.0
    assert comprehension_rate("!!! ???", vocab) == 0.0


def test_min_verse_length_guard():
    vocab = set(stem_tokens("the cat"))
    # one token, but threshold of 2 -> treated as too short
    assert comprehension_rate("cat", vocab, min_verse_length=2) == 0.0


def test_case_insensitive():
    vocab = set(stem_tokens("the cat"))
    assert comprehension_rate("THE CAT", vocab) == 1.0


def test_stem_variant_counts_as_known():
    # vocab "run" should mark morphological variants as known
    vocab = set(stem_tokens("run"))
    assert comprehension_rate("running", vocab) == 1.0
    assert comprehension_rate("she runs and ran", vocab) > 0.0


def test_grade_adds_column():
    df = pl.DataFrame({"verse": ["the cat sat"], "ref": ["Test 1:1"]})
    vocab = set(stem_tokens("the cat sat"))
    out = grade(df, vocab)
    assert out["comprehension_rate"][0] == 1.0


def test_load_bible_skips_malformed_lines(tmp_path):
    p = tmp_path / "bible.txt"
    p.write_text("Hello world -- Gen 1:1\nno separator here\nBye -- Gen 1:2\n")
    df = load_bible(str(p))
    assert df.height == 2
    assert df["ref"].to_list() == ["Gen 1:1", "Gen 1:2"]
