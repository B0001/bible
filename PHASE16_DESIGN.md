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

Scheme pairs that rhyme (31,102 verses, one verse = one line):

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
