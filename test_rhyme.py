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


# ---------------------------------------------------- round 2: lineate etc.

@needs_cmu
def test_reduced_pronunciations_do_not_rhyme():
    # "them" has a reduced DH AH0 M form; its unstressed vowel made "come"/
    # "them" the second-commonest rhyme in the lineated KJV.
    assert not rhyme.rhymes("come", "them")
    assert rhyme.rhymes("them", "hem")


@needs_wordnet
def test_content_rhymes_exclude_function_words():
    assert rhyme.rhymes("me", "thee") and not rhyme.content_rhymes("me", "thee")
    assert rhyme.content_rhymes("rams", "lambs")
    assert rhyme.content_distance("me", "thee") == 1.0


def test_stitch_skips_blank_renderings():
    corpora = {"a": ["", "x y"], "b": ["some words", "x z"]}
    path, _ = rhyme.stitch(corpora, "AA", distance=lambda a, b: 0.0)
    assert path[0] == "b"


def test_stitch_scales_linearly():
    # 8 translations x 400 lines under ABAB must finish fast; the per-strophe
    # enumeration it replaced was N^k per strophe.
    import time

    corpora = {f"t{n}": [f"line {'abcdefgh'[(n + i) % 8]}" for i in range(400)] for n in range(8)}
    t0 = time.time()
    rhyme.stitch(corpora, "ABAB", distance=lambda a, b: float(a != b))
    assert time.time() - t0 < 10


def _lineate_brute(verses, scheme, lo, hi, alpha, beta, distance, break_before=frozenset()):
    # Break points computed independently of rhyme.break_after.
    tokens, breaks = [], set()
    for v in verses:
        words = v.split()
        for j, tok in enumerate(words):
            tokens.append(tok)
            nxt = words[j + 1] if j + 1 < len(words) else None
            if rhyme.CLAUSE_END.search(tok) or nxt is None or nxt in break_before:
                breaks.add(len(tokens))
    order = sorted(breaks)
    target = (lo + hi) / 2
    pairs = rhyme.scheme_pairs(scheme)
    best = float("inf")

    def walk(p, cuts):
        nonlocal best
        if p == len(tokens):
            ends = [rhyme.terminal_word(tokens[q - 1]) for q in cuts]
            lens = [b - a for a, b in itertools.pairwise([0, *cuts])]
            cost = sum(beta * abs(n - target) / target for n in lens)
            for s in range(0, len(cuts), len(scheme)):
                for i, j in pairs:
                    if s + j < len(cuts):
                        cost += alpha * (distance(ends[s + i], ends[s + j]) - 1)
            best = min(best, cost)
            return
        cands = [q for q in order if p < q <= p + hi] or [min(q for q in order if q > p)]
        for q in cands:
            walk(q, [*cuts, q])

    walk(0, [])
    return best


@pytest.mark.parametrize("break_before", [frozenset(), frozenset({"wb"})])
@pytest.mark.parametrize("scheme", ["AABB", "ABAB", "XAXA"])
def test_lineate_is_exactly_optimal(scheme, break_before):
    rng = random.Random(scheme)
    vocab = ["wa", "wb", "wc", "wd"]
    table = {(a, b): rng.random() for a in vocab for b in vocab}

    def distance(a, b):
        return table[(a, b)]

    for _ in range(4):
        verses = [" ".join(rng.choice(vocab) + rng.choice(["", "", ","]) for _ in range(6))
                  for _ in range(3)]
        _, cost = rhyme.lineate(verses, scheme, lo=1, hi=4, distance=distance,
                                break_before=break_before)
        want = _lineate_brute(verses, scheme, 1, 4, 1.0, 0.05, distance, break_before)
        assert cost == pytest.approx(want)


@needs_cmu
def test_lineate_keeps_every_word_and_finds_the_rhyme():
    verses = [
        "The mountains skipped like rams, and the little hills like lambs.",
        "What ailed thee, O thou sea, that thou fleddest?",
    ]
    lines, _ = rhyme.lineate(verses, "AA", lo=2, hi=8)
    assert " ".join(lines).split() == " ".join(verses).split()
    assert lines[:2] == ["The mountains skipped like rams,", "and the little hills like lambs."]
    assert rhyme.scheme_satisfaction(verses, "AA") == (0, 1)


# ------------------------------------------- joint stitch + lineate (round 3)

def _joint_brute(corpora, scheme, gamma, penalties, default, distance,
                 break_before=frozenset()):
    names = list(corpora)
    n = len(corpora[names[0]])
    best = float("inf")
    for path in itertools.product(names, repeat=n):
        reg = sum(gamma * rhyme._register(penalties, default, a, b)
                  for a, b in itertools.pairwise(path))
        text = [corpora[t][v] for v, t in enumerate(path)]
        _, cost = rhyme.lineate(text, scheme, lo=1, hi=4, distance=distance,
                                break_before=break_before)
        best = min(best, reg + cost)
    return best


