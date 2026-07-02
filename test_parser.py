"""Tests for the comprehension-scoring core."""
import importlib.util
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from parser import (
    SemanticModel,
    comprehension_rate,
    grade,
    grade_longest_passage,
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
    tokenize,
    tokenize_and_stem,
    update_vocab_file,
    verse_effort,
    weighted_comprehension_rate,
)

_wordfreq_available = importlib.util.find_spec("wordfreq") is not None
_spacy_available = importlib.util.find_spec("spacy") is not None


# --------------------------------------------------------------------------- #
# P7.0: language-aware tokenizer
# --------------------------------------------------------------------------- #

def test_tokenize_english_unchanged():
    assert tokenize("The Cat Sat", "en") == ["the", "cat", "sat"]


def test_tokenize_hebrew_extracts_consonants():
    # שָׁלוֹם with niqqud → שלום (consonants only)
    assert tokenize("שָׁלוֹם", "he") == ["שלום"]


def test_tokenize_hebrew_strips_cantillation():
    # Word with tiphcha (U+05D8 is tet, but let's use a cantillation mark like etnachta U+05C1... wait
    # Let's use a niqqud mark: patach U+05B7 on alef.
    # אַ (alef + patach U+05B7) → alef
    word_with_niqqud = "אַלֹהֵים"  # אֱלֹהֵים (Elohim) with niqqud
    result = tokenize(word_with_niqqud, "he")
    assert result == ["אלהים"]  # אלהים without niqqud


def test_tokenize_greek_strips_diacritics():
    # ἐν (epsilon + smooth breathing + accent) → εν
    assert tokenize("ἐν", "el") == ["εν"]


def test_tokenize_greek_strips_diacritics_extended():
    # ἀρχῇ (John 1:1) → αρχη
    assert tokenize("ἀρχῇ", "el") == ["αρχη"]


def test_tokenize_and_stem_hebrew_no_stemmer():
    # Hebrew tokenization should return bare consonants, not Snowball stems
    result = tokenize_and_stem("שָׁלוֹם", "he")
    assert result == ["שלום"]


def test_tokenize_and_stem_greek_no_stemmer():
    result = tokenize_and_stem("ἐν", "el")
    assert result == ["εν"]


def test_comprehension_rate_hebrew():
    # שלום is in the vocab; rate should be 1.0
    vocab = set(tokenize_and_stem("שלום", "he"))
    assert comprehension_rate("שָׁלוֹם", vocab, lang="he") == 1.0


def test_comprehension_rate_greek():
    # εν is in the vocab
    vocab = set(tokenize_and_stem("εν", "el"))
    assert comprehension_rate("ἐν", vocab, lang="el") == 1.0


def test_english_default_lang_unchanged():
    # Existing English behavior is unaffected by the lang parameter default
    vocab = set(stem_tokens("the cat sat"))
    assert comprehension_rate("the cat sat", vocab) == 1.0
    assert comprehension_rate("the cat sat", vocab, lang="en") == 1.0


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


# --------------------------------------------------------------------------- #
# P8.3: language-aware Phase 5 personalization
# --------------------------------------------------------------------------- #

def test_record_review_hebrew_strips_niqqud(tmp_path):
    """A Hebrew review word with niqqud is stored as bare consonants, matching verse tokens."""
    p = tmp_path / "vocab.txt"
    p.write_text("אהבה\n")
    stem = record_review(str(p), "שָׁלוֹם", correct=True, when=NOW, lang="he")
    assert stem == "שלום"
    profile = load_profile(str(p), lang="he")
    assert "שלום" in profile
    assert profile["שלום"].n_correct == 1


def test_hebrew_review_affects_decay_scoring(tmp_path):
    """End-to-end: a reviewed Hebrew word scores by recall probability in a verse."""
    p = tmp_path / "vocab.txt"
    p.write_text("")  # empty seed vocab
    record_review(str(p), "שָׁלוֹם", correct=True, when=NOW, lang="he")
    profile = load_profile(str(p), lang="he")
    # Verse is just the reviewed word (with niqqud); freshly reviewed -> p = 1.0
    rate = weighted_comprehension_rate("שָׁלוֹם", profile, NOW, decay=True, lang="he")
    assert rate == pytest.approx(1.0)


def test_load_profile_hebrew_seed_vocab(tmp_path):
    """Hebrew seed vocab keys are bare consonants, not English stems."""
    p = tmp_path / "vocab.txt"
    p.write_text("שָׁלוֹם\n")
    profile = load_profile(str(p), lang="he")
    assert "שלום" in profile


