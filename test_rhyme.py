"""Gates for rhyme.py: each test is a claim the module could get wrong."""

import itertools
import os
import random
import subprocess
import sys

import pytest

import rhyme


def _have(corpus):
    try:
        from nltk.data import find
    except ImportError:
        return False
    for name in (f"corpora/{corpus}", f"corpora/{corpus}.zip"):
        try:
            find(name)
            return True
        except LookupError:
            pass
    return False


needs_cmu = pytest.mark.skipif(not _have("cmudict"), reason="nltk cmudict not downloaded")
needs_wordnet = pytest.mark.skipif(
    not all(map(_have, ("cmudict", "wordnet", "stopwords"))),
    reason="nltk cmudict/wordnet/stopwords not downloaded",
)


# --------------------------------------------------------------- phonetics

@needs_cmu
def test_unstressed_suffixes_do_not_rhyme():
    # The original draft's tail began at the last vowel of *any* stress, so
    # every -ers / -ed pair "rhymed". These are the Psalm 23 cases.
    assert not rhyme.rhymes("pastures", "waters")
    assert not rhyme.rhymes("shepherd", "sheltered")
    assert rhyme.rhymes("waters", "daughters")


@needs_cmu
def test_every_pronunciation_counts():
    # "want" is AA1 or AO1 in CMUDict; first-pronunciation-only misses one.
    assert rhyme.rhymes("want", "font")
    assert rhyme.rhymes("want", "haunt")


@needs_cmu
def test_identity_and_oov_are_not_rhymes():
    assert not rhyme.rhymes("lord", "LORD")
    assert rhyme.phonetic_distance("lord", "lord") == 1.0
    assert not rhyme.rhymes("zzqx", "zzqx")
    assert rhyme.phonetic_distance("zzqx", "lord") == 1.0


@needs_cmu
def test_distance_orders_rhyme_slant_and_miss():
    assert rhyme.phonetic_distance("name", "flame") == 0.0
    slant = rhyme.phonetic_distance("soul", "hold")
    assert 0.0 < slant < rhyme.phonetic_distance("soul", "waters") <= 1.0


def test_terminal_word_skips_punctuation():
    assert rhyme.terminal_word("for his name's sake.") == "sake"
    assert rhyme.terminal_word('he said, "Let there be light";') == "light"
    assert rhyme.terminal_word("...") == ""


def test_scheme_pairs():
    assert rhyme.scheme_pairs("AABB") == [(0, 1), (2, 3)]
    assert rhyme.scheme_pairs("ABAB") == [(0, 2), (1, 3)]
    assert rhyme.scheme_pairs("AAAA") == [(0, 1), (1, 2), (2, 3)]
    assert rhyme.scheme_pairs("XAXA") == [(1, 3)]


# ----------------------------------------------------------------- stitch

def _brute_force(corpora, scheme, gamma, penalties, default, distance):
    names = list(corpora)
    n = len(corpora[names[0]])
    k = len(scheme)
    pairs = rhyme.scheme_pairs(scheme)
    ends = {t: [rhyme.terminal_word(x) for x in corpora[t]] for t in names}
    best = float("inf")
    for path in itertools.product(names, repeat=n):
        cost = sum(
            gamma * rhyme._register(penalties, default, path[i], path[i + 1])
            for i in range(n - 1)
        )
        for s in range(0, n, k):
            for i, j in pairs:
                if s + j < n:
                    cost += distance(ends[path[s + i]][s + i], ends[path[s + j]][s + j])
        best = min(best, cost)
    return best


@pytest.mark.parametrize("scheme", ["AABB", "ABAB", "AAAA", "ABCB", "AAB"])
def test_stitch_is_exactly_optimal(scheme):
    # The DP must match exhaustive search, including non-adjacent schemes
    # (ABAB) that a first-order Viterbi over lines cannot express, and a
    # trailing partial strophe (7 lines).
    rng = random.Random(scheme)
    vocab = ["wa", "wb", "wc", "wd", "we"]
    table = {(a, b): rng.random() for a in vocab for b in vocab}

    def distance(a, b):
        return table[(a, b)]

    for _ in range(5):
        corpora = {t: [f"line {rng.choice(vocab)}" for _ in range(7)] for t in "xyz"}
        penalties = {("x", "y"): rng.random()}
        _, cost = rhyme.stitch(corpora, scheme, gamma=0.7, register_penalties=penalties,
                               default_penalty=0.4, distance=distance)
        want = _brute_force(corpora, scheme, 0.7, penalties, 0.4, distance)
        assert cost == pytest.approx(want)


