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
  unlock. `--vocab PATH` doubles as a vocab *profile* — different files are
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
  stripped too), per-verse **read tracking** in SQLite (`READS_DB`, default
  `reads.db` — gitignored; mark read/unread buttons, unread-only filter,
  progress line), a "Find longest passage" button (imports `longest_span` from
  parser), and **RTL rendering** for Hebrew (`_cell_styles(lang)` sets
  `direction: rtl` on the verse column when the Bible's `lang == "he"`).
  Host/port/debug come from env vars (`DASH_HOST`, `DASH_PORT`, `DASH_DEBUG`).
- **`scripts/`** — standalone converters that download source texts into
  `data/` (gitignored): `convert_wlc.py` (Hebrew OT from openscriptures/morphhb
  OSIS XML; strips morphhb's `/` morpheme markers), `convert_gnt.py` (Greek NT
  from byztxt CSV files), `convert_delitzsch_nt.py` (Hebrew NT from
  HebrewNewTestament/HebDelitzsch OSIS).
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
  the Bible dropdown populates, verses + learn-next chips render, chip taps
  rescore, no horizontal scroll at 360px, dark-mode background. Skips when
  playwright (`pip install '.[e2e]'` + `playwright install chromium`) or
  exported site data (`scripts/export_static.py`) is missing.
- **`sample/`** — runnable sample data: `nasb_sample.txt` (12 verses),
  `my_vocab.txt` (EF top-100 English words), `hebrew_vocab.txt`,
  `greek_vocab.txt` (starter vocabularies for the original languages).

**Data flow:** `scripts/convert_*.py` → `data/*.txt` → `parser.py --lang X` →
`out/<bible>_graded.csv` (listed in `bibles.toml`) → `dash_app.py`.

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

One `Dockerfile` on `python:3.12-slim`: installs from `requirements.txt` (core
deps only — optional extras are not in the image), downloads NLTK stopwords,
copies `bibles.toml` + `scripts/`, pre-grades the sample data to
`out/nasb_graded.csv`, and serves via gunicorn (`dash_app:server`) on
`0.0.0.0:8050` with a `/health` endpoint. `requirements.txt` mirrors the core
pyproject deps; `pyproject.toml` is canonical.
