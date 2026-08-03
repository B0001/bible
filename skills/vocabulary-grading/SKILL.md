---
name: vocabulary-grading
description: >-
  Score a text corpus by how much of it a given reader already knows, and
  sequence it into a learning order. Use when building a graded reader,
  extensive-reading tool, or i+1 / comprehensible-input feature; when asked to
  rank passages by difficulty, pick "what should I read next", rank "what word
  should I learn next", find the longest passage a learner can read, or add
  spaced repetition over a corpus. Covers the comprehension-rate formula,
  corpus-internal difficulty ranks, an O(n) longest-readable-span algorithm,
  unlock ranking, and a half-life recall model.
---

# Vocabulary grading

Given a corpus and a reader's known-word list, produce: a per-item comprehension
rate, a vocabulary-size difficulty for every item, a learning order, the longest
contiguous span the reader can read now, and a ranked list of what to learn next.

Extracted from a working implementation that grades 12 Bible translations in 11
languages (~325k verses). Reference implementation: `parser.py` in
https://github.com/B0001/bible — this skill is the portable statement of it.

A dependency-free implementation of the four core functions ships alongside this
skill at `${CLAUDE_SKILL_DIR}/scripts/grade.py` (stdlib only, ~120 lines). Read
or copy it rather than re-deriving the algorithms.

## The one rule that makes everything else work

**Normalize both sides with the same function.** Corpus tokens and vocabulary
entries must pass through one tokenizer/stemmer before comparison. If they
don't, matching silently under-counts and every downstream metric is wrong in a
way that still looks plausible.

```python
vocab_forms = set(tokenize_and_stem(open(vocab_path).read(), lang))
verse_forms = tokenize_and_stem(verse_text, lang)
```

See the companion skill `multilingual-text-tokenization` for what that function
should be per language.

## 1. Comprehension rate

```python
def comprehension_rate(text, vocab_forms, min_length=1, lang="en"):
    forms = tokenize_and_stem(text, lang)
    if not forms or len(forms) < min_length:
        return 0.0
    return sum(1 for f in forms if f in vocab_forms) / len(forms)
```

- **Repeats count with multiplicity**, per token not per type. A verse using
  "the" four times gets four known-counts out of its token total. Any
  hash-vector or set-based reimplementation will diverge here; that divergence
  is the reason this formula was made canonical in the source repo.
- **Too-short items score 0.0, not null.** Keep the `not forms` guard separate
  from the length guard so `min_length=0` can't produce 0/0 on a
  punctuation-only item.
- **Target ≈ 0.95.** The comprehensible-input "sweet spot" (i+1): high enough to
  read fluently, low enough to meet new words. Treat 0.95 as a tunable default,
  and cite Hu & Nation on coverage if you need to justify it — it is a
  convention, not something this method derives.

## 2. Corpus-internal difficulty (the portable metric)

Do **not** rank words with an external frequency list. External lists are
modern-language, English-biased, and keyed on surface forms — useless for
Biblical Hebrew or Koine Greek, and mismatched with your stems. Rank within the
corpus being read. It needs no download, works identically in every language,
and is optimal for the goal: learning a corpus's own most frequent words first
maximizes comprehension of that corpus fastest.

```python
def corpus_ranks(token_lists):
    counts = Counter(t for toks in token_lists for t in toks)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {form: i + 1 for i, (form, _) in enumerate(ordered)}

def verse_difficulty(forms, ranks, target=0.95, known=frozenset()):
    if not forms:
        return None
    fallback = len(ranks) + 1
    eff = sorted(0 if f in known else ranks.get(f, fallback) for f in forms)
    k = max(1, math.ceil(target * len(eff)))
    return eff[k - 1]
```

**Ties break lexicographically by form**, never by hash or insertion order. That
one choice is what makes the output deterministic, reproducible across runs, and
byte-comparable between a Python pipeline and a JS reimplementation.

**Read the number as:** *once you know the top-N most frequent words of this
corpus (plus your own known words, which are free), at least `target` of this
item is known.* Sorting by difficulty ascending **is** the learning order. A
vocabulary-size slider maps straight onto N — everything at `difficulty <= N` is
readable now.

Worked example, small enough to use as a test fixture:

```
tokens = [["a","b","a"], ["b","c"], ["a","c","d","a"]]
counts: a=4, b=2, c=2, d=1
ranks:  a→1, b→2, c→3, d→4        (b before c: tie on 2, "b" < "c")

difficulty(target=0.95, known=∅):
  v1: n=3, k=ceil(2.85)=3, eff [1,1,2] → 2
  v2: n=2, k=2,            eff [2,3]   → 3
  v3: n=4, k=ceil(3.8)=4,  eff [1,1,3,4] → 4
learning order: v1, v2, v3
```

Break ties between items by original index (stable sort), not by a second
metric.

**Two properties worth stating in your docs:** difficulty computed with
`known=∅` is *vocabulary-independent* — a property of the text, so it is safe to
publish (see the `derived-dataset-publishing` skill). And it is *corpus-relative*
— rank 500 in one corpus is not rank 500 in another, so never average or compare
across corpora.

## 3. Longest readable span

Find the longest contiguous run whose **combined** rate clears the threshold —
not a run of individually-passing items. Two items at 50% each combine to 50%;
a per-item filter gets this wrong and looks right.

The trick is a reduction. Let `a[k] = known[k] - min_rate * total[k]` and `P` be
its prefix sum. Then

```
(Σ known[i..j-1]) / (Σ total[i..j-1]) >= min_rate   ⟺   P[j] - P[i] >= 0
```