@needs_cmu
def test_stitch_takes_the_rhyming_rendering():
    corpora = {
        "a": ["He is my light.", "He keeps me all the day."],
        "b": ["He is my lamp.", "He keeps me through the night."],
    }
    path, cost = rhyme.stitch(corpora, "AA", gamma=0.1)
    assert path == ["a", "b"]
    assert cost == pytest.approx(0.1 * 0.3)
    # A register penalty larger than the rhyme gain keeps one translation.
    path, _ = rhyme.stitch(corpora, "AA", gamma=5.0)
    assert len(set(path)) == 1


def test_stitch_rejects_misaligned_corpora():
    with pytest.raises(ValueError):
        rhyme.stitch({"a": ["x"], "b": ["x", "y"]})


# ------------------------------------------------------------- substitute

@needs_wordnet
def test_substitute_changes_only_the_final_word():
    lines = ["Sing praises to his name;", "His anger burns like fire."]
    new, edits = rhyme.substitute(lines, "AA")
    assert edits == [(1, "fire", "flame")]
    assert new == ["Sing praises to his name;", "His anger burns like flame."]
    assert rhyme.scheme_satisfaction(new, "AA") == (1, 1)


@needs_wordnet
def test_substitute_gate_can_veto():
    lines = ["Sing praises to his name;", "His anger burns like fire."]
    new, edits = rhyme.substitute(lines, "AA", accept=lambda line: False)
    assert new == lines and edits == []


@needs_wordnet
def test_synonym_filters_block_seen_failures():
    assert "pee" not in rhyme.synonyms("waters")
    assert "key" not in rhyme.synonyms("name")
    assert "diced" not in rhyme.synonyms("died")
    assert rhyme.synonyms("me") == ()  # stopword; WordNet would offer "maine"
    assert all(s.islower() for s in rhyme.synonyms("lord"))


@needs_wordnet
def test_synonyms_keep_regular_inflection():
    plural = rhyme.synonyms("fires")
    assert plural and all(s.endswith("s") for s in plural)


@needs_wordnet
def test_substitute_is_hash_seed_independent():
    code = ("import rhyme; print(rhyme.synonyms('soul'), rhyme.synonyms('waters'))")
    outs = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       check=True, cwd=os.path.dirname(os.path.abspath(rhyme.__file__)),
                       env={**os.environ, "PYTHONHASHSEED": seed}).stdout
        for seed in ("1", "2", "3")
    }
    assert len(outs) == 1


def test_replace_terminal_keeps_case_and_punctuation():
    assert rhyme.replace_terminal("the house of the LORD for ever.", "aye") == \
        "the house of the LORD for aye."
    assert rhyme.replace_terminal("praise the LORD!", "god") == "praise the GOD!"
    assert rhyme.replace_terminal('"Fire"', "flame") == '"Flame"'


# -------------------------------------------------------- rhyme classes

@needs_cmu
def test_laplacian_nullity_equals_rhyme_classes():
    # Spectral fact: nullity(L) = number of connected components. Two
    # independent computations must agree, including the non-transitive case
    # where "want" joins the AA-N-T and AO-N-T classes into one component.
    words = ["want", "font", "haunt", "name", "flame", "fire", "soul",
             "hold", "waters", "daughters", "lord"]
    classes = rhyme.rhyme_classes(words)
    lap = rhyme.laplacian(rhyme.rhyme_adjacency(words))
    assert all(sum(row) == 0 for row in lap)
    assert len(words) - rhyme.matrix_rank(lap) == len(classes)
    assert ["want", "font", "haunt"] in classes
    assert not rhyme.rhymes("font", "haunt")  # chained only through "want"


def test_matrix_rank_exact():
    assert rhyme.matrix_rank([[1, 2], [2, 4]]) == 1
    assert rhyme.matrix_rank([[1, -1, 0], [-1, 2, -1], [0, -1, 1]]) == 2


# -------------------------------------------------------------------- CLI

@needs_wordnet
def test_cli_stitch_and_substitute(tmp_path, capsys):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("He is my light. -- psalms 1:1\nHe keeps me all the day. -- psalms 1:2\n")
    b.write_text("He is my lamp. -- psalms 1:1\nHe keeps me through the night. -- psalms 1:2\n")
    rhyme.main(["stitch", "--bible", f"a={a}", "--bible", f"b={b}",
                "--ref", "psalms 1:", "--scheme", "AA", "--gamma", "0.1"])
    out = capsys.readouterr().out
    assert "# stitched: 1/1 scheme pairs rhyme" in out
    assert "[b] He keeps me through the night. -- psalms 1:2" in out
    rhyme.main(["substitute", "--bible", str(a), "--ref", "psalms", "--scheme", "AA"])
    assert "# before: 0/1" in capsys.readouterr().out
