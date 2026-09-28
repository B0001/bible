#!/usr/bin/env python3
"""Rhymed Bible text: two engines and a rhyme-class graph.

Neither engine produces a translation. Both produce a *rhymed composite* and
report how much of the requested rhyme scheme they actually achieved, so the
claim "this rhymes" is checked rather than assumed.

* **Stitching** (System 1, ``stitch``): given N aligned translations, choose one
  translation per verse so line endings fit a rhyme scheme (``AABB``, ``ABAB``,
  ...), with a penalty for jumping between translations of different eras.
  Solved *exactly*: strophes are independent except for the register penalty
  at their boundary, so a Viterbi pass over strophes (state = translation of
  the strophe's last line) with every N^k assignment enumerated inside a
  strophe is optimal for any scheme, not just adjacent-pair ones.
* **Substitution** (System 2, ``substitute``): one baseline text; only the
  line-final word changes, to a WordNet synonym that rhymes with its partner.
* **Rhyme classes** (``rhyme_classes`` / ``laplacian``): line endings as a
  graph, A_ij = 1 when two endings rhyme, L = D - A. The number of rhyme
  classes is the number of connected components, which equals the nullity of
  L; the test suite checks the union-find count against an exact-arithmetic
  rank of L.

Rhyme definition (the part the original draft got wrong): the rhyme tail runs
from the **last primary-stressed** vowel (CMUDict stress ``1``) to the end of
the word, stress digits removed. Taking the last vowel of *any* stress instead
makes every ``-ers``/``-ed`` word rhyme ("pastures"/"waters",
"shepherd"/"sheltered"). Every CMUDict pronunciation counts, so "want" (AA1 or
AO1) rhymes with both "font" and "haunt". Identical words are not rhymes.

Requires the NLTK ``cmudict`` corpus (and ``wordnet`` + ``stopwords`` for
substitution): ``python -m nltk.downloader cmudict wordnet stopwords``.
"""

import argparse
import itertools
import re
import sys
from collections.abc import Callable, Sequence
from fractions import Fraction
from functools import cache, lru_cache

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*")


# ---------------------------------------------------------------- phonetics

@lru_cache(maxsize=1)
def _cmu():
    from nltk.corpus import cmudict

    return cmudict.dict()


def terminal_word(line: str) -> str:
    """Last word of ``line`` (lowercased, possessive ``'s`` kept), or ``''``."""
    words = WORD_RE.findall(line)
    return words[-1].lower() if words else ""


def pronunciations(word: str) -> list[list[str]]:
    return _cmu().get(word.lower(), [])


def _tail(phones: Sequence[str]) -> tuple[str, ...]:
    """Phones from the last primary-stressed vowel on, stress digits stripped.

    Falls back to the last secondary-stressed, then the last vowel of any
    stress, for words CMUDict marks without a primary stress.
    """
    for stresses in ("1", "2", "012"):
        idx = [i for i, p in enumerate(phones) if p[-1] in stresses]
        if idx:
            return tuple(p.rstrip("012") for p in phones[idx[-1]:])
    return tuple(phones)


@cache
def rhyme_tails(word: str) -> frozenset[tuple[str, ...]]:
    """Every rhyme tail of ``word`` over its CMUDict pronunciations."""
    return frozenset(_tail(p) for p in pronunciations(word))


def rhymes(a: str, b: str) -> bool:
    """True when two distinct words share a rhyme tail."""
    a, b = a.lower(), b.lower()
    if not a or not b or a == b:
        return False
    return bool(rhyme_tails(a) & rhyme_tails(b))


def _edit(x: Sequence[str], y: Sequence[str]) -> int:
    prev = list(range(len(y) + 1))
    for i, xi in enumerate(x, 1):
        cur = [i]
        for j, yj in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (xi != yj)))
        prev = cur
    return prev[-1]


@cache
def phonetic_distance(a: str, b: str) -> float:
    """0 for a rhyme, 1 for no information, else normalized tail edit distance.

    Partial credit is what lets the optimizer prefer a slant rhyme
    ("soul"/"hold") over no rhyme at all. Identical words score 1: repeating
    a word is not rhyming it.
    """
    if rhymes(a, b):
        return 0.0
    ta, tb = rhyme_tails(a), rhyme_tails(b)
    if not ta or not tb or a.lower() == b.lower():
        return 1.0
    return min(_edit(x, y) / max(len(x), len(y)) for x in ta for y in tb)


# ------------------------------------------------------------------ schemes

