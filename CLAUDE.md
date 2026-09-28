# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

A vocabulary-graded Bible reader for **English, Biblical Hebrew, and Koine
Greek**. The core idea: given a user's known-words list ("your vocab"), score
every verse by its **comprehension rate** — the fraction of its words the user
already knows — and surface verses at the language-learning sweet spot (~95%
known words, "i+1"). Texts are `verse -- reference` line format: NASB English
(from `tushortz/variety-bible-text`), WLC Hebrew OT, Byzantine Greek NT, and
the Delitzsch Hebrew NT (fetched by `scripts/convert_*.py`).

See `SPEC.md` for the improvement plan and locked-in decisions; this section
describes the current state.

## Architecture

the audio files are in https://4.dbt.io/open-api-4.json

A single-machine **polars** pipeline scores verses, writes a CSV, and a Dash app
displays it. (An earlier Spark implementation, `parser.scala`, was removed — see
SPEC.md §4; the dataset fits in memory so no cluster is needed.)

- **`parser.py`** — the canonical scoring pipeline, a parameterized module with a
  `main()` and CLI args. Reads a `<verse> -- <reference>` Bible file and a
  whitespace-separated vocab file. **Language-aware tokenization** via
  `tokenize(text, lang)` / `tokenize_and_stem(text, lang)` and the `--lang
  en|he|el` flag: English is lowercased + Snowball-stemmed (vocab "run" →
  "running", "ran"); Hebrew strips niqqud/cantillation (U+0591–U+05C7) and
  extracts consonant runs (no stemmer — consonantal forms already conflate
  variants); Greek NFD-normalizes and strips combining diacritics. Per verse:
  `comprehension_rate = (# verse forms in the vocab form set) / (total forms)`;
  verses shorter than `--min-verse-length` score 0. Writes `ref, verse,
  comprehension_rate, known_count, total_count` to the `--out` CSV in **one
  tokenization pass** (the counts feed the UI's longest-passage button). Key
  functions: `tokenize`, `tokenize_and_stem`, `comprehension_rate`, `load_bible`, `load_vocab`.
  `--longest-passage-out PATH` writes the single longest contiguous verse span
  whose combined rate ≥ `--known-rate`, via `longest_span()` — an O(n)
  prefix-sum + monotone-stack algorithm shared with the Dash UI (import it,
  don't re-implement it). With
  `--passage-window N` (+ required `--passage-out`), also writes per-passage
  scores: `grade_passages()` slides an N-verse window one verse at a time and
  scores each window's concatenated text as a single unit, so multi-verse
  passages near the comprehension sweet spot can be surfaced, not just
  isolated verses. With `--next-words N` (+ required `--next-words-out`), also
  writes a "what to learn next" ranking: `next_words_to_learn()` tallies, for
  every under-threshold verse, which single unknown stem would push it to or
  above `--known-rate` if learned, and ranks stems by how many verses they'd
  unlock.
  `--word-freq-out PATH` writes the Bible's own frequency table via
  `word_frequencies()` (`form, rank, count, verse_count, cum_coverage`) — one
  per language/translation, since the point is what *this* corpus needs, not
  general word frequency. `--passage-leverage N --passage-leverage-out PATH`
  ranks unknown stems by `passage_leverage()`: the increase, over the reader's
  current vocab, in how many verses sit inside a run of `--min-span` (default 5)
  consecutive verses whose combined rate clears `--known-rate`
  (`readable_coverage()`, the same prefix-sum sweep as `longest_span` but
  unioning every maximal run instead of keeping the widest). Runs are scored per
  book and only the 200 most frequent unknown forms *of each book* are tried —
  a global cap would discard exactly the corpus-rare, book-local words this is
  for (Mahlon and Chilion are what unlock Ruth). The graded CSV also carries
  `corpus_difficulty_rank`: `verse_difficulty` with no known set, so it grades
  the text rather than the reader. `--vocab PATH` doubles as a vocab *profile* — different files are
  different profiles/translations, nothing special needed to swap them.
  `--learn WORD [WORD ...]` calls `update_vocab_file()` to persist newly
  learned words into that profile (case-insensitive dedup, applies to the same
  run too). **Phase 5 personalization** (see `PHASE5_DESIGN.md`): `--review WORD
  correct|wrong` (`record_review()`) logs a review to `<vocab>.reviews.csv`, and
  `--decay` grades by time-decayed recall probability
  (`weighted_comprehension_rate()` over a half-life model:
  `load_profile`/`recall_prob`/`half_life`) instead of the binary known set.
  `--study N --study-out PATH` produces a combined study queue via `study_queue()`:
  due reviews (recall prob < 0.5, most-forgotten first) then new-word unlock
  ranking; columns `stem, action, score, reason`. `--effort` adds a per-verse
  `effort` column via `verse_effort()` — sum of `d(w)*(1-recall_prob)` where
  `d(w) = clamp(1 - zipf/8, 0, 1)`; requires `pip install '.[lexical]'` (wordfreq),
  degrades to d=1 with a warning when absent. `--semantic` grants partial credit to
  unknown verse tokens similar to known vocab words via `SemanticModel` /
  `load_semantic_model()`; requires `pip install '.[semantic]'` (spaCy
  `en_core_web_md`), embeds **surface forms** (not stems), degrades to credit=0
  when absent, and is **English-only** (`lang != "en"` returns None with a
  warning). All Phase 5 features are language-aware: `record_review`,
  `load_profile`, and `_word_difficulty` take `lang` so Hebrew/Greek profiles
  key by stripped forms and wordfreq uses the right wordlist. All Phase 5 flags
  are opt-in; without them scoring is byte-identical to the binary path.
  Review logs (`*.reviews.csv`) are gitignored.
- **`dash_app.py`** — Plotly Dash web front end. Loads all Bibles listed in
  `bibles.toml` (stdlib `tomllib`; entries with missing CSVs are skipped with a
  warning; falls back to `BIBLE_GRADED_CSV`, default `out/graded.csv`). UI: a
  Bible dropdown, comprehension-rate RangeSlider, **nikudim-insensitive search**
  (a `verse_plain` column is built at load via `_strip_marks`; the needle is
  stripped too), per-verse **read tracking** (mark read/unread buttons,
  unread-only filter, progress line), a "Find longest passage" button (imports
  `longest_span` from parser), and **RTL rendering** for Hebrew
  (`_cell_styles(lang)` sets `direction: rtl` on the verse column when the
  Bible's `lang == "he"`).
  Host/port/debug come from env vars (`DASH_HOST`, `DASH_PORT`, `DASH_DEBUG`).

  **Read-tracking backend is dual.** `DATABASE_URL` (a `postgres://` DSN)
  selects Postgres; unset, it falls back to SQLite at `READS_DB` (default
  `reads.db`, gitignored). Both run the same SQL — only the parameter
  placeholder (`?` vs `%s`) and the timestamp column type differ, which is why
  the shared statements use standard `ON CONFLICT DO NOTHING` and
  `CURRENT_TIMESTAMP` rather than the SQLite-only `INSERT OR IGNORE` /
  `datetime('now')`. `psycopg_pool` is imported lazily inside `_pool()`, so a
  SQLite-only install never needs it. Postgres exists because Kubernetes runs
  multiple replicas and SQLite on a shared volume permits only one writer; see
  `k8s/README.md`. Every backend fault is caught and logged, degrading to
  "nothing marked read" rather than taking the reader down.
- **`rhyme.py`** — rhymed composites, separate from grading (Phase 16, see
  `PHASE16_DESIGN.md`). `stitch()` picks one translation per verse to fit a
  rhyme scheme plus a register-switch penalty (exact: a Viterbi pass across
  strophes, with every assignment enumerated inside a strophe — so non-adjacent
  schemes like ABAB are handled). `substitute()` swaps only a line's final word
  for a filtered WordNet synonym. `rhyme_classes()` / `laplacian()` give rhyme
  classes as components of the rhyme graph, where nullity(L) = the class count.
  Rhyme = shared CMUDict tail from the last *primary*-stressed vowel, over all
  pronunciations; identical words never rhyme. Needs NLTK `cmudict`,
  `wordnet` and `stopwords`; `test_rhyme.py` skips without them. The measured
  finding: the translations rhyme on ~0.3% of scheme pairs and neither engine
  gets past ~0.6%, so don't describe its output as "rhyming Bible text".
- **`scripts/`** — standalone converters that download source texts into
  `data/` (gitignored): `convert_wlc.py` (Hebrew OT from openscriptures/morphhb
  OSIS XML; strips morphhb's `/` morpheme markers), `convert_gnt.py` (Greek NT
  from byztxt CSV files), `convert_delitzsch_nt.py` (Hebrew NT from
  HebrewNewTestament/HebDelitzsch OSIS).
- **`scripts/export_dataset.py`** — builds a HuggingFace-ready dataset in
  `out/dataset/` from `site/data/` (run `export_static.py` first); uploading
  needs `pip install '.[dataset]'` (the Hub CLI is an extra, not a core dep —
  the Docker image serves the Dash app and has no use for it): `books.csv`
  (one row per USFM book code; curated `english`/`hebrew`/`greek` name columns
  plus one column per translation holding the book string as it literally
  appears in that text's refs, so those stay joinable against the Parquet
  `ref` — `wlc`/`gnt` ship Latin abbreviations, the real names are in the
  curated columns) plus `data/<id>/train-00000.parquet` per translation and a README with
  the HF `configs:` block. CSV for the tiny book table because it exists to
  attract corrections; Parquet for the ~370k metric rows because it is the
  Hub's native format and sidesteps CSV's encoding traps around Hebrew/Arabic.
  **Exports no verse text** — refs and derived metrics only, which is what
  keeps copyrighted translations publishable. Only vocabulary-independent
  columns ship (`total_count`, `difficulty_rank`); `comprehension_rate` and
  `known_count` describe one reader's vocab, not a text. Also writes
  `data/difficulty_by_translation/` — `difficulty_rank` pivoted to one column
  per translation over a `(book_usfm, chapter, verse)` key, null where a canon
  lacks the verse. Word-frequency tables stay local: publishing a full form
  list of a copyrighted translation is a different question from publishing
  refs and ranks. Book mapping is
  positional per `BOOK_ORDER`, guarded by `validate_books()` — canons differ
  (Statenvertaling inserts Baruch; the Russian Synodal text carries the
  Orthodox deuterocanon and puts the Catholic epistles before the Pauline
  ones), so a source whose order drifts fails loudly instead of mislabelling.
- **`test_parser.py` + `test_dash_app.py`** — 150+ pytest unit tests covering the
  scoring core, tokenizers, Phase 5 recall model, study queue, lexical effort,
  semantic credit, longest passage, corpus ranks / verse difficulty (Phase 12),
  and the Dash app's pure logic (mark stripping, read-tracking round-trip).
  `test_dash_app.py` sets `BIBLE_GRADED_CSV`/`READS_DB` env vars **before
  importing dash_app** — keep it that way, the module loads data at import
  time. Tests marked `@pytest.mark.lexical` / `@pytest.mark.semantic` skip when
  those extras are absent; CI runs them in separate jobs that install the extras.
- **`test_site_smoke.py`** — Playwright browser smoke tests (marked
  `@pytest.mark.e2e`) that serve `site/` and drive it in headless Chromium:
  init actually runs (catches strict-mode ReferenceErrors unit tests can't),
  the read route auto-renders the longest passage at the current level, Done
  marks verses read and advances the queue, the slider rebuilds the queue,
  browse/settings routes show the table and vocab box, no horizontal scroll
  at 360px, dark-mode background. Skips when playwright (`pip install
  '.[e2e]'` + `playwright install chromium`) or exported site data
  (`scripts/export_static.py`) is missing.
- **`sample/`** — runnable sample data: `nasb_sample.txt` (12 verses),
  `my_vocab.txt` (EF top-100 English words), `hebrew_vocab.txt`,
  `greek_vocab.txt` (starter vocabularies for the original languages).

- **`skills/` + `.claude-plugin/`** — the repo doubles as a Claude Code plugin
  marketplace shipping four Agent Skills: `vocabulary-grading`,
  `multilingual-text-tokenization`, `asr-forced-alignment`, and
  `derived-dataset-publishing`. They are the *portable* statement of this
  project's domain knowledge — formulas, algorithms, traps, and measured
  negative results — with this codebase as the reference implementation, so a
  skill must never assume the reader has these files. `.claude-plugin/
  marketplace.json` lists the single `graded-reader` plugin with
  `"source": "./"` and an explicit `skills` array (root-level `skills/`, per the
  marketplace-root pattern); `.claude-plugin/plugin.json` is the plugin
  manifest. Neither pins a `version`, so installs track the latest commit —
  adding one means bumping it on every release. Validate with `claude plugin
  validate .` after editing either manifest. When you change an algorithm in
  `parser.py` or `scripts/`, check whether the corresponding SKILL.md still
  tells the truth; the numbers in them (cost, corpus size, error rates) are
  measured, not illustrative.

**Data flow:** `scripts/convert_*.py` → `data/*.txt` → `parser.py --lang X` →
`out/<bible>_graded.csv` (listed in `bibles.toml`) → `dash_app.py`.
A second branch feeds the static site and the published dataset:
`data/*.txt` → `scripts/export_static.py` → `site/data/*.json` →
`scripts/export_dataset.py` → `out/dataset/` (HuggingFace).

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# Run the scoring pipeline on the bundled sample data
.venv/bin/python parser.py --bible sample/nasb_sample.txt \
    --vocab sample/my_vocab.txt --out out/nasb_graded.csv

# Hebrew / Greek (fetch texts first via scripts/convert_*.py)
.venv/bin/python parser.py --bible data/wlc.txt \
    --vocab sample/hebrew_vocab.txt --out out/wlc_graded.csv --lang he

# Tests
.venv/bin/python -m pytest

# Dash web app (loads bibles.toml entries whose CSVs exist, binds 127.0.0.1:8050)
.venv/bin/python dash_app.py
```

**Heads up on pytest:** there is an unrelated `conftest.py` in the parent
`~/Downloads` directory that stubs out `polars` with a mock. The project's own
`pyproject.toml` anchors pytest's rootdir here so that conftest is not loaded —
always run pytest from the project root, and keep the `[tool.pytest.ini_options]`
block in `pyproject.toml`.

## Deployment / infra

One `Dockerfile` on `python:3.12-slim`: installs from `requirements.txt`,
downloads NLTK stopwords, copies `bibles.toml` + `scripts/`, pre-grades the
sample data to `out/nasb_graded.csv`, and serves via gunicorn
(`dash_app:server`) on `0.0.0.0:8050` with a `/health` endpoint.
`requirements.txt` mirrors the core pyproject deps plus `psycopg`;
`pyproject.toml` is canonical.

The image runs **non-root (uid 10001) with a read-only root filesystem**, which
constrains what it may assume:
- `HOME=/tmp`. The account's home is `/home/app`, which does not exist and is
  read-only regardless; gunicorn's control server opens a socket under `$HOME`
  at startup and logs an error without this.
- Nothing may write to `/app`. The only writable path is the `/tmp` emptyDir.
- Neither `DATABASE_URL` nor `READS_DB` is baked in, deliberately — see below.

`k8s/` holds Kustomize manifests (`base` + `dev`/`prod` overlays):
Deployment/Service/Ingress/ConfigMap/PDB, plus an HPA in prod. **There is no
PVC.** Read tracking moved to Postgres precisely so the pods hold no local
state; `DATABASE_URL` is injected from a Secret named `bible-reader-db`. The dev
overlay ships a dev-only in-cluster Postgres StatefulSet; prod expects a managed
instance with the Secret created out of band. `k8s/README.md` covers the
connection-budget ceiling (`replicas × workers × READS_POOL_MAX`), which is what
now bounds `maxReplicas`.

Postgres tests live in `test_reads_postgres.py` (marker `postgres`), skipped
unless `TEST_DATABASE_URL` is set. They run dash_app in a **subprocess** on
purpose: the backend is resolved at import time, so an in-process reload would
swap it out from under `test_dash_app.py` in the same session.


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:970c3bf2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->