def test_load_semantic_model_refuses_non_english(tmp_path):
    """Semantic credit is English-only; other languages get None + warning."""
    import parser as _parser
    _parser._semantic_warned = False
    try:
        p = tmp_path / "vocab.txt"
        p.write_text("שלום\n")
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert load_semantic_model(str(p), lang="he") is None
    finally:
        _parser._semantic_warned = False


@pytest.mark.lexical
@pytest.mark.skipif(not _wordfreq_available, reason="[lexical] extra not installed")
def test_word_difficulty_uses_hebrew_wordlist():
    """A very common Hebrew word gets a real (below-fallback) difficulty for lang='he'."""
    from parser import _word_difficulty
    # של is among the most frequent Hebrew words; its Zipf freq must register
    assert _word_difficulty("של", "he") < 1.0


# --------------------------------------------------------------------------- #
# P7.4: grade_longest_passage — O(n) prefix-sum + monotone deque
# --------------------------------------------------------------------------- #

def test_longest_span_empty_input_returns_none():
    from parser import longest_span
    assert longest_span([], [], 0.95) is None


def test_longest_span_direct():
    from parser import longest_span
    # verses: 2/2, 0/3, 2/2, 2/2 — longest qualifying span is indices [2, 4)
    assert longest_span([2, 0, 2, 2], [2, 3, 2, 2], 0.95) == (2, 4)


def test_longest_passage_all_known_returns_full_corpus():
    """When all words are known, the longest passage is the entire corpus."""
    df = pl.DataFrame({"verse": ["the cat sat", "on the mat"], "ref": ["a", "b"]})
    vocab = set(stem_tokens("the cat sat on mat"))
    result = grade_longest_passage(df, vocab, min_rate=0.95)
    assert result.height == 1
    assert result["n_verses"][0] == 2
    assert result["start_ref"][0] == "a"
    assert result["end_ref"][0] == "b"
    assert result["comprehension_rate"][0] == pytest.approx(1.0)


def test_longest_passage_nothing_known_returns_empty():
    """When nothing is known, no passage meets the threshold."""
    df = pl.DataFrame({"verse": ["cat dog bird"], "ref": ["a"]})
    vocab = set()
    result = grade_longest_passage(df, vocab, min_rate=0.95)
    assert result.height == 0


def test_longest_passage_alternating_finds_longest_run():
    """Corpus alternates known/unknown; result is the longest known run."""
    # Verses: fully known, unknown, fully known, fully known
    # The last two form the longest run
    vocab = set(stem_tokens("the cat"))
    df = pl.DataFrame({
        "verse": ["the cat", "dog bird fish", "the cat", "the cat"],
        "ref": ["a", "b", "c", "d"],
    })
    result = grade_longest_passage(df, vocab, min_rate=0.95)
    assert result.height == 1
    assert result["n_verses"][0] == 2
    assert result["start_ref"][0] == "c"
    assert result["end_ref"][0] == "d"


def test_longest_passage_empty_corpus_returns_empty():
    """Empty DataFrame returns empty result without errors."""
    df = pl.DataFrame({"verse": [], "ref": []}, schema={"verse": pl.Utf8, "ref": pl.Utf8})
    result = grade_longest_passage(df, set(), min_rate=0.95)
    assert result.height == 0


def test_longest_passage_hebrew_tokenization():
    """Hebrew passage scoring uses the he tokenizer (no stemming, niqqud stripped)."""
    # Two Hebrew words, both known
    vocab = set(tokenize_and_stem("שלום אהבה", "he"))
    df = pl.DataFrame({
        "verse": ["שָׁלוֹם אַהֲבָה", "כֶּלֶב"],  # shalom + love, then dog (unknown)
        "ref": ["a", "b"],
    })
    result = grade_longest_passage(df, vocab, min_rate=0.95, lang="he")
    assert result.height == 1
    # Only first verse is fully known
    assert result["n_verses"][0] == 1
    assert result["start_ref"][0] == "a"


def test_longest_passage_combined_rate_not_per_verse():
    """The threshold applies to the combined passage, not each verse individually.

    Verse a: 1/2 known (50%). Verse b: 1/2 known (50%).
    Combined: 2/4 = 50% — below 0.95, so no passage qualifies.
    But if vocab covers 3/4 tokens across both: 3/4 = 75%, still below 0.95.
    """
    vocab = set(stem_tokens("the"))  # only "the" known
    df = pl.DataFrame({
        "verse": ["the cat", "the dog"],  # 1/2 each; combined 2/4 = 50%
        "ref": ["a", "b"],
    })
    result = grade_longest_passage(df, vocab, min_rate=0.95)
    assert result.height == 0


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
