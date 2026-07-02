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
12. ✅ **Stem- or lemma-aware matching** — delivered in Phase 1; it is the
    canonical scoring formula (§3).
13. ✅ **Per-passage (not just per-verse) scoring**: `grade_passages()` slides a
    `--passage-window N`-verse window one verse at a time over the bible (file
    order) and scores each window as one unit via the same `comprehension_rate`
    formula over the concatenated text — surfaces readable multi-verse
    *passages*, not just isolated verses. CLI: `--passage-window` (default 1 =
    off) + `--passage-out`. Tested in `test_parser.py` (sliding behavior,
    combined-unit scoring, corpus-shorter-than-window edge case).
14. ✅ **"Learn the next word" feature**: `next_words_to_learn()` finds every
    verse currently below `--known-rate`, and for each unknown stem in it,
    checks whether adding *just that stem's* occurrences would push the verse's
    comprehension rate to or above the threshold ("unlocking" it). Tallies
    unlocks per stem across the corpus and returns the top N, ranked by unlock
    count descending. CLI: `--next-words N` (default 0 = off) + required
    `--next-words-out`. Tested in `test_parser.py` (single-word unlock,
    multi-verse ranking order, top-N truncation).
15. ✅ **Pluggable translations / vocab profiles; persist user vocab across
    runs.** `--bible` and `--vocab` were already plain file paths, so swapping
    translations or vocab profiles was just pointing at a different file — no
    new machinery needed there. The missing piece was *persistence*:
    `update_vocab_file()` + `--learn WORD [WORD ...]` appends newly-learned
    words to the `--vocab` file (case-insensitive dedup, creates the file if
    missing, one word per line), so a profile grows across runs instead of
    requiring manual editing. Learned words apply to the same run too (loaded
    after the update). Tested in `test_parser.py` (append, dedup, missing-file
    creation, persisted word actually affects `load_vocab`). Explicitly *not*
    built: auth/multi-user accounts (out of scope, §5) or a profile-selection
    UI — a profile is just a vocab file path.

### Phase 5 — Research-backed personalization ✅ DONE
Refines items 14–15 with approaches from the vocabulary-acquisition / CALL
(computer-assisted language learning) literature. Full implementation blueprint
in [`PHASE5_DESIGN.md`](PHASE5_DESIGN.md).

16. ✅ **Per-word mastery model.** `WordHistory` dataclass, `load_profile()`,
    `record_review()`, and `--review WORD correct|wrong` replace the binary
    vocab set with a per-word history log (`<vocab>.reviews.csv`). Half-life
    recall model (`half_life()`, `recall_prob()`) and `weighted_comprehension_rate()`
    score verses by mean recall probability; `--decay` enables time-decayed
    scoring. Backward-compatible: without `--decay` and with a seed-only profile
    the result is byte-identical to the binary formula.
17. ✅ **Spaced-repetition study queue** (refines item 14). `study_queue()`
    combines due reviews (recall prob < 0.5, most-forgotten first) with the
    existing unlock ranking into one ranked DataFrame (`stem, action, score,
    reason`). CLI: `--study N --study-out PATH`.
18. ✅ **Lexical-complexity-aware scoring.** `verse_effort()` sums
    `d(w) * (1 - recall_prob)` per verse token where `d(w) = clamp(1 -
    zipf_frequency(w) / 8, 0, 1)` via `wordfreq`. `--effort` adds an `effort`
    column to the graded output. Requires `pip install '.[lexical]'`; degrades
    to `d=1` (unknown-word count) with a warning when the extra is absent.
19. ✅ **Semantic-similarity fallback.** `SemanticModel` pre-computes spaCy
    `en_core_web_md` vectors for the vocab's *surface words* (not stems) so
    unknown verse tokens that are close synonyms of known words get partial
    credit: `p_effective = max(recall_prob, SIM_WEIGHT * cosine)` with
    `SIM_TAU = 0.6`, `SIM_WEIGHT = 0.8`. `--semantic` enables it. Requires
    `pip install '.[semantic]'`; degrades to `credit=0` when absent.

_Citations verified via arXiv/ACL search 2026-06-30: DKT (1506.05908) and
SemEval-2021 LCP (2106.00473) are real arXiv papers; the Settles & Meeder HLR
paper is real but lives on the ACL Anthology, not arXiv; Bayesian Knowledge
Tracing predates arXiv (1994) and has no arXiv entry._

## Phase 6 — Production hardening

Two blockers preventing production deployment; everything else is acceptable for
internal/low-traffic use.

### 6a. Gunicorn WSGI server ✅ DONE

**Problem:** `app.run()` uses Flask's single-threaded dev server. One slow request
blocks all others; Flask itself logs a warning that this is not for production.

**Fix:**
- Add `gunicorn>=21` to `pyproject.toml` core deps and `requirements.txt`.
- Expose `server = app.server` in `dash_app.py` (gunicorn entry point).
- Replace Dockerfile `CMD` with:
  `gunicorn --bind 0.0.0.0:${DASH_PORT:-8050} --workers 2 dash_app:server`
- Keep `app.run(...)` block under `if __name__ == "__main__":` for local dev.

Worker count: 2 is conservative and safe — each worker loads the full DataFrame
into memory, so this is memory-bound. Increase if CPU becomes the bottleneck.

_Acceptance:_ `docker run` serves requests through gunicorn; Flask dev-server
warning is gone; two concurrent requests complete in parallel.

### 6b. Health check endpoint ✅ DONE

**Problem:** No `/health` route. Load balancers (ALB, nginx, k8s liveness probe)
have no signal that the app is up and the CSV loaded successfully.