A rate constraint becomes an additive one. Only left endpoints where `P` hits a
new strict minimum can ever be optimal, so candidates form a strictly decreasing
stack built in one left-to-right pass; a single right-to-left sweep pops each
one at most once. **O(n) total, two passes.** Implementation in
`scripts/grade.py::longest_span`.

Edge cases to pin with tests: empty input → `None`; everything known → the whole
corpus is one span; nothing known → empty result rather than a zero-length span;
ties → decide and test which one you return.

For a top-K list rather than a single answer, take the longest span, recurse
into the two surrounding segments, and cache each segment's span so every
iteration rescans only the two new segments.

## 4. What to learn next

Rank candidate words by **how many currently-unreadable items a single new word
would unlock**.

```python
for item in corpus:
    forms = tokenize_and_stem(item, lang)
    total = len(forms)
    if total < min_length:
        continue
    counts = Counter(forms)
    known = sum(c for f, c in counts.items() if f in vocab_forms)
    if known / total >= target:
        continue                      # already readable, contributes nothing
    for form, count in counts.items():
        if form not in vocab_forms and (known + count) / total >= target:
            unlocks[form] += 1
```

- **All occurrences of the candidate are credited at once** (`known + count`,
  not `known + 1`). Learning a word teaches you every instance of it.
- **Only single-word unlocks count.** An item needing two new words scores for
  neither. This is deliberate: it ranks the words that pay off *now*.
- Break ties by corpus rank ascending, so the more frequent word wins. The
  source repo's Python side omits this tiebreaker and its JS side has it — the
  two orderings then differ among ties. Add it on both sides.

## 5. Optional: recall decay, effort, semantic credit

Three refinements, each opt-in. **Every one must degrade to a no-op with a
single warning when its optional dependency is missing — never crash, and never
silently change scores.** Use a module-level `_warned` flag so a 300k-item run
emits one warning, not 300k.

**Half-life recall.** A word's strength is a half-life; recall probability decays
from the last review.

```
h = clamp(H0 * GROWTH ** (n_correct - n_incorrect), H_MIN, H_MAX)   # 1.0, 2.0, 0.1, 365.0 days
p = 2 ** (-elapsed_days / h)
```

Correct doubles the half-life, wrong halves it; only the *net* matters, so
history order is irrelevant. Then `rate = mean(p_i)` over the item's tokens
instead of a binary count. Store reviews as an **append-only event log**
(`stem, timestamp, correct`) and replay derived state — never mutate a stored
strength. Words never reviewed are `p = 1.0`; words not in the profile are
`p = 0.0`. This is Settles & Meeder's half-life form, deliberately heuristic:
BKT/DKT need training data you don't have on day one, and this gets most of the
value. Due-for-review threshold `p < 0.5`, most-forgotten first.

**Lexical effort**, to rank two items with *identical* comprehension rates by how
hard their unknown words are:

```
d(w) = clamp(1 - zipf(w) / 8, 0, 1)          # 8 ≈ top of the Zipf scale
effort = Σ d(w_i) * (1 - p_i)
```

Pass the **surface form** to the frequency lookup, not the stem — frequency
tables are keyed on real words.

**Semantic partial credit**, granting unknown tokens credit for being close to
known ones:

```
credit(w) = 0.8 * sim(w)  if sim(w) >= 0.6  else 0
p_effective(w) = max(recall_prob(w), credit(w))
```

Capped below 1.0 so similarity never fully substitutes for knowledge.
**Embed surface forms, never stems** — many stems are non-words (`above`→`abov`,
`mercy`→`merci`) and are out-of-vocabulary in every embedding model, so
embedding stems returns near-zero credit everywhere and looks like it works.
Restrict to languages with real vector models and return `None` with a warning
otherwise, rather than scoring with the wrong language's embeddings.

## Traps

1. **Combined rate ≠ per-item rate** for any span or window. Write the test that
   fails a per-item refactor before you write the algorithm.
2. **Stems for matching, surface forms for embedding and frequency lookup.**
   Getting this backwards yields features that return zeros and look healthy.
3. **Don't hardcode one language's stemmer in the "advanced" code paths.** The
   source repo's decay/effort paths use `STEMMER.stem(t) if lang == "en" else t`
   while its default path dispatches properly — so for Spanish or Russian the
   optional features silently match bare tokens against stems. Route every path
   through one shared normalizer.
4. **The first review of a seed word can lower its score**: never-reviewed means
   `p = 1.0` forever, and logging one review starts the decay. Expected, but
   surprises users. Say so in the UI.
5. **Reader-scoped metrics must never ship as corpus metadata.**
   `comprehension_rate` and `known_count` describe one person; `total_count` and
   `difficulty_rank` describe the text.
6. **Tokenize once.** Reuse one token list per item for the rate, the counts,
   the corpus ranks and the difficulty. The source repo grades 23k verses in
   ~0.8 s this way; tokenizing per metric is 4× that for no reason.
7. **Assert backward compatibility exactly.** When you add the weighted model,
   test that with decay off it is *bit-identical* (`==`, not `approx`) to the
   binary formula across a range of items including degenerate ones.
8. **Cross-runtime parity is fragile and worth pinning.** If you mirror this in
   JS for a browser, note that `x < y` on strings compares UTF-16 code units in
   JS and code points in Python — identical for all BMP scripts. Do not "fix"
   it with `localeCompare`; that is locale-dependent and breaks parity. Test one
   shared fixture on both sides.
