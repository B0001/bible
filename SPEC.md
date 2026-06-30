# Improvement Spec: Vocabulary-Graded Bible Reader

Status: draft · Owner: TBD · Last updated: 2026-06-30

## 1. Goal

Turn this exploratory collection of scripts into one coherent, runnable
application: **given a user's known-word list, rank Bible verses by comprehension
rate and present the ~95%-comprehension verses for vocabulary growth.**

The current repo has the right idea but three half-finished pieces that don't
connect: a single-machine parser, a Spark parser, and a Dash UI that still shows
tutorial boilerplate. This spec defines the target and the steps to get there.

## 2. Current state (what's actually wrong)

### Core logic
- **Two divergent implementations** of the same scoring (`parser.py` pandas vs
  `parser.scala` Spark) with *different formulas*:
  - `parser.py`: exact token-membership count ÷ total tokens.
  - `parser.scala`: `HashingTF` term-freq dot product ÷ total terms — subject to
    hash collisions at `numFeatures=100000`, and counts repeats differently.
  There is no single source of truth.
- `parser.scala` computes `easyVerses` and `top10` but **never writes them** —
  the actual product (the graded verse list) is discarded.
- `parser.py` does Snowball stemming and builds `unique_stems.csv` but never uses
  the stems in scoring — dead computation. Stemming should either feed scoring
  (match "run"/"running") or be removed.
- Hardcoded local paths (`~/bible`, `~/Downloads/nasb.txt`), and `app_dir`
  (`~/bible`) is written to without being created.
- Required input `my_vocab.txt` is not in the repo and has no schema/sample.

### UI
- `dash_app.py` is **unmodified Dash tutorial scaffolding**: title reads "US
  Agriculture Exports (2011)", dropdowns/sliders are NYC/MTL/SF placeholders,
  none of it is wired to verse data.
- Uses deprecated `dash_core_components` / `dash_html_components` imports
  (removed in Dash 2.x; should be `from dash import dcc, html`).
- Reads `lookup_df.csv`, which doesn't contain `comprehension_rate` (that column
  is added in-memory in `parser.py`, never persisted).
- `debug=True` hardcoded; no host/port config for container deployment.

### Packaging & infra
- No dependency manifest (`requirements.txt` / `pyproject.toml`); versions are
  installed ad hoc inside Dockerfiles.
- **Five Dockerfiles** (`Dockerfile`, `.spark`, `.al`, `.llvm`, `.distroless`),
  all experiments, none authoritative; base images are EOL (Python 3.5, Spark
  2.4.4, Scala 2.11).
- No tests, no CI, no linting.
- `README.md` is one line (`# bible`).

## 3. Target architecture

```
vocab file (user) ─┐
                   ├─► scoring pipeline ─► graded_verses.parquet/csv ─► Dash UI
NASB text ─────────┘   (one implementation,    (verse, ref, score)      (browse/filter
                        canonical formula)                                by score)
```

Decisions locked in:
- **Single-machine, polars-based pipeline is canonical.** The dataset is ~31k
  verses — it fits in memory, no cluster needed. Drop Spark (`parser.scala`) and
  rewrite the pipeline on **polars** (not pandas). One comprehension formula,
  documented below.
- **Stem/lemma-aware matching.** A word in the vocab counts all its
  morphological variants as known (e.g. vocab "run" → "running", "ran" count).
  Both the verse tokens and the vocab are stemmed (Snowball, already imported)
  before membership comparison. This makes the existing stemming code load-bearing
  instead of dead.
- **Canonical comprehension formula:** for each verse, tokenize → lowercase →
  stem; `comprehension_rate = (# verse stems present in the stemmed vocab set) /
  (total verse stems)`; verses with `< passage_min_verse_length` tokens score 0.
- **Persist the product.** The pipeline writes a single output with columns
  `ref, verse, comprehension_rate` (and optionally token counts), which the UI
  consumes directly.
- **Parameterize all paths and the vocab** via CLI args / env vars / config —
  no `~/Downloads` hardcoding.

## 4. Work items

Phased; each item lists acceptance criteria.

### Phase 1 — Make the core correct and runnable ✅ DONE
1. ✅ **Rewrite the pipeline on polars** as a parameterized module with a `main()`
   and CLI args (`--bible`, `--vocab`, `--out`, `--known-rate`). Implement the
   canonical stem-aware formula from §3: stem verse tokens and vocab with the
   Snowball stemmer, score by stemmed-membership ratio. Create output dirs before
   writing. _Done when:_
   `python parser.py --bible nasb.txt --vocab my_vocab.txt --out graded.csv`
   produces a CSV with `ref, verse, comprehension_rate`.
2. ✅ **Remove Spark and pandas.** Deleted `parser.scala`, `Dockerfile.spark`,
   `spark-k8s-instructions.sh`; EF top-100 vocab rescued into
   `sample/my_vocab.txt`. No pandas/pyspark dependency.
3. ✅ **Commit sample data**: `sample/nasb_sample.txt` (12 verses) and
   `sample/my_vocab.txt`. NASB source URL documented in SPEC/CLAUDE.
4. ✅ **Verify stemming behavior** end-to-end — covered by
   `test_stem_variant_counts_as_known` (vocab "run" marks "running" known).

   _Also delivered early from later phases:_ `pyproject.toml` + `requirements.txt`
   dependency manifests (item 7), pytest test suite (item 9). Note: a
   `pyproject.toml` was required now anyway to anchor pytest's rootdir away from
   an unrelated `~/Downloads/conftest.py` that stubs `polars`.

