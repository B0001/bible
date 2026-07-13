# Phase 12 — Universal learning order (corpus frequency ranks)

The heart of the app: a single number per verse, in **every** language, that
says *when* to read it. Depends on Phase 11 being done. UI comes in Phase 13;
this phase is pipeline + data only.

## D1 Design decision: ranks come from the corpus itself

External frequency lists (wordfreq, n-gram datasets) are modern-language,
English-biased, and keyed by surface forms — useless for Biblical Hebrew and
Koine Greek and mismatched with our stems. Instead, rank every word by its
frequency **within the Bible being read**. This works identically for all 12
bibles, needs no downloads or new dependencies, and is optimal for the goal:
learning the corpus's own most frequent words first maximizes comprehension of
that corpus fastest. This supersedes `scripts/build_english_poset.py`, which
is deleted in P12.4.

## D2 The algorithm (locked — implement exactly this)

**Rank table.** Given every verse's token list (stems, from
`tokenize_and_stem`): count occurrences of each distinct form over the whole
corpus; sort by `(-count, form)` (count descending, ties broken by the form's
lexicographic order — deterministic); assign ranks 1, 2, 3, … in that order.
Rank 1 = most frequent word in this Bible.

**Verse difficulty.** For one verse with token list `forms`, rank table
`ranks`, target comprehension `target = 0.95`, and a (possibly empty) set of
already-`known` forms:

1. If `forms` is empty → difficulty is **null** (no tokens, nothing to learn;
   sorts last).
2. Effective rank of each token: `0` if the form is in `known`, else
   `ranks[form]` (fall back to `len(ranks) + 1` if the form is somehow absent).
3. Sort the effective ranks ascending. Let `k = max(1, ceil(target *
   len(forms)))`.
4. Difficulty = the k-th smallest effective rank (1-indexed).

Interpretation: difficulty N means "once you know the top-N most frequent
words of this Bible (plus your own known words, which are free), at least 95%
of this verse's words are known." Sorting verses by difficulty ascending IS
the learning order. A vocabulary-size slider maps directly onto N (Phase 13).

**Worked example** (use as the test fixture; the numbers are exact):

```
tokens = [["a","b","a"], ["b","c"], ["a","c","d","a"]]
counts: a=4, b=2, c=2, d=1
ranks:  a→1, b→2, c→3, d→4        (b before c: tie on count 2, "b" < "c")

difficulty(target=0.95, known=∅):
  v1: n=3, k=ceil(2.85)=3, sorted eff [1,1,2]   → 2
  v2: n=2, k=2,            sorted eff [2,3]     → 3
  v3: n=4, k=ceil(3.8)=4,  sorted eff [1,1,3,4] → 4
learning order: v1, v2, v3

with known={"c"}:
  v2: sorted eff [0,2] → 2      (ties with v1; stable order keeps v1 first)
  v3: sorted eff [0,1,1,4] → 4
```

## P12.1 parser.py: `corpus_ranks` and `verse_difficulty`

Add to `parser.py` (near `next_words_to_learn`), plus `import math` at top:

```python
def corpus_ranks(token_lists):
    """Frequency rank of every distinct form across the corpus (1 = most
    frequent). Ties broken by lexicographic form order — deterministic."""
    counts = Counter()
    for toks in token_lists:
        counts.update(toks)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {form: i + 1 for i, (form, _) in enumerate(ordered)}


def verse_difficulty(forms, ranks, target=0.95, known=frozenset()):
    """Smallest vocabulary size N (learning forms in corpus-frequency order,
    `known` forms free) at which >= target of `forms` is known. None for
    empty verses. See PHASE12_DESIGN.md D2."""
    if not forms:
        return None
    fallback = len(ranks) + 1
    eff = sorted(0 if f in known else ranks.get(f, fallback) for f in forms)
    k = max(1, math.ceil(target * len(eff)))
    return eff[k - 1]
```

Docstrings may be reworded but signatures and behavior are locked.

## P12.2 parser.py main(): always write a `difficulty_rank` column

In `main()`, the one-tokenization pass (≈line 843) already computes `forms`
per verse but discards them. Change it to keep them:

```python
verses = bible_df["verse"].to_list()
all_forms, knowns, totals = [], [], []
for verse in verses:
    forms = tokenize_and_stem(verse, args.lang)
    all_forms.append(forms)
    knowns.append(sum(1 for f in forms if f in vocab_stems))
    totals.append(len(forms))
ranks = corpus_ranks(all_forms)
difficulties = [
    verse_difficulty(f, ranks, known=vocab_stems) for f in all_forms
]
```

Then, where `known_count`/`total_count` are appended (≈line 879), add a third
series after them:

```python
pl.Series("difficulty_rank", difficulties, dtype=pl.Int64),
```