@pytest.mark.parametrize("break_before", [frozenset(), frozenset({"wb"})])
@pytest.mark.parametrize("scheme", ["AABB", "ABAB", "XAXA"])
def test_stitch_lineate_is_exactly_optimal(scheme, break_before):
    # The joint DP must equal: min over every translation assignment of its
    # register penalty + lineate() on the text it selects. Lines may cross
    # verse boundaries, and some verses exceed hi with no clause break.
    rng = random.Random("joint" + scheme)
    vocab = ["wa", "wb", "wc", "wd"]
    table = {(a, b): rng.random() for a in vocab for b in vocab}

    def distance(a, b):
        return table[(a, b)]

    for _ in range(3):
        corpora = {
            t: [" ".join(rng.choice(vocab) + rng.choice(["", "", "", ","])
                         for _ in range(rng.randint(1, 6))) for _ in range(4)]
            for t in "xyz"
        }
        penalties = {("x", "y"): rng.random()}
        lines, path, cost = rhyme.stitch_lineate(
            corpora, scheme, lo=1, hi=4, gamma=0.3, register_penalties=penalties,
            default_penalty=0.2, distance=distance, break_before=break_before)
        assert cost == pytest.approx(
            _joint_brute(corpora, scheme, 0.3, penalties, 0.2, distance, break_before))
        chosen = [corpora[t][v] for v, t in enumerate(path)]
        assert " ".join(lines).split() == " ".join(chosen).split()


def test_stitch_lineate_with_one_translation_is_lineate():
    rng = random.Random(7)
    vocab = ["wa", "wb", "wc"]

    def distance(a, b):
        return float(a == b)

    verses = [" ".join(rng.choice(vocab) + rng.choice(["", ","]) for _ in range(5))
              for _ in range(6)]
    lines, path, cost = rhyme.stitch_lineate({"only": verses}, "ABAB", lo=1, hi=4,
                                             distance=distance)
    want_lines, want_cost = rhyme.lineate(verses, "ABAB", lo=1, hi=4, distance=distance)
    assert cost == pytest.approx(want_cost)
    assert " ".join(lines).split() == " ".join(want_lines).split()
    assert set(path) == {"only"}


def test_stitch_lineate_skips_blank_verses():
    corpora = {"a": ["x y,", "", "z w."], "b": ["x q,", "", "z v."]}
    lines, _, _ = rhyme.stitch_lineate(corpora, "AA", lo=1, hi=4,
                                          distance=lambda a, b: 0.0)
    assert " ".join(lines).split()[0] == "x"
    assert len(" ".join(lines).split()) == 4


@needs_cmu
def test_stitch_lineate_beats_either_alone():
    corpora = {
        "a": ["The mountains skipped like rams, and the little hills rejoiced.",
              "What ails you, O sea, that you flee?"],
        "b": ["The hills leapt like rams, and the little hills like lambs.",
              "What ailed thee, O thou sea, that thou fleddest?"],
    }
    lines, path, cost = rhyme.stitch_lineate(corpora, "AABB", lo=2, hi=10)
    for t in corpora:
        assert cost <= rhyme.lineate(corpora[t], "AABB", lo=2, hi=10)[1] + 1e-9
    assert rhyme.scheme_satisfaction(lines, "AABB") == (2, 2)
    assert path == ["b", "a"]


def test_stitch_lineate_short_verses_stay_polynomial():
    # Runs of very short verses let one line cross many verse boundaries;
    # enumerating whole lines branched N ways per crossing (1 Chronicles 1
    # took 8 s). 8 translations x 120 one-word verses, hi = 16.
    import time

    corpora = {f"t{n}": [f"w{'abcdefgh'[(n * v) % 8]}." for v in range(120)] for n in range(8)}
    t0 = time.time()
    lines, _, _ = rhyme.stitch_lineate(corpora, "AABB", distance=lambda a, b: float(a == b))
    assert time.time() - t0 < 30  # ~5 s here; exponential would be hours
    assert len(" ".join(lines).split()) == 120


@needs_cmu
def test_conjunction_breaks_find_rhymes_punctuation_hides():
    # No punctuation between "night" and "and": only a conjunction break
    # lets "delight" / "night" end lines.
    verses = ["the law is his delight and he meditates by day and by night and he is blessed"]
    plain, _ = rhyme.lineate(verses, "AA", lo=2, hi=8)
    conj, _ = rhyme.lineate(verses, "AA", lo=2, hi=8, break_before=rhyme.CONJUNCTIONS)
    assert rhyme.scheme_satisfaction(plain, "AA")[0] == 0  # one unbreakable line
    assert conj[:2] == ["the law is his delight", "and he meditates by day and by night"]
    assert " ".join(conj).split() == verses[0].split()


def test_break_after_rules():
    words = ["he", "rose,", "and", "went", "up"]
    assert rhyme.break_after(words, 1)                       # "rose,"
    assert not rhyme.break_after(words, 2)                   # "and" -> "went"
    assert rhyme.break_after(words, 4)                       # verse end
    assert not rhyme.break_after(words, 0)
    assert rhyme.break_after(["he", "went", "and", "came"], 1, rhyme.CONJUNCTIONS)


@needs_wordnet
def test_exact_distances_give_no_partial_credit():
    assert rhyme.exact_distance("name", "flame") == 0.0
    assert rhyme.exact_distance("soul", "hold") == 1.0  # slant: 1/3 under phonetic_distance
    assert rhyme.content_exact_distance("me", "thee") == 1.0
    assert rhyme.content_exact_distance("rams", "lambs") == 0.0


@needs_wordnet
def test_cli_joint_conj(tmp_path, capsys):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("The law is his delight and he thinks on it by day and by night. -- psalms 1:2\n")
    b.write_text("His pleasure is in the law and he muses on it day and night. -- psalms 1:2\n")
    rhyme.main(["joint", "--bible", f"a={a}", "--bible", f"b={b}", "--ref", "psalms",
                "--scheme", "AA", "--content", "--conj", "--lo", "2", "--hi", "10"])
    out = capsys.readouterr().out
    assert "# joint: 1/1 scheme pairs rhyme (1 on content words)" in out
    assert "The law is his delight\n" in out
