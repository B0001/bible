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

## Round 3: translation and line breaks chosen together

`stitch_lineate()` picks each verse's translation *and* the line breaks in
one exact DP. The objective is lineation's (rhyme reward, length term) plus
stitching's register penalty between consecutive verses. Every verse appears
verbatim in exactly one translation. A line may run across a verse boundary,
so one line can join two translations.

The claim the tests check: the returned cost equals the minimum, over *every*
translation assignment, of that assignment's register penalty plus
`lineate()` on the text it selects (brute force for AABB, ABAB and XAXA). With
one translation, it reduces to `lineate()`.

Full Bible, strophes restarting each chapter, content-only cost, γ = 0:

| AABB content rhymes | N=1 | 2 | 3 | 4 | 8 |
|---|---|---|---|---|---|
| stitch | 14 | 34 | 60 | 82 | 88 |
| lineate (KJV) | 366 | 366 | 366 | 366 | 366 |
| **joint** | **366** | **532** | **838** | **1143** | **1260** |

ABAB, N = 8: joint 1826 content rhymes (5.05% of pairs) vs 427 for lineate
and 73 for stitch.

**Finding: the gains don't add up, they multiply.** With 8 translations the
joint search finds 1260 content rhymes, 2.8× the sum of the two engines alone
(366 + 88). A different translation of a verse means different clause-final
words to break on, so each choice gives the line breaker new candidates. Unlike
stitching, the joint result keeps rising as translations are added (still +117
from 4 to 8). The joint rate is 3.5% of AABB pairs (5% ABAB). That's still not
verse, but it's 38× the one-verse-per-line baseline on content words. At N = 1
the joint's content count equals lineate's exactly (366); the all-rhyme counts
differ by 3 (433 vs 430), from ties between equal-cost lineations.

Example (`python rhyme.py joint` with all eight texts, `--ref "psalms 1:"
--content`): YLT supplies "…is his delight, / And in His law he doth meditate
by day and by night". Then BBE ends the psalm with "upright / upright", which
scores nothing because a repeated word isn't a rhyme.

Performance: the first version enumerated whole lines, branching N ways at
every verse boundary a line crossed. That's exponential on runs of short
verses (1 Chronicles 1: 8 s), and a full-Bible run didn't finish in 35
minutes. A verse start inside an open line is now its own DP state (words so
far, break seen), so each step stays within one verse. The full Bible with 8
translations takes 4.4 min for AABB and 10.6 min for ABAB. The slowest chapter
is Nehemiah 10's name list (1.7 s AABB, 12 s ABAB).

The CLI's `joint` defaults to `--gamma 0.1`. At γ = 0 translations switch on
arbitrary ties, and a small penalty keeps a verse's switch for when it buys a
rhyme or a better line length. The table above uses γ = 0, the upper bound.

Reproducing: `python scripts/convert_scrollmapper.py --translation YLT`
(likewise ASV, BBE, Darby, Webster) writes `data/<name>.txt` with refs that
match the tushortz texts verse for verse. It maps the 66 books by position,
guarded by book-count, book-order and verse-count checks, and it refuses
Tyndale (mostly empty) and DRC (78-book canon). It also converts `--` to an
em dash. YLT writes dashes as `--`, and 135 of its verses end in " --",
which fused with the ` -- ` separator and corrupted their refs. The
hand-built `data/ylt.txt` used in round 3's CLI example had that bug. The
round-3 tables read the CSVs directly and are unaffected.

## Round 4: conjunction breaks, and exact scoring in the CLI

`break_before` (the CLI's `--conj`) lets a line also end just *before* a
word in `CONJUNCTIONS`: and, but, for, or, nor, yet, that, which, who, when,
because. It's off by default, so every earlier number is unchanged. The
brute-force optimality tests for both `lineate` and `stitch_lineate` now
run with and without it, and their break points are computed independently
of `rhyme.break_after`.

Full Bible, content-only exact cost, content-word rhymes:

| engine | AABB | AABB +conj | ABAB | ABAB +conj |
|---|---|---|---|---|
| lineate KJV | 366 | 465 | 427 | 630 |
| lineate BBE | 318 | 496 | 395 | 691 |
| **joint, 8 translations** | 1260 | **1736 (4.66%)** | 1826 | **2589 (6.97%)** |

Conjunction breaks add 27–75% more rhymes. They help most where punctuation
is sparse (BBE's Basic English, +56% and +75%). The run took 5.4 min for
AABB and 15 min for ABAB; Nehemiah 10 is still the slowest chapter (11 s,
ABAB).

**Short lines.** `lo` is only a length target, so a one-word line costs
0.045, far less than a rhyme is worth. On Psalms + Proverbs + Isaiah with
exact scoring, 9% of the joint's rhymes involve a line under 4 words (14%
with `--conj`). Almost every such short line is there to win a rhyme (15 of
15 short lines, 31 of 31), so the counts above include them. Read them as
"rhymes at line ends, some on very short lines", not as stanza-quality verse.

**CLI defaults changed after reading real output.** With graded distance
(partial credit for near-rhymes), the optimizer bought near-rhymes with
one-word lines. Isaiah 55 came out as "wine / and milk", "A leader / and
commander". So `lineate`, `joint` and `stitch` now score exact rhymes
(`exact_distance` / `content_exact_distance`), and `--slant` restores
graded credit. `joint`'s default γ went from 0.1 to 0.2. At 0.1 a switch
cost less than a line-length improvement, so Ecclesiastes 3 switched
translation 15 times for no rhyme. At 0.2 it stays in one text, and
Psalm 114 stays in NASB with 3 rhymes (sea/flee, and rams/lambs twice). The
library functions keep their defaults (`phonetic_distance`, γ = 0), which
all the tables use.

## Round 5: a hard floor on line length

Round 4 found that 9–14% of rhymes involve a line under 4 words. `min_words`
(CLI `--min-words`, default 1 = no floor) makes a break fewer than
`min_words` words into a line illegal, except at the end of the text. It
applies to `lineate` and `stitch_lineate` alike. The brute-force optimality
tests run with floors of 1 and 3 and check that no line except the last is
shorter.

Full Bible, 8 translations, `--conj`, content-only exact cost:

| content-word rhymes | no floor | `min_words=4` | kept |
|---|---|---|---|
| joint, AABB | 1736 | **1538 (4.14%)** | 89% |
| joint, ABAB | 2589 | **2298 (6.23%)** | 89% |
| lineate KJV, AABB | 465 | 408 | 88% |
| lineate KJV, ABAB | 630 | 527 | 84% |

**Finding:** the short lines weren't carrying the result. With every line
at least 4 words, 84–89% of the rhymes survive, which matches round 4's
9–14% estimate. The few lines still under 4 words (3–5 across the
Bible) are chapter endings, which are exempt. `--min-words 4` is the
setting to use for output meant to be read. Psalm 114 (KJV, AABB) then comes
out as two clean rams/lambs couplets.

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
