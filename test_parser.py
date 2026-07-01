"""Tests for the comprehension-scoring core."""
import importlib.util
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from parser import (
    SemanticModel,
    comprehension_rate,
    grade,
    grade_passages,
    half_life,
    load_bible,
    load_profile,
    load_semantic_model,
    load_vocab,
    next_words_to_learn,
    recall_prob,
    record_review,
    stem_tokens,
    study_queue,
    update_vocab_file,
    verse_effort,
    weighted_comprehension_rate,
)

_wordfreq_available = importlib.util.find_spec("wordfreq") is not None
_spacy_available = importlib.util.find_spec("spacy") is not None


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


# --------------------------------------------------------------------------- #
# Phase 5: review history + half-life recall model
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 6, 30, tzinfo=timezone.utc)


def test_record_review_creates_log_and_load_profile_replays(tmp_path):
    p = tmp_path / "vocab.txt"
    p.write_text("faith\n")
    record_review(str(p), "faith", correct=True, when=NOW)
    record_review(str(p), "faith", correct=False, when=NOW)
    profile = load_profile(str(p))
    stem = stem_tokens("faith")[0]
    assert profile[stem].n_correct == 1
    assert profile[stem].n_incorrect == 1
    assert profile[stem].last_seen == NOW


def test_load_profile_includes_review_only_words(tmp_path):
    p = tmp_path / "vocab.txt"
    p.write_text("faith\n")  # "grace" only ever appears in the review log
    record_review(str(p), "grace", correct=True, when=NOW)
    profile = load_profile(str(p))
    assert stem_tokens("grace")[0] in profile


def test_half_life_grows_with_net_correct():
    from parser import WordHistory

    weak = WordHistory(n_correct=0, n_incorrect=0)
    strong = WordHistory(n_correct=3, n_incorrect=0)
    assert half_life(strong) > half_life(weak)


def test_recall_prob_decays_over_time():
    from parser import WordHistory

    hist = WordHistory(n_correct=1, n_incorrect=0, last_seen=NOW)
    fresh = recall_prob(hist, NOW, decay=True)
    later = recall_prob(hist, NOW + timedelta(days=30), decay=True)
    assert fresh == pytest.approx(1.0)
    assert 0.0 < later < fresh


def test_recall_prob_unknown_and_decay_off():
    from parser import WordHistory

    assert recall_prob(None, NOW, decay=True) == 0.0          # not in profile
    hist = WordHistory(last_seen=NOW - timedelta(days=999))
    assert recall_prob(hist, NOW, decay=False) == 1.0         # binary "known"


def test_weighted_rate_matches_binary_when_decay_off():
    # Backward-compatibility guarantee from PHASE5_DESIGN.md §0.3
    vocab_text = "the cat sat on"
    p_profile = {s: __import__("parser").WordHistory() for s in stem_tokens(vocab_text)}
    vocab = set(stem_tokens(vocab_text))
    for verse in ["the cat sat", "the dog ran on", "!!!", "sat quietly"]:
        assert weighted_comprehension_rate(
            verse, p_profile, NOW, decay=False
        ) == comprehension_rate(verse, vocab)


# --------------------------------------------------------------------------- #
# Phase 5.2: study queue
# --------------------------------------------------------------------------- #

from parser import WordHistory  # noqa: E402 (after NOW is defined above)


def _old_hist(n_incorrect=2, days_ago=100):
    """Helper: a WordHistory whose recall probability will be well below 0.5."""
    return WordHistory(n_correct=0, n_incorrect=n_incorrect, last_seen=NOW - timedelta(days=days_ago))


def test_study_queue_schema():
    """Output always has the four required columns even when empty."""
    queue = study_queue(pl.DataFrame({"verse": ["hello"], "ref": ["a"]}), {}, NOW)
    assert set(queue.columns) == {"stem", "action", "score", "reason"}


def test_study_queue_due_review_appears():
    """A profile word with low recall probability shows up as a 'review' item."""
    faith_stem = stem_tokens("faith")[0]
    profile = {faith_stem: _old_hist()}
    bible_df = pl.DataFrame({"verse": ["faith hope"], "ref": ["a"]})

    queue = study_queue(bible_df, profile, NOW)
    assert "review" in queue["action"].to_list()
    assert faith_stem in queue.filter(pl.col("action") == "review")["stem"].to_list()


def test_study_queue_new_word_appears():
    """An unknown word that would unlock a verse appears as a 'learn' item."""
    # Profile knows 2 of 3 words; learning the 3rd pushes comprehension to 3/3 >= 0.95
    known_stems = stem_tokens("the sat")
    profile = {s: WordHistory() for s in known_stems}
    bible_df = pl.DataFrame({"verse": ["the cat sat"], "ref": ["a"]})

    queue = study_queue(bible_df, profile, NOW)
    assert "learn" in queue["action"].to_list()