def scheme_pairs(scheme: str) -> list[tuple[int, int]]:
    """Positions that must rhyme: each line with the previous line of its letter.

    ``AABB`` -> [(0, 1), (2, 3)]; ``ABAB`` -> [(0, 2), (1, 3)]; ``AAAA`` ->
    [(0, 1), (1, 2), (2, 3)]. ``X`` marks an unrhymed line (as in ``XAXA``).
    """
    last: dict[str, int] = {}
    pairs = []
    for i, ch in enumerate(scheme.upper()):
        if ch == "X":
            continue
        if ch in last:
            pairs.append((last[ch], i))
        last[ch] = i
    return pairs


def scheme_satisfaction(lines: Sequence[str], scheme: str) -> tuple[int, int]:
    """(rhyming pairs, required pairs) of ``lines`` under a repeating ``scheme``."""
    k = len(scheme)
    pairs = scheme_pairs(scheme)
    hit = total = 0
    for start in range(0, len(lines), k):
        block = lines[start:start + k]
        for i, j in pairs:
            if j < len(block):
                total += 1
                hit += rhymes(terminal_word(block[i]), terminal_word(block[j]))
    return hit, total


# ----------------------------------------------------------- System 1: stitch

def _register(penalties, default, a, b):
    if a == b:
        return 0.0
    return penalties.get((a, b), penalties.get((b, a), default))


def stitch(
    corpora: dict[str, Sequence[str]],
    scheme: str = "AABB",
    alpha: float = 1.0,
    gamma: float = 0.5,
    register_penalties: dict[tuple[str, str], float] | None = None,
    default_penalty: float = 0.3,
    distance: Callable[[str, str], float] = phonetic_distance,
) -> tuple[list[str], float]:
    """Pick one translation per line minimizing rhyme + register cost exactly.

    ``corpora`` maps translation name -> aligned lines (same length). Returns
    the chosen translation name per line and the minimal total cost. Cost is
    ``alpha * sum(distance over scheme pairs) + gamma * sum(register penalty
    over adjacent lines)``; ties break toward earlier translations.
    """
    names = list(corpora)
    n_lines = len(corpora[names[0]])
    if any(len(v) != n_lines for v in corpora.values()):
        raise ValueError("corpora must be aligned to the same number of lines")
    if n_lines == 0:
        return [], 0.0
    penalties = register_penalties or {}
    ends = {t: [terminal_word(x) for x in corpora[t]] for t in names}
    k = len(scheme)
    pairs = scheme_pairs(scheme)

    def reg(a, b):
        return _register(penalties, default_penalty, a, b)

    # best[t] = (cost, path) over complete strophes, last line rendered by t.
    best: dict[str | None, tuple[float, list[str]]] = {None: (0.0, [])}
    for start in range(0, n_lines, k):
        size = min(k, n_lines - start)
        inner = [(i, j) for i, j in pairs if j < size]
        nxt: dict[str | None, tuple[float, list[str]]] = {}
        for combo in itertools.product(names, repeat=size):
            local = sum(
                alpha * distance(ends[combo[i]][start + i], ends[combo[j]][start + j])
                for i, j in inner
            ) + sum(gamma * reg(combo[i], combo[i + 1]) for i in range(size - 1))
            for prev, (cost, path) in best.items():
                total = cost + local + (gamma * reg(prev, combo[0]) if prev else 0.0)
                if combo[-1] not in nxt or total < nxt[combo[-1]][0] - 1e-12:
                    nxt[combo[-1]] = (total, path + list(combo))
        best = nxt
    cost, path = min(best.values(), key=lambda cp: cp[0])
    return path, cost


# ------------------------------------------------------- System 2: substitute

# Alternative spellings of the same inflection: "church" + es, "hope" + d.
_SUFFIX_CLASS = {"s": ("s", "es"), "es": ("es", "s"), "d": ("d", "ed"), "ed": ("ed", "d")}


@lru_cache(maxsize=1)
def _stopwords() -> frozenset[str]:
    # No LookupError fallback: an empty set would silently switch the filter
    # off (it did, once, and changed the measured edit count).
    from nltk.corpus import stopwords

    return frozenset(stopwords.words("english"))