Final CSV column order: `ref, verse, comprehension_rate, [effort],
known_count, total_count, difficulty_rank`. `difficulty_rank` is nullable
(null = empty verse). The user's vocab counts as known (free) — that is
intentional: the column answers "how many *new* top-frequency words until I
can read this."

No new CLI flag: the column is always written (one Counter pass + a sort per
verse; negligible). `dash_app.py` needs no change (it selects columns
explicitly and ignores extras).

## P12.3 Tests (test_parser.py)

Add — using the D2 fixture verbatim:

1. `test_corpus_ranks_orders_by_count_then_form`: build ranks from the fixture
   `tokens`; assert `ranks == {"a": 1, "b": 2, "c": 3, "d": 4}`.
2. `test_verse_difficulty_matches_worked_example`: assert difficulties
   `[2, 3, 4]` for the three verses with `known=frozenset()`.
3. `test_verse_difficulty_known_words_are_free`: with `known={"c"}`, assert
   v2 → 2 and v3 → 4.
4. `test_verse_difficulty_empty_verse_is_none`:
   `verse_difficulty([], {"a": 1}) is None`.
5. `test_verse_difficulty_unseen_form_falls_back`:
   `verse_difficulty(["z"], {"a": 1}) == 2` (fallback = len(ranks)+1).
6. `test_cli_writes_difficulty_rank` (integration, follow the style of the
   existing CLI tests in test_parser.py if any; otherwise call `main()` via
   monkeypatched `sys.argv` or subprocess on `sample/nasb_sample.txt` +
   `sample/my_vocab.txt` into a tmp_path CSV): assert the output CSV header
   ends with `difficulty_rank` and that the column parses as integers (or
   empty) with at least one non-empty value.

## P12.4 Delete the superseded poset script

- Delete `scripts/build_english_poset.py` and the
  `english_poset_vocabulary.json` line from `.gitignore`. D1 explains why:
  corpus-derived ranks replace the downloaded English-only list, and its tier
  slicing is subsumed by the difficulty number.

## P12.5 site: same algorithm in JS + parity test

Create `site/rank.js` — an ES module (the site already dynamic-imports
modules; see `stemmerFor` in app.js):

```js
// Corpus frequency ranks + verse difficulty. Mirrors parser.py
// corpus_ranks / verse_difficulty exactly — see PHASE12_DESIGN.md D2.
// Parity is tested (test_static_export.py); change both sides together.
export function corpusRanks(tokenLists) {
  const counts = new Map();
  for (const toks of tokenLists)
    for (const t of toks) counts.set(t, (counts.get(t) || 0) + 1);
  const ordered = [...counts.entries()].sort(
    (x, y) => y[1] - x[1] || (x[0] < y[0] ? -1 : 1));
  const ranks = new Map();
  ordered.forEach(([form], i) => ranks.set(form, i + 1));
  return ranks;
}

export function verseDifficulty(forms, ranks, target = 0.95, known = new Set()) {
  if (!forms.length) return null;
  const fallback = ranks.size + 1;
  const eff = forms
    .map(f => (known.has(f) ? 0 : (ranks.get(f) ?? fallback)))
    .sort((a, b) => a - b);
  const k = Math.max(1, Math.ceil(target * eff.length));
  return eff[k - 1];
}
```

Note on tie-breaking: `x[0] < y[0]` compares UTF-16 code units, Python
compares code points — identical for all BMP scripts, which covers every
language here. Do not "fix" this with localeCompare (locale-dependent, would
break parity).

Parity test — add to `test_static_export.py`, following its existing
node-subprocess pattern (P9.2 stemmer-fidelity tests):
`test_rank_js_matches_python`: run `node --input-type=module -e "<script>"`
where the script imports `site/rank.js` (absolute `file://` path), evaluates
the D2 fixture (ranks, three difficulties with empty `known`, v2 with
`known={"c"}`), and prints JSON; compare against `corpus_ranks` /
`verse_difficulty` from parser.py on the same fixture. Skip the test when
`node` is not on PATH (same guard the existing node tests use).

## Out of scope (Phase 13)

No UI changes: `site/app.js`, `site/index.html`, `dash_app.py` layout, and
`scripts/export_static.py` are untouched. The site computes ranks client-side
from `bible.tokens` (already shipped in every `site/data/<id>.json`), so the
export format does not change.

## Definition of done

- `ruff check .` clean; `pytest` green including the six new tests and the
  node parity test (or its skip on machines without node).
- `python parser.py --bible sample/nasb_sample.txt --vocab sample/my_vocab.txt
  --out /tmp/g.csv` writes a `difficulty_rank` column; spot-check that the
  verse with the lowest difficulty is visibly "easier" (more common words)
  than the one with the highest.
- `scripts/build_english_poset.py` no longer exists.