def test_study_queue_reviews_before_learns():
    """Due reviews are ordered before new-word recommendations."""
    faith_stem = stem_tokens("faith")[0]
    profile = {faith_stem: _old_hist()}
    bible_df = pl.DataFrame({"verse": ["the cat sat"], "ref": ["a"]})

    queue = study_queue(bible_df, profile, NOW)
    actions = queue["action"].to_list()
    if "review" in actions and "learn" in actions:
        last_review = max(i for i, a in enumerate(actions) if a == "review")
        first_learn = min(i for i, a in enumerate(actions) if a == "learn")
        assert last_review < first_learn


def test_study_queue_reviews_sorted_ascending_by_score():
    """Most-forgotten words (lowest recall prob) come first among review items."""
    faith_stem = stem_tokens("faith")[0]
    grace_stem = stem_tokens("grace")[0]
    profile = {
        faith_stem: WordHistory(n_correct=0, n_incorrect=3, last_seen=NOW - timedelta(days=200)),
        grace_stem: WordHistory(n_correct=0, n_incorrect=1, last_seen=NOW - timedelta(days=30)),
    }
    bible_df = pl.DataFrame({"verse": ["faith grace"], "ref": ["a"]})

    queue = study_queue(bible_df, profile, NOW)
    review_rows = queue.filter(pl.col("action") == "review")
    if review_rows.height >= 2:
        scores = review_rows["score"].to_list()
        assert scores == sorted(scores)


def test_study_queue_top_n_caps_total():
    """top_n limits the total number of rows returned."""
    profile = {}  # nothing known -> everything is a "learn" candidate
    bible_df = pl.DataFrame({"verse": ["cat dog bird fish"], "ref": ["a"]})

    queue = study_queue(bible_df, profile, NOW, top_n=2)
    assert queue.height <= 2


def test_study_queue_empty_when_nothing_to_do():
    """Seed-only profile with complete verse coverage produces an empty queue."""
    profile = {s: WordHistory() for s in stem_tokens("the cat sat")}
    bible_df = pl.DataFrame({"verse": ["the cat sat"], "ref": ["a"]})
    # Seed words have p=1.0 (no reviews, so last_seen=None -> p=1.0), no due reviews.
    # All verse stems are in the profile, so next_words_to_learn returns nothing.
    queue = study_queue(bible_df, profile, NOW, known_rate=0.95)
    assert queue.height == 0


def test_study_queue_seed_words_not_in_due_reviews():
    """Seed words (no review history) are not flagged as due for review."""
    profile = {s: WordHistory() for s in stem_tokens("faith grace")}
    bible_df = pl.DataFrame({"verse": ["faith grace"], "ref": ["a"]})

    queue = study_queue(bible_df, profile, NOW)
    assert queue.filter(pl.col("action") == "review").height == 0


# --------------------------------------------------------------------------- #
# Phase 5.3: lexical effort (verse_effort)
# --------------------------------------------------------------------------- #

def test_verse_effort_zero_for_all_known():
    """A verse where every stem is in the seed profile has zero effort."""
    profile = {s: WordHistory() for s in stem_tokens("the cat sat")}
    effort = verse_effort("the cat sat", profile, NOW, decay=False)
    assert effort == 0.0


def test_verse_effort_nonzero_for_unknown():
    """Effort is positive when some verse words are unknown."""
    profile = {s: WordHistory() for s in stem_tokens("the")}
    effort = verse_effort("the cat sat", profile, NOW, decay=False)
    assert effort > 0.0


def test_verse_effort_increases_with_more_unknowns():
    """More unknown words → higher effort (with fallback d=1)."""
    profile = {s: WordHistory() for s in stem_tokens("the")}
    effort_one_unknown = verse_effort("the cat", profile, NOW, decay=False)
    effort_two_unknown = verse_effort("the cat sat", profile, NOW, decay=False)
    assert effort_two_unknown > effort_one_unknown


def test_verse_effort_decay_off_counts_unknown_words():
    """With decay=False and wordfreq absent, effort = number of unknown word tokens."""
    import parser as _parser
    orig = _parser._WORDFREQ_AVAILABLE
    _parser._WORDFREQ_AVAILABLE = False
    _parser._wordfreq_warned = False
    try:
        profile = {s: WordHistory() for s in stem_tokens("the")}
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            effort = verse_effort("the cat sat", profile, NOW, decay=False)
        # "cat" and "sat" are unknown; d=1 for each → effort = 2.0
        assert effort == pytest.approx(2.0)
    finally:
        _parser._WORDFREQ_AVAILABLE = orig
        _parser._wordfreq_warned = False