@cache
def synonyms(word: str, max_senses: int = 3, min_count: int = 1) -> tuple[str, ...]:
    """Single-word WordNet synonyms of ``word``, most common sense first.

    Each filter here answers a failure seen on the full KJV (see
    PHASE16_DESIGN.md):

    * only the ``max_senses`` most frequent synsets, and only lemmas attested
      ``min_count`` times in WordNet's sense-tagged corpus -- rare senses gave
      "waters" -> "pee", "name" -> "key", "wrath" -> "ira";
    * no proper-noun lemmas ("me" -> "Maine") and no stopwords as the word
      being replaced ("also" -> "too", "are" -> "be");
    * a synset only counts if ``word`` inflects from it in the synset's own
      part of speech, and the synonym takes the same regular suffix or is
      dropped -- otherwise "died" (verb) picked up the noun "dice" and became
      "diced". Irregular forms ("made") have no suffix to carry, so they get
      no synonyms rather than a wrong tense.

    Deterministic: WordNet orders synsets by sense frequency; lemmas within a
    synset are sorted by tagged-corpus count. (A ``set``, as in the original
    draft, makes the chosen substitute depend on PYTHONHASHSEED.)
    """
    from nltk.corpus import wordnet as wn

    word = word.lower()
    if word in _stopwords():
        return ()
    out: list[str] = []
    for syn in wn.synsets(word)[:max_senses]:
        base = wn.morphy(word, syn.pos()) or word
        if word == base:
            endings: tuple[str, ...] = ("",)
        elif word.startswith(base) and word[len(base):] in _SUFFIX_CLASS:
            endings = _SUFFIX_CLASS[word[len(base):]]
        else:
            continue
        for lem in sorted(syn.lemmas(), key=lambda lem: -lem.count()):
            name = lem.name()
            if not name.isalpha() or name[0].isupper() or lem.count() < min_count:
                continue
            cand = next((name + e for e in endings if pronunciations(name + e)), "")
            if cand and cand not in (word, base) and cand not in out:
                out.append(cand)
    return tuple(out)


def replace_terminal(line: str, new: str) -> str:
    """Swap the last word of ``line`` for ``new``, keeping case and punctuation."""
    matches = list(WORD_RE.finditer(line))
    if not matches:
        return line
    m = matches[-1]
    old = m.group()
    if old.isupper() and len(old) > 1:
        new = new.upper()
    elif old[0].isupper():
        new = new[0].upper() + new[1:]
    return line[:m.start()] + new + line[m.end():]


def substitute(
    lines: Sequence[str],
    scheme: str = "AABB",
    accept: Callable[[str], bool] | None = None,
) -> tuple[list[str], list[tuple[int, str, str]]]:
    """Make scheme pairs rhyme by swapping one line-final word for a synonym.

    For each unrhymed pair (i, j) the later line's ending is replaced with its
    first synonym that rhymes with line i; failing that, line i's ending with
    one rhyming line j (only if line i is not already anchoring an earlier
    rhyme). ``accept`` is the optional validation gate (e.g. an LM perplexity
    threshold); a rejected candidate falls through to the next one. Returns the
    new lines and the list of ``(line index, old word, new word)`` edits.
    """
    out = list(lines)
    edits: list[tuple[int, str, str]] = []
    k = len(scheme)
    pairs = scheme_pairs(scheme)
    for start in range(0, len(out), k):
        locked: set[int] = set()
        for pi, pj in pairs:
            i, j = start + pi, start + pj
            if j >= len(out):
                continue
            wi, wj = terminal_word(out[i]), terminal_word(out[j])
            if rhymes(wi, wj):
                locked |= {i, j}
                continue
            order = [(j, wj, wi)] + ([] if i in locked else [(i, wi, wj)])
            for idx, word, anchor in order:
                done = False
                for cand in synonyms(word):
                    if not rhymes(cand, anchor):
                        continue
                    new_line = replace_terminal(out[idx], cand)
                    if accept is None or accept(new_line):
                        out[idx] = new_line
                        edits.append((idx, word, cand))
                        done = True
                        break
                if done:
                    locked |= {i, j}
                    break
    return out, edits


# ------------------------------------------------------ rhyme-class graph

def rhyme_adjacency(words: Sequence[str]) -> list[list[int]]:
    return [[int(i != j and rhymes(a, b)) for j, b in enumerate(words)]
            for i, a in enumerate(words)]


def laplacian(adj: Sequence[Sequence[int]]) -> list[list[int]]:
    """L = D - A for a symmetric 0/1 adjacency matrix."""
    return [[(sum(row) if i == j else 0) - row[j] for j in range(len(row))]
            for i, row in enumerate(adj)]


