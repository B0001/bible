#!/usr/bin/env python3
"""Rhymed Bible text: four engines and a rhyme-class graph.

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
* **Lineation** (``lineate``): one text, no words changed; choose the line
  breaks (at clause punctuation) so line endings fit the scheme. The most
  productive of the three on the full KJV -- see PHASE16_DESIGN.md.
* **Joint** (``stitch_lineate``): translation per verse and line breaks in
  one exact DP; with 8 translations it finds 2.8x the content rhymes of the
  two engines above combined.
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
import heapq
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
    """Every rhyme tail of ``word`` over its CMUDict pronunciations.

    Pronunciations with no primary stress are ignored when the word has one
    that does: those are reduced function-word forms ("them" as DH AH0 M),
    and their unstressed vowel made "come"/"them" the second most common
    rhyme in the lineated KJV.
    """
    prons = pronunciations(word)
    stressed = [p for p in prons if any(ph.endswith("1") for ph in p)]
    return frozenset(_tail(p) for p in (stressed or prons))


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


def content_rhymes(a: str, b: str) -> bool:
    """A rhyme between two non-stopwords: "sword"/"lord", not "me"/"thee"."""
    stop = _stopwords() | ARCHAIC_STOPWORDS
    return a.lower() not in stop and b.lower() not in stop and rhymes(a, b)


def content_distance(a: str, b: str) -> float:
    """``phonetic_distance`` with stopword endings scored as no rhyme (1)."""
    stop = _stopwords() | ARCHAIC_STOPWORDS
    if a.lower() in stop or b.lower() in stop:
        return 1.0
    return phonetic_distance(a, b)


def exact_distance(a: str, b: str) -> float:
    """0 for a rhyme, else 1: no partial credit for near-rhymes."""
    return 0.0 if rhymes(a, b) else 1.0


def content_exact_distance(a: str, b: str) -> float:
    """0 for a content-word rhyme, else 1."""
    return 0.0 if content_rhymes(a, b) else 1.0


def scheme_satisfaction(
    lines: Sequence[str], scheme: str, rhyme_test: Callable[[str, str], bool] = rhymes
) -> tuple[int, int]:
    """(rhyming pairs, required pairs) of ``lines`` under a repeating ``scheme``.

    Pass ``rhyme_test=content_rhymes`` to count only content-word rhymes.
    """
    k = len(scheme)
    pairs = scheme_pairs(scheme)
    hit = total = 0
    for start in range(0, len(lines), k):
        block = lines[start:start + k]
        for i, j in pairs:
            if j < len(block):
                total += 1
                hit += rhyme_test(terminal_word(block[i]), terminal_word(block[j]))
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

    # Line-level DP. After line i of a strophe the state is the translation of
    # line i plus the translations of earlier lines some later line of the
    # strophe must rhyme with ("open anchors"): N states for AABB, N^2 for
    # ABAB. Exact, and linear in the number of lines -- enumerating N^k
    # assignments per strophe stopped scaling at N = 8.
    def open_after(i, size):
        return tuple(a for a in range(i + 1) if any(p == a and i < q < size for p, q in pairs))

    State = tuple  # (translation of this line, *translations of open anchors)
    layer: dict[State, float] = {}
    back: list[dict[State, tuple[State | None, str]]] = []
    for line in range(n_lines):
        start, i = divmod(line, k)
        start *= k
        size = min(k, n_lines - start)
        choices = [t for t in names if corpora[t][line].strip()] or names
        anchors_now = open_after(i, size)
        prev_open = open_after(i - 1, size) if i else ()
        nxt: dict[State, float] = {}
        ptr: dict[State, tuple[State | None, str]] = {}
        for prev, cost in (layer.items() if line else [((), 0.0)]):
            held = dict(zip(prev_open, prev[1:], strict=True)) if i else {}
            if i:
                held[i - 1] = prev[0]
            for t in choices:
                c = cost
                if line:
                    c += gamma * reg(prev[0], t)
                for a, b in pairs:
                    if b == i and b < size:
                        c += alpha * distance(ends[held[a]][start + a], ends[t][line])
                held_t = {**held, i: t}
                state = (t, *(held_t[a] for a in anchors_now))
                if state not in nxt or c < nxt[state] - 1e-12:
                    nxt[state] = c
                    ptr[state] = (prev if line else None, t)
        layer = nxt
        back.append(ptr)
    state = min(layer, key=layer.__getitem__)
    cost = layer[state]
    path = []
    for ptr in reversed(back):
        prev, t = ptr[state]
        path.append(t)
        state = prev
    return path[::-1], cost


# ------------------------------------------------------ lineation (words fixed)

CLAUSE_END = re.compile(r"[,;:.!?][\"')\]]*$")

# Opt-in extra break points: a line may also end just *before* one of these.
# Coordinating conjunctions plus the subordinators that open most clauses in
# the English translations measured here.
CONJUNCTIONS = frozenset(
    ["and", "but", "for", "or", "nor", "yet", "that", "which", "who", "when", "because"]
)


def break_after(words: Sequence[str], j: int, break_before: frozenset[str] = frozenset()) -> bool:
    """May a line end after ``words[j]``? Verse end, clause punctuation, or
    the next word is in ``break_before``."""
    if j == len(words) - 1 or CLAUSE_END.search(words[j]):
        return True
    return bool(break_before) and words[j + 1].lower().strip("\"'(`") in break_before


def lineate(
    verses: Sequence[str],
    scheme: str = "AABB",
    lo: int = 4,
    hi: int = 16,
    alpha: float = 1.0,
    beta: float = 0.05,
    distance: Callable[[str, str], float] = phonetic_distance,
    break_before: frozenset[str] = frozenset(),
    min_words: int = 1,
) -> tuple[list[str], float]:
    """Re-break a passage into lines so line endings fit ``scheme``; no word changes.

    Stitching and substitution both change *which words* are said. This keeps
    one text verbatim -- ``" ".join(lines).split() == " ".join(verses).split()``
    -- and only chooses where lines end. Breaks are allowed after clause
    punctuation (``, ; : . ! ?``), at verse ends, and before any word in
    ``break_before`` (e.g. ``CONJUNCTIONS``; empty by default). Each scheme pair scores
    ``alpha * (distance - 1)`` (a rhyme is a reward of ``alpha``, so the
    optimizer is not paid to make fewer, longer lines to dodge pairs); each
    line costs ``beta * |words - target| / target`` with target the midpoint of
    ``lo..hi``. ``min_words`` is a hard floor (default 1, i.e. none): a break
    fewer than ``min_words`` words into a line is not a legal end, except at
    the end of the text. A line may exceed ``hi`` only when no legal break
    comes sooner.

    Exact DP over (words consumed, line index in strophe, end positions of the
    strophe's open rhyme anchors). Returns the lines and the minimal cost.
    """
    if not 1 <= min_words <= hi:
        raise ValueError(f"need 1 <= min_words <= hi, got {min_words}, {hi}")
    tokens: list[str] = []
    breaks: set[int] = set()
    for verse in verses:
        words = verse.split()
        for j, tok in enumerate(words):
            tokens.append(tok)
            if break_after(words, j, break_before):
                breaks.add(len(tokens))
    n_tok = len(tokens)
    if not n_tok:
        return [], 0.0
    ends = [terminal_word(t) for t in tokens]
    order = sorted(breaks)
    k = len(scheme)
    pairs = scheme_pairs(scheme)
    target = (lo + hi) / 2
    keep = {i: tuple(a for a in range(i + 1) if any(p == a and q > i for p, q in pairs))
            for i in range(k)}

    State = tuple  # (tokens consumed, next line index, *end token of each open anchor)
    start: State = (0, 0)
    best: dict[State, float] = {start: 0.0}
    back: dict[State, State] = {}
    # States only move forward in tokens, so process them in token order.
    frontier = {0: [start]}
    for p in [0, *order[:-1]]:
        for state in frontier.pop(p, []):
            cost = best[state]
            i = state[1]
            held = dict(zip(keep[i - 1] if i else (), state[2:], strict=True))
            if n_tok - p < min_words:
                cands = [n_tok]  # the text's last line may be short
            else:
                cands = [q for q in order if p + min_words <= q <= p + hi] or \
                    [min(q for q in order if q >= p + min_words)]
            for q in cands:
                c = cost + beta * abs((q - p) - target) / target
                end = q - 1
                for a, b in pairs:
                    if b == i:
                        c += alpha * (distance(ends[held[a]], ends[end]) - 1)
                held_q = {**held, i: end}
                ni = (i + 1) % k
                nxt = (q, ni, *((held_q[a] for a in keep[i]) if ni else ()))
                if nxt not in best or c < best[nxt] - 1e-12:
                    if nxt not in best:
                        frontier.setdefault(q, []).append(nxt)
                    best[nxt] = c
                    back[nxt] = state
    final = min((s for s in best if s[0] == n_tok), key=best.__getitem__)
    cost = best[final]
    cuts = []
    s = final
    while s != start:
        cuts.append(s[0])
        s = back[s]
    cuts = [0, *reversed(cuts)]
    return [" ".join(tokens[a:b]) for a, b in itertools.pairwise(cuts)], cost


# ------------------------------------- joint: translation per verse + breaks

def stitch_lineate(
    corpora: dict[str, Sequence[str]],
    scheme: str = "AABB",
    lo: int = 4,
    hi: int = 16,
    alpha: float = 1.0,
    beta: float = 0.05,
    gamma: float = 0.0,
    register_penalties: dict[tuple[str, str], float] | None = None,
    default_penalty: float = 0.3,
    distance: Callable[[str, str], float] = phonetic_distance,
    break_before: frozenset[str] = frozenset(),
    min_words: int = 1,
) -> tuple[list[str], list[str], float]:
    """Choose each verse's translation *and* the line breaks, jointly and exactly.

    The objective is ``lineate``'s (rhyme reward, line-length term) plus
    ``stitch``'s register penalty between consecutive verses' translations;
    ``break_before`` and ``min_words`` act exactly as in ``lineate``.
    Every verse appears verbatim in exactly one translation, and a line may run
    across a verse boundary, so one line can join two translations. Returns
    ``(lines, translation per verse, cost)``; the cost equals the minimum,
    over every translation assignment, of that assignment's register penalty
    plus ``lineate`` on the text it selects (the tests check exactly this).

    DP over two kinds of point, processed in text order: line boundaries
    inside a verse (translation fixed), and verse starts (translation not yet
    chosen), which may fall mid-line. A state carries the point, the previous
    verse's translation (register term), the words already in the current
    line and whether it has passed a legal break (for the length rule), the
    line index in the strophe, and the end *words* of open rhyme anchors.
    Each transition walks within one verse only. Enumerating whole lines
    instead branches N ways at every verse a line crosses, and that was
    exponential on runs of short verses: 1 Chronicles 1, 8 translations, 8 s.
    """
    if not 1 <= min_words <= hi:
        raise ValueError(f"need 1 <= min_words <= hi, got {min_words}, {hi}")
    names = list(corpora)
    n_verses = len(corpora[names[0]])
    if any(len(v) != n_verses for v in corpora.values()):
        raise ValueError("corpora must be aligned to the same number of lines")
    penalties = register_penalties or {}
    toks = {t: [v.split() for v in corpora[t]] for t in names}
    choices = [[t for t in names if toks[t][v]] for v in range(n_verses)]
    k = len(scheme)
    pairs = scheme_pairs(scheme)
    target = (lo + hi) / 2
    keep = {i: tuple(a for a in range(i + 1) if any(p == a and q > i for p, q in pairs))
            for i in range(k)}

    def breakable(t, v, o):  # may a line end after o tokens of verse v in t?
        return break_after(toks[t][v], o - 1, break_before)

    def verse_start(v):  # next verse at or after v that is not blank everywhere
        while v < n_verses and not choices[v]:
            v += 1
        return v

    # state: (verse, translation | None, consumed, last translation,
    #         words in current line, passed a break, line index, *anchor words)
    start = (verse_start(0), None, 0, None, 0, False, 0)
    best: dict[tuple, float] = {start: 0.0}
    back: dict[tuple, tuple] = {}
    heap = [((start[0], 0, 0), 0, start)]
    tick = 1
    done: set[tuple] = set()
    finals = []

    def relax(src, dst, cost, piece):
        nonlocal tick
        if dst not in best or cost < best[dst] - 1e-12:
            best[dst] = cost
            back[dst] = (src, piece)
            key = (dst[0], 0 if dst[1] is None else 1, dst[2])
            heapq.heappush(heap, (key, tick, dst))
            tick += 1

    while heap:
        _, _, st = heapq.heappop(heap)
        if st in done:
            continue
        done.add(st)
        v, t, o, last, w0, seen0, i, *anchor_words = st
        if v >= n_verses:
            finals.append(st)
            continue
        held = dict(zip(keep[i - 1] if i else (), anchor_words, strict=True))
        if t is None:
            opts = [(t2, gamma * _register(penalties, default_penalty, last, t2)
                     if last else 0.0) for t2 in choices[v]]
        else:
            opts = [(t, 0.0)]
        for tt, reg in opts:
            base = best[st] + reg
            n = len(toks[tt][v])
            seen = seen0
            for o2 in range(o + 1, n + 1):
                if not breakable(tt, v, o2):
                    continue
                w = w0 + o2 - o
                if w < min_words and not (o2 == n and verse_start(v + 1) >= n_verses):
                    continue  # too short to end here (unless the text ends)
                if w > hi and seen:
                    break
                end = terminal_word(toks[tt][v][o2 - 1])
                c = base + beta * abs(w - target) / target
                for a, b in pairs:
                    if b == i:
                        c += alpha * (distance(held[a], end) - 1)
                ni = (i + 1) % k
                held_n = {**held, i: end}
                anchors = tuple(held_n[a] for a in keep[i]) if ni else ()
                point = (verse_start(v + 1), None, 0) if o2 == n else (v, tt, o2)
                relax(st, (*point, tt, 0, False, ni, *anchors), c, (v, tt, o, o2, True))
                if w > hi:
                    break  # the first break past hi must end the line
                seen = True
            else:
                # Verse exhausted without the line having to end: the line may
                # also run on. ``seen`` says whether it passed a legal break.
                nv = verse_start(v + 1)
                if nv < n_verses:
                    relax(st, (nv, None, 0, tt, w0 + n - o, seen, i, *anchor_words), base,
                          (v, tt, o, n, False))
    if not finals:
        return [], [], 0.0
    final = min(finals, key=best.__getitem__)
    cost = best[final]
    pieces = []
    st = final
    while st != start:
        st, piece = back[st]
        pieces.append(piece)
    pieces.reverse()
    path: list[str] = [names[0]] * n_verses
    lines, words = [], []
    for v, t, a, b, ends in pieces:
        path[v] = t
        words.extend(toks[t][v][a:b])
        if ends:
            lines.append(" ".join(words))
            words = []
    return lines, path, cost


# ------------------------------------------------------- System 2: substitute

# Alternative spellings of the same inflection: "church" + es, "hope" + d.
_SUFFIX_CLASS = {"s": ("s", "es"), "es": ("es", "s"), "d": ("d", "ed"), "ed": ("ed", "d")}


# Early Modern English function words NLTK's modern list lacks. Almost half
# of the lineated KJV's rhymes were thee/me/ye/be pairs.
ARCHAIC_STOPWORDS = frozenset(
    ["thee", "thou", "thy", "thine", "ye", "hath", "doth", "art", "shalt", "wilt", "hast", "dost", "unto", "yea"]
)


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
    content, _ = scheme_satisfaction(lines, scheme, content_rhymes)
    return f"{hit}/{total} scheme pairs rhyme ({content} on content words)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("stitch", help="System 1: pick a translation per verse")
    s2 = sub.add_parser("substitute", help="System 2: synonym swaps in one text")
    s2.add_argument("--bible", required=True)
    s3 = sub.add_parser("lineate", help="re-break one text into rhyming lines, words unchanged")
    s3.add_argument("--bible", required=True)
    s4 = sub.add_parser("joint", help="translation per verse and line breaks, chosen together")
    for p in (s1, s4):
        p.add_argument("--bible", action="append", required=True, metavar="NAME=PATH")
        p.add_argument("--penalty", action="append", default=[], metavar="A:B=X",
                       help="register penalty for switching between A and B")
        p.add_argument("--default-penalty", type=float, default=0.3)
    s1.add_argument("--gamma", type=float, default=0.5)
    s4.add_argument("--gamma", type=float, default=0.2,
                    help="switch penalty weight; the default makes a switch cost more "
                         "than any line-length gain, so only rhymes buy one")
    for p in (s3, s4):
        p.add_argument("--min-words", type=int, default=1,
                       help="hard floor on words per line (the last line is exempt)")
        p.add_argument("--lo", type=int, default=4, help="target minimum words per line")
        p.add_argument("--hi", type=int, default=16, help="maximum words per line")
    for p in (s1, s2, s3, s4):
        p.add_argument("--ref", required=True, help='ref prefix, e.g. "psalms 23:"')
        p.add_argument("--scheme", default="AABB")
    for p in (s1, s3, s4):
        p.add_argument("--content", action="store_true",
                       help="optimize for content-word rhymes (no me/thee)")
        p.add_argument("--slant", action="store_true",
                       help="give near-rhymes partial credit (tends to buy them "
                            "with one-word lines); default scores exact rhymes only")
    for p in (s3, s4):
        p.add_argument("--conj", action="store_true",
                       help="also allow breaks before and/but/for/that/which/...")
    args = ap.parse_args(argv)
    content, slant = getattr(args, "content", False), getattr(args, "slant", False)
    dist = {(False, False): exact_distance, (True, False): content_exact_distance,
            (False, True): phonetic_distance, (True, True): content_distance}[content, slant]

    if args.cmd == "lineate":
        rows = load_passage(args.bible, args.ref)
        if not rows:
            sys.exit(f"no verses match {args.ref!r}")
        verses = [v for _, v in rows]
        bb = CONJUNCTIONS if args.conj else frozenset()
        lines, _ = lineate(verses, args.scheme, args.lo, args.hi, distance=dist,
                           break_before=bb, min_words=args.min_words)
        print(f"# one verse per line: {_report(verses, args.scheme)}")
        print(f"# lineated:           {_report(lines, args.scheme)}")
        for n, line in enumerate(lines):
            if n and n % len(args.scheme) == 0:
                print()
            print(line)
        return

    if args.cmd in ("stitch", "joint"):
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
        if args.cmd == "joint":
            lines, path, cost = stitch_lineate(
                corpora, args.scheme, args.lo, args.hi, gamma=args.gamma,
                register_penalties=penalties, default_penalty=args.default_penalty,
                distance=dist, break_before=CONJUNCTIONS if args.conj else frozenset(),
                min_words=args.min_words)
            for n, verses in corpora.items():
                print(f"# {n}, one verse per line: {_report(verses, args.scheme)}")
            print(f"# joint: {_report(lines, args.scheme)}")
            runs = [(t, len(list(g))) for t, g in itertools.groupby(path)]
            print("# translations in verse order: "
                  + ", ".join(f"{t}×{n}" for t, n in runs))
            for n, line in enumerate(lines):
                if n and n % len(args.scheme) == 0:
                    print()
                print(line)
            return
        path, cost = stitch(corpora, args.scheme, gamma=args.gamma,
                            register_penalties=penalties,
                            default_penalty=args.default_penalty, distance=dist)
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