**Fix:** Three lines in `dash_app.py` after `df = load_graded(...)`:
```python
@app.server.route("/health")
def health():
    return "ok", 200
```

The endpoint lives after `df = load_graded(...)` so it only becomes reachable
once the data is loaded — a meaningful readiness signal, not just "process alive."

_Acceptance:_ `curl localhost:8050/health` returns `200 ok`; a curl to `/health`
before the CSV exists raises before the route is registered (startup fails fast).

## Phase 7 — Multilingual, Read Tracking, Longest Passage

Full design in [`PHASE7_DESIGN.md`](PHASE7_DESIGN.md). Four capabilities:

- **P7.0** ✅ Language-aware tokenizer (`--lang en|he|el`): Hebrew strips niqqud + extracts consonants; Greek NFD-normalizes and strips combining diacritics. `tokenize()` and `tokenize_and_stem()` added; `lang="en"` default everywhere — backward compatible. 57 tests pass.
- **P7.1** ✅ Data converters: `scripts/convert_wlc.py` (WLC Hebrew OT from openscriptures/morphhb OSIS XML), `scripts/convert_gnt.py` (Byzantine Greek NT from byztxt), `scripts/convert_modern_he_ot.py` (Mechon Mamre), `scripts/convert_delitzsch_nt.py` (Delitzsch Hebrew NT). Each is standalone and writes `verse -- ref` format.
- **P7.2** ✅ `bibles.toml` (stdlib `tomllib`) multi-Bible config; `dash_app.py` loads all configured CSVs at startup, skips missing with a warning; `dcc.Dropdown` Bible selector.
- **P7.3** ✅ Read tracking: `reads.db` SQLite (stdlib `sqlite3`), `reads(bible_id, ref, read_at)` table; "Read" column (✓) in DataTable; "Mark selected as read/unread" buttons; "Show unread only" toggle; progress counter "N of M verses at ≥95% read".
- **P7.4** ✅ `grade_longest_passage()` — O(n) prefix-sum + monotone deque in `parser.py`; `--longest-passage-out` CLI; graded CSV includes `known_count` and `total_count`; "Find longest passage" button in UI runs the same algorithm inline over those columns.

Five texts total: NASB English, Biblical Hebrew OT (WLC), Biblical Greek NT (Byzantine), Modern Hebrew OT (Mechon Mamre), Modern Hebrew NT (Delitzsch). `--lang he` covers all three Hebrew texts — niqqud-strip logic is identical for Biblical and modern Hebrew; vocabulary distribution differs, which is the learning value of having both.

## Phase 8 — Repo-wide improvement ✅ DONE

Full design in [`PHASE8_DESIGN.md`](PHASE8_DESIGN.md). Paid down Phase 7 debt —
every item was a confirmed defect found by inspection: ✅ commit Phase 7 +
gitignore `reads.db` (P8.0), ✅ strip morphhb `/` morpheme markers from WLC text
(P8.1), ✅ drop the broken Mechon Mamre converter (P8.2), ✅ language-aware
Phase 5 personalization — reviews/profiles/wordfreq were English-hardcoded
(P8.3), ✅ shared `longest_span()` dedupes the passage algorithm between parser
and UI (P8.4), ✅ one-pass grading — 23k WLC verses in ~0.8s, CSV byte-identical
(P8.5), ✅ RTL rendering for Hebrew driven by `bibles.toml` lang (P8.6),
✅ docs/Docker catch-up (P8.7), ✅ `test_dash_app.py` — 81 tests total (P8.8).

## Phase 9 — Static client-side reader ("viral-proof") ✅ DONE

Full design in [`PHASE9_DESIGN.md`](PHASE9_DESIGN.md). If traffic spikes, the
answer is less backend, not more: a static site on GitHub Pages that scores
verses **in the browser** against a vocab pasted into a textarea, with read
tracking in localStorage (plus JSON export/import for backup). Pre-tokenized
per-Bible JSON is produced by `scripts/export_static.py` reusing
`tokenize_and_stem`, so browser scores match the pipeline exactly. Zero
servers, ~$0/month, and it makes per-user state private by construction —
sidestepping the multi-user/auth blocker (§5). The Dash app stays as the local
power tool for the Phase 5 SRS stack. Includes a stopgap (P9.0): cap the Dash
table callback at 500 rows — today a wide filter ships ~6 MB of JSON per
request.

## Phase 9.5 — All languages

`--lang` accepts any ISO 639-1 code: the 15 NLTK Snowball languages (ar, da,
de, en, es, fi, fr, hu, it, nl, no, pt, ro, ru, sv) get stem-aware matching;
he/el keep their mark-stripping paths; Arabic additionally strips harakat and
renders RTL; anything else falls back to exact lowercased word forms (zh/ja/th
word segmentation is out of scope). `scripts/convert_getbible.py` fetches any
of ~117 translations in 63 languages from getbible.net (`--list` to browse).
Twelve Bibles are pre-configured in `bibles.toml`; the static site's manifest
carries per-language stopword lists and vendored Snowball JS stemmers so
browser stemming matches the pipeline.

## 5. Out of scope (for now)
- Authentication / multi-user accounts.
- Cloud cost optimization of the build images (the Dockerfile size experiments).
- Greek lemmatization (spaCy `el_core_news_md`) — Phase 7.5 candidate.
- UI tab overhaul — only additive changes in Phase 7.

## 6. Open questions

None outstanding.

_Resolved:_ single-machine **polars** pipeline (Spark dropped); **stem/lemma-aware**
matching (Snowball) is canonical; sample data (`sample/`) ships in the repo; output
paths accept `s3://` URIs (`pip install '.[s3]'` for `s3fs`).