@pytest.mark.lexical
@pytest.mark.skipif(not _wordfreq_available, reason="[lexical] extra not installed")
def test_verse_effort_common_word_has_lower_difficulty():
    """A very common unknown word has lower effort than a rare one (requires wordfreq)."""
    # "the" is one of the most common English words (high Zipf, low d)
    # "seraphim" is rare (low Zipf, high d)
    profile = {}  # nothing known
    effort_common = verse_effort("the", profile, NOW, decay=False)
    effort_rare = verse_effort("seraphim", profile, NOW, decay=False)
    assert effort_rare > effort_common


@pytest.mark.lexical
@pytest.mark.skipif(not _wordfreq_available, reason="[lexical] extra not installed")
def test_verse_effort_same_rate_different_difficulty():
    """Two verses at the same comprehension rate are ranked by word difficulty."""
    # "the dog" and "a seraphim": both 0/2 known
    profile = {}
    effort_easy = verse_effort("the dog", profile, NOW, decay=False)
    effort_hard = verse_effort("a seraphim", profile, NOW, decay=False)
    # "seraphim" is much rarer than "dog", so the hard verse has higher effort
    assert effort_hard > effort_easy


# --------------------------------------------------------------------------- #
# Phase 5.4: semantic similarity fallback (SemanticModel / load_semantic_model)
# --------------------------------------------------------------------------- #

def test_weighted_rate_unchanged_when_semantic_model_none():
    """semantic_model=None (default) leaves weighted_comprehension_rate unchanged."""
    vocab_text = "the cat sat on"
    profile = {s: WordHistory() for s in stem_tokens(vocab_text)}
    vocab = set(stem_tokens(vocab_text))
    for verse in ["the cat sat", "the dog ran on", "sat quietly"]:
        assert weighted_comprehension_rate(
            verse, profile, NOW, decay=False, semantic_model=None
        ) == comprehension_rate(verse, vocab)


def test_load_semantic_model_returns_none_when_spacy_absent(tmp_path):
    """load_semantic_model gracefully returns None when spaCy is not available."""
    import parser as _parser
    orig = _parser._SPACY_AVAILABLE
    _parser._SPACY_AVAILABLE = False
    _parser._semantic_warned = False
    try:
        p = tmp_path / "vocab.txt"
        p.write_text("happy\n")
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = load_semantic_model(str(p))
        assert model is None
    finally:
        _parser._SPACY_AVAILABLE = orig
        _parser._semantic_warned = False


def test_weighted_rate_no_credit_when_semantic_model_none():
    """An unknown word gets p=0 when no semantic model is provided."""
    profile = {s: WordHistory() for s in stem_tokens("the")}
    rate = weighted_comprehension_rate("the joyful", profile, NOW, decay=False, semantic_model=None)
    # "the" known (p=1), "joyful" unknown (p=0) -> 0.5
    assert rate == pytest.approx(0.5)


@pytest.mark.semantic
@pytest.mark.skipif(not _spacy_available, reason="[semantic] extra not installed")
def test_semantic_model_credit_for_similar_word(tmp_path):
    """A word semantically similar to a known vocab word gets nonzero credit."""
    import spacy
    try:
        nlp = spacy.load("en_core_web_md")
    except OSError:
        pytest.skip("en_core_web_md not installed")
    model = SemanticModel(nlp, ["happy"])
    # "joyful" is similar to "happy"
    assert model.credit("joyful") > 0.0


@pytest.mark.semantic
@pytest.mark.skipif(not _spacy_available, reason="[semantic] extra not installed")
def test_semantic_model_credit_caps_at_sim_weight(tmp_path):
    """Semantic credit never exceeds SIM_WEIGHT."""
    import spacy
    from parser import _SIM_WEIGHT
    try:
        nlp = spacy.load("en_core_web_md")
    except OSError:
        pytest.skip("en_core_web_md not installed")
    model = SemanticModel(nlp, ["happy", "joyful", "glad"])
    assert model.credit("joyful") <= _SIM_WEIGHT


@pytest.mark.semantic
@pytest.mark.skipif(not _spacy_available, reason="[semantic] extra not installed")
def test_semantic_credit_raises_comprehension_rate(tmp_path):
    """weighted_comprehension_rate is higher with a semantic model than without."""
    import spacy
    try:
        nlp = spacy.load("en_core_web_md")
    except OSError:
        pytest.skip("en_core_web_md not installed")
    # Profile knows "happy"; verse contains "joyful" (semantically similar but stem-unknown)
    profile = {s: WordHistory() for s in stem_tokens("happy")}
    model = SemanticModel(nlp, ["happy"])
    rate_without = weighted_comprehension_rate("joyful", profile, NOW, decay=False, semantic_model=None)
    rate_with = weighted_comprehension_rate("joyful", profile, NOW, decay=False, semantic_model=model)
    assert rate_with > rate_without