### Phase 2 — Wire up the UI ✅ DONE
5. ✅ **Rewrite `dash_app.py`** against real data: loads the graded CSV, real
   title, a comprehension **RangeSlider** (default 90–100%) and a reference/text
   search box, both feeding a sortable `DataTable` via callback. Placeholder
   controls removed.
6. ✅ **UI deployment-ready**: input path (`BIBLE_GRADED_CSV`) and
   host/port/debug read from env vars; migrated to Dash 2.x+ imports
   (`from dash import Dash, dcc, html, ...`). Verified: server boots and serves
   HTTP 200.

### Phase 3 — Packaging, infra, hygiene ✅ DONE
7. ✅ **Dependency manifest**: `pyproject.toml` (canonical) + `requirements.txt`
   (Docker), deps polars/nltk/dash, dev extras pytest/ruff. No pandas/pyspark.
8. ✅ **Collapsed to one Dockerfile** on `python:3.12-slim`: installs
   requirements, downloads NLTK stopwords, pre-grades the sample data, and
   `CMD`s the Dash app on `0.0.0.0:8050`. Deleted the alpine/distroless/amazonlinux
   experiments (Spark one was removed in Phase 1).
9. ✅ **Tests** in `test_parser.py` (exact rates, empty verse → 0, min-length
   guard, case-insensitivity, stem-variant match, malformed-line skipping) —
   9 passing.
10. ✅ **CI** at `.github/workflows/ci.yml`: installs, downloads NLTK data, runs
    `ruff check` + `pytest` on push/PR.
11. ✅ **README** rewritten: what it does, how-it-works diagram, quickstart,
    own-data usage, config, Docker, dev commands.

### Phase 4 — Product depth (optional/stretch)
12. **Stem- or lemma-aware matching** so morphological variants count as known.
13. **Per-passage (not just per-verse) scoring** using `passage_min_verse_length`
    and contiguous-window aggregation, to surface readable *passages*.
14. **"Learn the next word" feature**: rank the unknown words by how many
    near-95% verses they would unlock.
15. **Pluggable translations / vocab profiles**; persist user vocab across runs.

### Phase 5 — Research-backed personalization (proposed)
Refines items 14–15 with approaches from the vocabulary-acquisition / CALL
(computer-assisted language learning) literature instead of ad hoc heuristics.
Not started; sequence after Phase 4 since each item below depends on persisted
per-user vocab state (item 15).

16. **Per-word mastery model instead of a static known/unknown vocab file.**
    Replace the binary `my_vocab.txt` set with a per-word mastery probability
    learned from review history (knowledge-tracing style: Bayesian Knowledge
    Tracing [Corbett & Anderson 1994, pre-arXiv] or Deep Knowledge Tracing
    [arXiv:1506.05908]). `comprehension_rate` becomes a probability-weighted sum
    over verse stems rather than a hard membership count. Depends on item 15
    (persisted vocab) existing first.
17. **Spaced-repetition scheduling for "learn the next word"** (refines item 14):
    rank unknown words using a trainable forgetting-curve model — e.g. Duolingo's
    Half-Life Regression (Settles & Meeder, "A Trainable Spaced Repetition Model
    for Language Learning", ACL 2016 — ACL Anthology P16-1174; not on arXiv, code
    at github.com/duolingo/halflife-regression) — instead of pure unlock-count,
    so the ranking accounts for *when* a word is likely to be forgotten, not just
    how many verses it gates.
18. **Lexical-complexity-aware scoring.** Augment `comprehension_rate` with a
    per-word difficulty signal (cf. SemEval-2021 Task 1: Lexical Complexity
    Prediction, arXiv:2106.00473) so two verses with the same raw known-word
    ratio can be told apart by how *hard* their unknown words are — closer to a
    real i+1 estimate than a flat ratio.
19. **Semantic-similarity fallback for unseen words.** Use word/sentence
    embeddings to treat a verse word as "near-known" if it's a close synonym of
    a vocab word, reducing false negatives from `parser.py`'s exact
    stem-membership check (e.g. a known noun whose adjective form isn't a
    Snowball-recognized variant).

Open question for this phase: items 16–19 need a place to store per-user review
history (not currently modeled anywhere in this repo) — likely a prerequisite
sub-item before 16, not yet broken out.

_Citations verified via arXiv/ACL search 2026-06-30: DKT (1506.05908) and
SemEval-2021 LCP (2106.00473) are real arXiv papers; the Settles & Meeder HLR
paper is real but lives on the ACL Anthology, not arXiv; Bayesian Knowledge
Tracing predates arXiv (1994) and has no arXiv entry._

## 5. Out of scope (for now)
- Authentication / multi-user accounts.
- Cloud cost optimization of the build images (the Dockerfile size experiments).
- Non-English Bibles.

## 6. Open questions
- Where does output live — local file, S3 (`s3://bible-app-dash/`), or both?
- Is there an existing `my_vocab.txt` / `nasb.txt` to commit as sample data?
- Stemming choice: Snowball (already imported) sufficient, or move to a lemmatizer
  (WordNet) for more accurate variant matching? Snowball is the default.

_Resolved:_ single-machine **polars** pipeline (Spark dropped); **stem/lemma-aware**
matching is the canonical formula.
