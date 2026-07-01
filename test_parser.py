"""Tests for the comprehension-scoring core."""
import polars as pl
import pytest

from parser import (
    comprehension_rate,
    grade,
    grade_passages,
    load_bible,
    load_vocab,
    next_words_to_learn,
    stem_tokens,
    update_vocab_file,
)


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


def test_empty_verse_with_min_length_zero_does_not_divide_by_zero():
    # min_verse_length=0 must not trigger 0/0 on a verse with no word tokens
    assert comprehension_rate("!!! ???", set(), min_verse_length=0) == 0.0


def test_grade_passages_rejects_nonpositive_window():
    df = pl.DataFrame({"verse": ["a b"], "ref": ["Gen 1:1"]})
    with pytest.raises(ValueError):
        grade_passages(df, set(), window=0)


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


def test_grade_passages_slides_one_verse_at_a_time():
    df = pl.DataFrame(
        {
            "verse": ["the cat sat", "on the mat", "in the sun"],
            "ref": ["Gen 1:1", "Gen 1:2", "Gen 1:3"],
        }
    )
    vocab = set(stem_tokens("the cat sat on mat in sun"))
    out = grade_passages(df, vocab, window=2)
    assert out.height == 2  # 3 verses, window 2 -> 2 sliding windows
    assert out["start_ref"].to_list() == ["Gen 1:1", "Gen 1:2"]
    assert out["end_ref"].to_list() == ["Gen 1:2", "Gen 1:3"]
    assert out["num_verses"].to_list() == [2, 2]
    assert out["comprehension_rate"][0] == 1.0


def test_grade_passages_scores_as_one_combined_unit():
    # neither verse alone is 100% known, but the union of their vocab is
    df = pl.DataFrame(
        {"verse": ["the cat", "a dog"], "ref": ["Gen 1:1", "Gen 1:2"]}
    )
    vocab = set(stem_tokens("the cat a dog"))
    out = grade_passages(df, vocab, window=2)
    assert out["comprehension_rate"][0] == 1.0


def test_grade_passages_window_larger_than_corpus_is_empty():
    df = pl.DataFrame({"verse": ["a b"], "ref": ["Gen 1:1"]})
    vocab = set(stem_tokens("a b"))
    out = grade_passages(df, vocab, window=5)
    assert out.height == 0


def test_next_words_to_learn_unlocks_single_missing_word():
    df = pl.DataFrame(
        {
            "verse": ["the cat sat", "the dog ran", "the dog ran again"],
            "ref": ["a", "b", "c"],
        }
    )
    vocab = set(stem_tokens("the cat sat ran"))
    # "the cat sat": fully known, already above threshold, excluded.
    # "the dog ran": 2/3 known; learning "dog" alone -> 3/3 -> unlocked.
    # "the dog ran again": 2/4 known; "dog" or "again" alone only reaches 3/4 -> not unlocked.
    out = next_words_to_learn(df, vocab, known_rate=0.95)
    assert out.height == 1
    assert out["stem"].to_list() == stem_tokens("dog")
    assert out["verses_unlocked"].to_list() == [1]


def test_next_words_to_learn_orders_most_unlocks_first():
    df = pl.DataFrame(
        {
            "verse": ["see the cat", "look at cat", "see the dog"],
            "ref": ["a", "b", "c"],
        }
    )
    vocab = set(stem_tokens("see the look at"))
    out = next_words_to_learn(df, vocab, known_rate=0.95)
    assert out["stem"].to_list() == [stem_tokens("cat")[0], stem_tokens("dog")[0]]
    assert out["verses_unlocked"].to_list() == [2, 1]


def test_next_words_to_learn_respects_top_n():
    df = pl.DataFrame(
        {
            "verse": ["see the cat", "look at cat", "see the dog"],
            "ref": ["a", "b", "c"],
        }
    )
    vocab = set(stem_tokens("see the look at"))
    out = next_words_to_learn(df, vocab, known_rate=0.95, top_n=1)
    assert out.height == 1
    assert out["stem"].to_list() == [stem_tokens("cat")[0]]


def test_update_vocab_file_appends_new_words(tmp_path):
    p = tmp_path / "vocab.txt"
    p.write_text("the cat\n")
    added = update_vocab_file(str(p), ["dog", "fish"])
    assert added == ["dog", "fish"]
    assert p.read_text() == "the cat\ndog\nfish\n"


def test_update_vocab_file_dedupes_case_insensitively(tmp_path):
    p = tmp_path / "vocab.txt"
    p.write_text("the Cat\n")
    added = update_vocab_file(str(p), ["cat", "CAT", "dog", "dog"])
    assert added == ["dog"]
    assert p.read_text() == "the Cat\ndog\n"


def test_update_vocab_file_creates_missing_file(tmp_path):
    p = tmp_path / "new_profile" / "vocab.txt"
    added = update_vocab_file(str(p), ["hello"])
    assert added == ["hello"]
    assert p.read_text() == "hello\n"


def test_update_vocab_file_persists_for_load_vocab(tmp_path):
    p = tmp_path / "vocab.txt"
    p.write_text("the cat\n")
    update_vocab_file(str(p), ["running"])
    # "running" should now stem-match "run" thanks to the persisted word
    assert stem_tokens("run")[0] in load_vocab(str(p))
