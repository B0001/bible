# Phase 16 — Rhymed composites: stitching, substitution, rhyme classes

`rhyme.py` implements the two engines from the "Computational Biblical Rhyme
Optimization" spec, plus a rhyme-class graph (L = D − A), and measures how
far each gets on real text. Nothing here is a translation. Both engines
produce a *composite*, and the CLI always prints how many rhyme-scheme pairs
the input and output actually satisfy, so "it rhymes" gets checked rather
than assumed.

```bash
python -m nltk.downloader cmudict wordnet stopwords
python rhyme.py stitch --bible kjv=data/kjv.txt --bible nasb=data/nasb.txt \
    --penalty kjv:nasb=0.8 --ref "psalms 23:" --scheme AABB
python rhyme.py substitute --bible data/kjv.txt --ref "psalms " --scheme ABAB
```

## What changed from the draft code, and why

| Draft behavior | Consequence | Now |
|---|---|---|
| Rhyme tail began at the last vowel of *any* stress | every `-ers`/`-ed` pair "rhymed": pastures/waters, shepherd/sheltered | tail begins at the last **primary**-stressed vowel (as the spec says), stress digits stripped |
| First CMUDict pronunciation only | "want" (AA1/AO1) misses half its rhymes | all pronunciations; rhyme = shared tail |
| Identical words counted as rhymes | "LORD"/"LORD" would be a perfect rhyme | identity isn't rhyme |
| Stitch DP charged a rhyme cost on *every* adjacent pair | only AAAA could be expressed; ABAB (non-adjacent) couldn't be expressed at all | exact DP over strophes: every N^k assignment enumerated inside a strophe, Viterbi across strophe boundaries. Checked against brute force for AABB/ABAB/AAAA/ABCB and partial strophes |
| Synonyms gathered in a `set` | substitution changed with `PYTHONHASHSEED` | WordNet sense order, lemma count; a test runs three hash seeds |
| `d_phon` was 0/1 | no preference for slant over miss | normalized tail edit distance (0 = rhyme, 1 = no info) |

Also: a line's final word is the last alphabetic token (so `sake.` and
`light";` work); swaps keep capitalization and trailing punctuation.

## Measurements (full Bible, KJV / AKJV / NASB from `tushortz/variety-bible-text`)

Scheme pairs that rhyme (31,102 verses, one verse = one line). These are
round-1 numbers; the round-2 pronunciation fix below moves them by a pair or
two (KJV AABB 56 → 55). The round-2 tables are current.

| scheme | KJV | AKJV | NASB | stitch γ=0.5 | stitch γ=0 | substitute (KJV) |
|---|---|---|---|---|---|---|
| AABB | 56/15551 | 34 | 35 | 90 | 91 | 97 (41 swaps) |
| ABAB | 48/15550 | 36 | 37 | 82 | 85 | 95 |
| XAXA | 20/7775 | 14 | 17 | 36 | 39 | 49 |

Register penalties: kjv↔akjv 0.05, either↔nasb 0.8. Psalm 23: 0/3 pairs rhyme
under AABB in every translation, stitched or substituted.

**Finding: prose Bible translations are essentially rhyme-free, and neither
engine changes that.** Baseline ≈ 0.3% of pairs. Stitching three translations
roughly doubles it, to 0.6%. Substitution gets 0.6%. Rhyme in English verse
comes from choosing *what to say*, and both engines only choose among
near-fixed endings. A composite that rhymes in more than a handful of places
needs either many more translations (the stitch gain grows with N, since each
line gets N endings to choose from) or rewriting whole clauses, not just the
final word.

### Substitution precision

With no filters, WordNet's context-free senses dominated. The first Psalms
run gave "waters → pee", "name → key", "wrath → ira" and "times →
multiplication", and full-Bible runs gave "me → Maine" and "died → diced"
(the verb *die* picking up the noun *dice*). Filters added, each covered by a
test: top-3 senses only, lemmas attested in SemCor, no proper nouns, no
stopwords, and part-of-speech-consistent inflection (irregular forms get no
synonyms). Those filters cut the whole-Bible AABB swaps from 328 to 41.

Of the 41 that remain, my own unblinded reading judges about half acceptable
in context. Good: fire→flame, multitude→throng, apparel→dress,
remain→stay. Wrong sense: throne→pot, head→brain, and Daniel's pulse→beat
(his pulse is legumes). Wrong number: brethren→brother, days→day. The
spec's language-model perplexity gate is the `accept=` hook. It isn't
implemented because no LM dependency is present, so this precision is
unvalidated and nothing here should be read as a claim that substitutions are
faithful.

