# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

A vocabulary-graded Bible reader. The core idea: given a user's known-words list
("your vocab"), score every verse by its **comprehension rate** — the fraction of
its words the user already knows — and surface verses at the language-learning
sweet spot (~95% known words, "i+1"). Source text is the NASB Bible in
`verse -- reference` line format (sourced from the `tushortz/variety-bible-text`
GitHub repo).

See `SPEC.md` for the improvement plan and locked-in decisions; this section
describes the current state.

## Architecture

A single-machine **polars** pipeline scores verses, writes a CSV, and a Dash app
displays it. (An earlier Spark implementation, `parser.scala`, was removed — see
SPEC.md §4; the dataset fits in memory so no cluster is needed.)

- **`parser.py`** — the canonical scoring pipeline, a parameterized module with a
  `main()` and CLI args. Reads a `<verse> -- <reference>` Bible file and a
  whitespace-separated vocab file. **Scoring is stem-aware**: both verse tokens
  and the vocab are lowercased and Snowball-stemmed, so a vocab word counts all
  its morphological variants (vocab "run" → "running", "ran"). Per verse:
  `comprehension_rate = (# verse stems in the stemmed vocab set) / (total stems)`;
  verses shorter than `--min-verse-length` score 0. Writes `ref, verse,
  comprehension_rate` to the `--out` CSV. Key functions: `stem_tokens`,
  `comprehension_rate`, `grade`, `load_bible`, `load_vocab`. With
  `--passage-window N` (+ required `--passage-out`), also writes per-passage
  scores: `grade_passages()` slides an N-verse window one verse at a time and
  scores each window's concatenated text as a single unit, so multi-verse
  passages near the comprehension sweet spot can be surfaced, not just
  isolated verses. With `--next-words N` (+ required `--next-words-out`), also
  writes a "what to learn next" ranking: `next_words_to_learn()` tallies, for
  every under-threshold verse, which single unknown stem would push it to or
  above `--known-rate` if learned, and ranks stems by how many verses they'd
  unlock.
- **`dash_app.py`** — Plotly Dash web front end. Loads the graded CSV
  (`BIBLE_GRADED_CSV`, default `out/graded.csv`) and renders a sortable table
  filtered by a comprehension-rate RangeSlider and a reference/text search box
  (callback-driven). Host/port/debug come from env vars (`DASH_HOST`,
  `DASH_PORT`, `DASH_DEBUG`).
- **`test_parser.py`** — pytest unit tests for the scoring core (exact rates,
  empty verse, case-insensitivity, stem-variant matching).
- **`sample/`** — runnable sample data: `nasb_sample.txt` (12 verses) and
  `my_vocab.txt` (EF top-100 English words, rescued from the old Scala file).

**Data flow:** `parser.py` (vocab + Bible text) → graded CSV → `dash_app.py`.

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# Run the scoring pipeline on the bundled sample data
.venv/bin/python parser.py --bible sample/nasb_sample.txt \
    --vocab sample/my_vocab.txt --out out/graded.csv

# Tests
.venv/bin/python -m pytest

# Dash web app (reads out/graded.csv, binds 127.0.0.1:8050)
.venv/bin/python dash_app.py
```

**Heads up on pytest:** there is an unrelated `conftest.py` in the parent
`~/Downloads` directory that stubs out `polars` with a mock. The project's own
`pyproject.toml` anchors pytest's rootdir here so that conftest is not loaded —
always run pytest from the project root, and keep the `[tool.pytest.ini_options]`
block in `pyproject.toml`.

`start.py` is unrelated to the app logic — it's a boto3 helper to start/stop the
EC2 builder instance and print the SSH command:
```bash
python3 start.py --region us-east-1 --user ec2-user --pem key.pem [--instance_id i-xxxx] [--stop true]
```

## Deployment / infra

The remaining `Dockerfile.*` variants are exploratory attempts at a small image,
not a chosen standard — pick deliberately, don't assume one is "the" Dockerfile
(SPEC.md Phase 3 item 8 calls for collapsing them to one):
- `Dockerfile` — `FROM spark-py:spark-docker`, adds `dash` + `s3fs`. This was the
  Spark-on-k8s path; its base image was built by `spark-k8s-instructions.sh`,
  which has since been deleted (Spark/pandas were dropped per SPEC.md §4), so
  this Dockerfile can no longer be built as-is.
- `Dockerfile.al` (amazonlinux), `Dockerfile.llvm` (alpine), `Dockerfile.distroless`
  (Bazel + distroless base) — size/footprint experiments, also stale (e.g. install
  `pandas`/`fastparquet`, not the current polars/dash/nltk stack).

Supporting scripts: `aws-builder-userdata.sh` / `gcp-builder-userdata.sh`
(cloud-init bootstrap for a build VM), `minio-start.sh` (local S3-compatible store
for testing the bucket I/O). `requirements.txt` duplicates the pyproject deps;
`pyproject.toml` is canonical (see pytest note above).