def matrix_rank(m: Sequence[Sequence[int]]) -> int:
    """Exact rank by Gaussian elimination over the rationals."""
    rows = [[Fraction(x) for x in r] for r in m]
    rank = 0
    ncols = len(rows[0]) if rows else 0
    for c in range(ncols):
        piv = next((r for r in range(rank, len(rows)) if rows[r][c] != 0), None)
        if piv is None:
            continue
        rows[rank], rows[piv] = rows[piv], rows[rank]
        for r in range(len(rows)):
            if r != rank and rows[r][c] != 0:
                f = rows[r][c] / rows[rank][c]
                rows[r] = [a - f * b for a, b in zip(rows[r], rows[rank], strict=True)]
        rank += 1
    return rank


def rhyme_classes(words: Sequence[str]) -> list[list[str]]:
    """Connected components of the rhyme graph (union-find), first-seen order.

    With several pronunciations per word rhyme is not transitive, so a class
    can chain words that do not rhyme pairwise; that is the component, not a
    bug. ``len(rhyme_classes(w)) == n - matrix_rank(laplacian(adj))``.
    """
    parent = list(range(len(words)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in itertools.combinations(range(len(words)), 2):
        if rhymes(words[i], words[j]):
            parent[find(i)] = find(j)
    groups: dict[int, list[str]] = {}
    for i, w in enumerate(words):
        groups.setdefault(find(i), []).append(w)
    return list(groups.values())


# ---------------------------------------------------------------------- CLI

def load_passage(path: str, ref_prefix: str) -> list[tuple[str, str]]:
    """(ref, verse) rows whose ref starts with ``ref_prefix`` (case-insensitive)."""
    rows = []
    with open(path) as f:
        for line in f:
            verse, sep, ref = line.rstrip("\n").partition(" -- ")
            if sep and ref.lower().startswith(ref_prefix.lower()):
                rows.append((ref, verse))
    return rows


def _report(lines, scheme):
    hit, total = scheme_satisfaction(lines, scheme)
    return f"{hit}/{total} scheme pairs rhyme"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("stitch", help="System 1: pick a translation per verse")
    s1.add_argument("--bible", action="append", required=True, metavar="NAME=PATH")
    s1.add_argument("--penalty", action="append", default=[], metavar="A:B=X",
                    help="register penalty for switching between A and B")
    s1.add_argument("--default-penalty", type=float, default=0.3)
    s1.add_argument("--gamma", type=float, default=0.5)
    s2 = sub.add_parser("substitute", help="System 2: synonym swaps in one text")
    s2.add_argument("--bible", required=True)
    for p in (s1, s2):
        p.add_argument("--ref", required=True, help='ref prefix, e.g. "psalms 23:"')
        p.add_argument("--scheme", default="AABB")
    args = ap.parse_args(argv)

    if args.cmd == "stitch":
        passages = {}
        for spec in args.bible:
            name, _, path = spec.partition("=")
            passages[name] = load_passage(path, args.ref)
        refs = [r for r, _ in next(iter(passages.values()))]
        if not refs:
            sys.exit(f"no verses match {args.ref!r}")
        for name, rows in passages.items():
            if [r for r, _ in rows] != refs:
                sys.exit(f"{name}: refs do not align with the first --bible")
        corpora = {n: [v for _, v in rows] for n, rows in passages.items()}
        penalties = {}
        for spec in args.penalty:
            pair, _, val = spec.partition("=")
            a, _, b = pair.partition(":")
            penalties[(a, b)] = float(val)
        path, cost = stitch(corpora, args.scheme, gamma=args.gamma,
                            register_penalties=penalties,
                            default_penalty=args.default_penalty)
        for n, lines in corpora.items():
            print(f"# {n} alone: {_report(lines, args.scheme)}")
        chosen = [corpora[t][i] for i, t in enumerate(path)]
        print(f"# stitched: {_report(chosen, args.scheme)}, cost {cost:.3f}")
        for ref, t, line in zip(refs, path, chosen, strict=True):
            print(f"[{t}] {line} -- {ref}")
    else:
        rows = load_passage(args.bible, args.ref)
        if not rows:
            sys.exit(f"no verses match {args.ref!r}")
        lines = [v for _, v in rows]
        new, edits = substitute(lines, args.scheme)
        print(f"# before: {_report(lines, args.scheme)}")
        print(f"# after:  {_report(new, args.scheme)}, {len(edits)} word(s) swapped")
        for idx, old, cand in edits:
            print(f"#   {rows[idx][0]}: {old} -> {cand}")
        for (ref, _), line in zip(rows, new, strict=True):
            print(f"{line} -- {ref}")


if __name__ == "__main__":
    main()