## Round 2: more translations, and lineation

### Stitching improves with N, then levels off

Six more public-domain texts come from `scrollmapper/bible_databases` (ASV,
BBE, Darby, YLT, Webster, KJV), aligned to the tushortz NASB/AKJV by
(book order, chapter, verse). Full Bible, AABB, γ = 0, translations added in
the order shown:

| N | 1 KJV | 2 +NASB | 3 +BBE | 4 +YLT | 5 +Darby | 6 +ASV | 7 +Webster | 8 +AKJV |
|---|---|---|---|---|---|---|---|---|
| rhyming pairs | 55 | 87 | 138 | 176 | 182 | 190 | 193 | 194 |

The gains come from *dissimilar* translations (BBE's Basic English, YLT's
literalism). KJV-family texts (ASV, Webster, AKJV) add almost nothing,
because they end verses with the same words, and a repeated word is not a
rhyme. Eight translations reach 1.25%.

The per-strophe N^k enumeration from round 1 was too slow for this. `stitch`
is now a line-level DP whose state is the current line's translation plus
the translations of lines a later line must rhyme with: N states for AABB,
N² for ABAB. It's still exact (same brute-force tests), and the full
8-translation Bible runs in seconds. Blank renderings (ASV/BBE have 16 empty
verses) are never chosen.

### Lineation: change no words, choose the line breaks

`lineate()` keeps one text verbatim and chooses where lines end, allowed
after clause punctuation and at verse ends. It's an exact DP over
(tokens consumed, line in strophe, end positions of open rhyme anchors). A
rhyme is a *reward* (`alpha * (distance − 1)`), so the optimizer isn't paid
to make fewer, longer lines to dodge pairs. A small length term keeps lines
near 4–16 words. Tested: the words are preserved exactly, and the DP matches
brute-force enumeration of every valid lineation.

Full KJV, strophes restarting each chapter. "content" counts only rhymes
between non-stopwords (NLTK list plus thee/thou/ye/unto/yea…):

| engine | AABB all | AABB content | ABAB all | ABAB content |
|---|---|---|---|---|
| one verse per line | 46 (0.30%) | 14 | 41 (0.27%) | 14 |
| stitch, 8 translations | 189 (1.24%) | 87 | 167 (1.12%) | 73 |
| substitute (KJV) | 86 (0.56%) | 39 | 73 (0.49%) | 36 |
| **lineate (KJV)** | **904 (2.50%)** | 362 | **1117 (3.09%)** | 421 |
| lineate, content-only cost | 430 | **366 (1.01%)** | 501 | **427 (1.19%)** |

**Finding:** re-breaking the lines of *one unaltered translation* gives 4–5×
more content rhymes than stitching eight translations, and 9× more than
synonym substitution. It also changes nothing a reader could call
unfaithful. The rates are still low: this doesn't make the Bible rhyme; it
finds the rhymes the Bible already has (Psalm 114's "skipped like rams, /
and the little hills like lambs" — twice). Percentages across rows aren't
strictly comparable, because lineation produces more lines and so more
pairs. The raw counts are.

Before the content filter, 42% of lineated KJV rhymes were me/thee/ye/be
pairs. Those are legitimate in hymnody, but they're why "content" is
reported separately. The second most common rhyme was "come/them", an
artifact: CMUDict's reduced "them" (DH AH0 M) supplied an unstressed tail.
`rhyme_tails` now ignores a word's pronunciations that lack a primary stress
whenever it has one that doesn't.

```bash
python rhyme.py lineate --bible data/kjv.txt --ref "psalms 114:" --content
```

Next steps not taken: lineation over *stitched* choices (translation per
verse and breaks jointly — the product of both gains, at the cost of a larger
state); breaks at conjunctions ("and", "for") as well as punctuation.

## Rhyme classes and the Laplacian

`rhyme_classes(words)` builds the graph of line endings (A_ij = 1 when two
words rhyme) and returns its connected components. With multiple
pronunciations, rhyme is **not transitive**: "font" (AA N T) and "haunt"
(AO N T) don't rhyme, but both rhyme with "want", so the three form one
class. The test suite checks the union-find count against
`n − rank(L)` computed in exact rational arithmetic (nullity of L = D − A =
number of components). These are two independent computations of the same
number.

## Out of scope / not done

- Sentence-embedding `d_sem` in the stitch cost. `stitch(distance=...)` takes
  any callable, but no embedding model is a dependency.
- Neural G2P for words not in CMUDict. They score distance 1, so they never
  count as a rhyme.
- Clause-level lines. Lines are verses: splitting clauses differently per
  translation would break the V×N alignment that stitching relies on.
