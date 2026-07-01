# Phase 5 Design — Research-Backed Personalization

Status: design (ready to implement) · Author: Opus · Date: 2026-06-30

This is the implementation blueprint for SPEC.md Phase 5 (items 16–19). It exists
because those items need real algorithm/data-model design decisions that should be
settled before coding. Each section gives concrete schemas, function signatures,
formulas, and acceptance criteria so the work can be executed step by step.

## 0. Guiding decisions (positions taken)

These resolve the ambiguity in SPEC §4 items 16–19. Confirm or override before
building.

1. **Start heuristic, not learned.** SPEC cites Bayesian Knowledge Tracing and
   Deep Knowledge Tracing. Both need training data we do not have yet (no logged
   reviews). Ship a closed-form **half-life model** first; revisit fitted
   BKT/DKT/HLR weights only once `reviews.csv` has real volume. Rationale: avoids
   building a training pipeline for zero data, and the half-life form already
   gives 80% of the value.
2. **Unify items 16 and 17 under one model.** A single per-word *half-life* yields
   both the mastery probability (item 16) and the review schedule (item 17). Do
   not build a separate mastery model and scheduler.
3. **Backward compatible by construction.** The new probability-weighted
   comprehension must reduce *exactly* to today's ratio when there is no review
   history: seed-vocab words count as `p = 1.0` ("permanently known"), unknown
   words as `p = 0.0`. Decay is opt-in (`--decay`, or simply having a
   `reviews.csv`). This keeps all current behavior and tests green.
4. **Heavy ML deps stay optional.** Embeddings (item 19) and trained complexity
   models must not bloat the core image (SPEC §5 keeps image size a concern).
   Gate them behind `pip install '.[lexical]'` / `'.[semantic]'` extras and make
   the features degrade gracefully (a warning + no-op) when the extra is absent.

## 1. Prerequisite — review-history data model

Item 15 gives us vocab *profiles* (a `--vocab` file). Phase 5 needs per-word
*history*. Add, alongside each profile, an append-only event log.

**Files per profile** (co-located with the vocab file, e.g. `profiles/me/`):
- `vocab.txt` — seed known words (existing, item 15).
- `reviews.csv` — append-only event log. Schema:

  | column     | type            | notes                                  |
  |------------|-----------------|----------------------------------------|
  | `stem`     | str             | Snowball stem of the reviewed word     |
  | `timestamp`| ISO-8601 string | event time (UTC)                       |
  | `correct`  | int (0/1)       | recalled correctly?                    |

**Derived state** (replay the log; never stored mutably):

```python
@dataclass
class WordHistory:
    n_correct: int
    n_incorrect: int
    last_seen: datetime | None   # None for seed-only words

def load_profile(vocab_path) -> dict[str, WordHistory]:
    """Seed words start at WordHistory(0,0,None); replay reviews.csv to fill in."""

def record_review(vocab_path, stem, correct, when=None) -> None:
    """Append one event to reviews.csv (create dir/header if missing)."""
```

**CLI:** extend `parser.py` (mirrors `--learn`):
`--review STEM {correct|wrong}` appends an event. (`--learn` still seeds vocab.)

**Acceptance:** `--review faith correct` appends a row; `load_profile` returns the
replayed counts; round-trips through `parser.py` runs.

## 2. Items 16+17 — half-life recall model & probability-weighted comprehension

**Half-life** (days), heuristic Leitner/SM-2-lite form, clamped:

```
h(word) = H0 * GROWTH ** (n_correct - n_incorrect)      # e.g. H0=1.0 day, GROWTH=2.0
h ∈ [H_MIN, H_MAX]                                       # e.g. [0.1, 365] days
```

**Recall probability** (Settles & Meeder half-life form):

```
p(word, now) = 2 ** (-elapsed_days / h(word))           # elapsed since last_seen
```

Special cases: seed word with no reviews and decay **off** → `p = 1.0`. Word not
in profile at all → `p = 0.0` (before the item-19 fallback).

```python
def recall_prob(history: WordHistory, now, decay: bool) -> float: ...
```

**Probability-weighted comprehension (item 16)** — generalize `comprehension_rate`:

```
comprehension_rate(verse) = (1/n) * Σ_i p(stem_i, now)        # n = total stems
```

When `decay=False` and every profile word is seed (p=1) this is identical to the
current `known / total`. Implement as a new `weighted_comprehension_rate(verse,
profile, now, decay)`; keep the existing `comprehension_rate(verse, vocab_stems)`
as the `decay=False`/binary fast path so nothing regresses.

**Spaced-repetition study queue (item 17)** — combine two signals into one ranked
list:

- **Due reviews:** profile words with `p(word, now) < REVIEW_P` (e.g. 0.5), ranked
  by ascending `p` (most-forgotten first). Reason: cheap retention.
- **New words:** the existing `next_words_to_learn` unlock ranking. Reason: growth.

```python
def study_queue(bible_df, profile, now, known_rate, review_p, top_n) -> pl.DataFrame:
    # columns: stem, action {"review","learn"}, score, reason
```

CLI: `--study N --study-out PATH`.

**Acceptance:** a word reviewed correctly twice has longer half-life and higher
`p`; a word last seen long ago appears in the due-review queue; with `decay=False`
and seed-only vocab, `weighted_comprehension_rate == comprehension_rate` for every
sample verse (assert in a test).

## 3. Item 18 — lexical-complexity-aware scoring

Per-word difficulty `d(w) ∈ [0,1]`. MVP uses corpus frequency (no training):

```
d(w) = clamp(1 - zipf_frequency(w, "en") / 8.0, 0, 1)   # via the `wordfreq` lib
```

Use it two ways (additive, not replacing comprehension):
- **Unknown-effort** per verse: `effort = Σ_{unknown stems} d(w)`. Surface verses
  with low effort even at equal known-ratio (their unknown words are easy).
- Optional **difficulty-weighted comprehension** behind a flag, where each unknown
  word's miss is scaled by `d(w)` — a closer i+1 estimate than a flat ratio.

```python
def verse_effort(verse, profile, now, decay) -> float: ...   # needs [lexical] extra
```

Degrades gracefully: if `wordfreq` is not installed, `d(w)=1` for all (effort =
unknown-word count) and a one-time warning is logged. Later upgrade path: a trained
SemEval-2021 LCP regressor behind the same function.

**Acceptance:** of two verses with identical known-ratio, the one whose unknown
words are rarer (lower Zipf) gets the higher `effort`; absent `wordfreq`, effort
equals the unknown-word count.

## 4. Item 19 — semantic-similarity "near-known" fallback

Reduce false negatives: an unknown verse word that is a close synonym of a known
word gets partial credit.

```
sim(w) = max over v in profile of cosine(emb(w), emb(v))
credit(w) = SIM_WEIGHT * sim(w)   if sim(w) >= SIM_TAU   else 0   # e.g. τ=0.6, weight=0.8
p_effective(w) = max(recall_prob(w), credit(w))
```

`credit` is capped below 1 so similarity never fully substitutes for real
knowledge. Plug `p_effective` into the weighted comprehension of §2.

**Embeddings:** keep light — spaCy `en_core_web_md` vectors or gensim GloVe, **not**
torch/transformers (image-size constraint, decision §0.4). Gate behind `[semantic]`
extra; if absent, `credit(w)=0` (current behavior) + a warning.

**Acceptance:** with a profile containing "happy", an unknown "joyful" verse word
gets nonzero credit (sim above τ); "table" gets ~0; without the extra installed,
all credits are 0 and scores match §2.

## 5. Packaging

```toml
[project.optional-dependencies]
lexical  = ["wordfreq>=3"]
semantic = ["spacy>=3", "en_core_web_md @ https://.../en_core_web_md-3.x.x.tar.gz"]
```

Core install stays polars/nltk/dash only. CI runs the core suite; add an optional
job that installs extras and runs the §3/§4 tests (marked `@pytest.mark.lexical` /
`semantic`, skipped when the import is unavailable).

## 6. Sequencing & dependencies

```
P5.0 review-log model (§1)  ──┬──► P5.1 half-life + weighted comprehension (§2 core)
                              │         └──► P5.2 study queue / scheduling (§2 queue)
                              ├──► P5.3 lexical complexity (§3, [lexical])
                              └──► P5.4 semantic fallback (§4, [semantic])
```

P5.0 is the hard dependency for everything. P5.1 is the core value. P5.2–P5.4 are
independent of each other and can be done in any order / by separate sessions. A
lighter model can take P5.0 → P5.1 → P5.2 in sequence; P5.3 and P5.4 are
self-contained add-ons.

## 7. Open questions for the user

1. **Profile storage:** flat `reviews.csv` (recommended — matches the
   CSV/polars/local-file style) or SQLite (better for large logs / concurrent
   writes)?
2. **Where do review outcomes come from?** A manual `--review` CLI for now, or do
   you want a quiz mode in the Dash app to generate them (separate, larger effort)?
3. **Embedding dependency budget:** is a ~40 MB spaCy `md` model acceptable, or
   should item 19 stay deferred to keep the image lean?
4. **Decay default:** opt-in via `--decay` (recommended, fully backward compatible)
   or on whenever a `reviews.csv` exists?
