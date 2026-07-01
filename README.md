# Bible Reader — graded by your vocabulary

Find Bible verses you can actually read. Given a list of words you already know,
this scores every verse by its **comprehension rate** — the fraction of its words
in your vocabulary — so you can read at the ~95% "sweet spot" that language
research associates with effective vocabulary growth.

Matching is **stem-aware**: knowing `run` also credits `running` and `ran`.

## How it works

```
your vocab ─┐
            ├─► parser.py (polars + NLTK) ─► out/graded.csv ─► dash_app.py (web UI)
Bible text ─┘     stem & score each verse      ref,verse,rate    filter & search
```

- **`parser.py`** — scoring pipeline. Input is a Bible text with one
  `verse text -- reference` per line and a whitespace-separated vocab file.
- **`dash_app.py`** — web UI to filter verses by comprehension rate and search.
- **`sample/`** — bundled sample data so everything runs out of the box.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m nltk.downloader stopwords

# Grade the bundled sample verses
.venv/bin/python parser.py \
    --bible sample/nasb_sample.txt \
    --vocab sample/my_vocab.txt \
    --out out/graded.csv

# Launch the web UI at http://127.0.0.1:8050
.venv/bin/python dash_app.py
```

### Using your own data

- **Vocab:** any file of whitespace-separated words (`sample/my_vocab.txt` is the
  EF top-100 English words).
- **Bible text:** lines of `verse text -- reference`. The full NASB used in
  development comes from
  [`tushortz/variety-bible-text`](https://raw.githubusercontent.com/tushortz/variety-bible-text/master/bibles/nasb.txt).

```bash
python parser.py --bible nasb.txt --vocab my_words.txt --out out/graded.csv \
    --known-rate 0.95 --min-verse-length 1
```

## Learner analytics

`parser.py` surfaces several learning aids alongside per-verse grading:

```bash
# Score contiguous 3-verse passages (find a readable multi-verse stretch)
python parser.py --bible nasb.txt --vocab my_words.txt --out out/graded.csv \
    --passage-window 3 --passage-out out/passages.csv

# Rank the 20 highest-leverage words to learn next (each ranked by how many
# almost-readable verses learning it alone would unlock)
python parser.py --bible nasb.txt --vocab my_words.txt --out out/graded.csv \
    --next-words 20 --next-words-out out/next_words.csv

# Add an effort column (how hard are the unknown words?) — requires [lexical]
python parser.py --bible nasb.txt --vocab my_words.txt --out out/graded.csv \
    --effort
```

## Personalization (spaced repetition)

A vocab file is a *profile*. Point `--vocab` at different files for different
learners or study goals. Profiles grow across runs:

```bash
# Persist newly learned words into the profile
python parser.py ... --learn grace mercy covenant

# Log a review outcome (correct/wrong) for spaced-repetition scheduling
python parser.py ... --review grace correct

# Grade with time-decayed recall instead of a binary known/unknown set
python parser.py ... --decay

# Get a prioritised study queue: due reviews first, then new words to unlock
python parser.py ... --study 20 --study-out out/study.csv

# Grant partial credit to verse words that are close synonyms of known words
# (requires [semantic]: pip install '.[semantic]' + python -m spacy download en_core_web_md)
python parser.py ... --semantic
```

Review history is stored in `<vocab>.reviews.csv` alongside the vocab file and
is excluded from version control (`.gitignore`).

## Configuration

`parser.py` flags:

| Flag | Default | Description |
|---|---|---|
| `--bible` | *(required)* | `verse -- ref` text file |
| `--vocab` | *(required)* | whitespace-separated vocab / profile file |
| `--out` | *(required)* | output CSV path |
| `--known-rate` | `0.95` | comprehension threshold for the "easy" summary |
| `--min-verse-length` | `1` | verses shorter than this score 0 |
| `--passage-window N` | `1` (off) | sliding window size for passage scoring |
| `--passage-out` | — | output path for passage CSV (required if window > 1) |
| `--next-words N` | `0` (off) | top N unlock-ranked words to learn next |
| `--next-words-out` | — | output path for next-words CSV (required if N > 0) |
| `--learn WORD …` | — | append word(s) to the vocab profile |
| `--review WORD correct\|wrong` | — | log a review event |
| `--decay` | off | grade with time-decayed recall from the review log |
| `--study N` | `0` (off) | produce a study queue of top N items |
| `--study-out` | — | output path for study queue CSV (required if N > 0) |
| `--effort` | off | add a lexical-effort column to the graded output |
| `--semantic` | off | grant partial credit to semantically similar words |

`dash_app.py` env vars: `BIBLE_GRADED_CSV` (default `out/graded.csv`),
`DASH_HOST` (default `127.0.0.1`), `DASH_PORT` (default `8050`), `DASH_DEBUG`.

## Docker

```bash
docker build -t bible-reader .
docker run -p 8050:8050 bible-reader   # serves the pre-graded sample data
```

## Development

```bash
.venv/bin/pytest                    # core tests
.venv/bin/pytest -m lexical         # requires [lexical] extra
.venv/bin/pytest -m semantic        # requires [semantic] extra + en_core_web_md
.venv/bin/ruff check .              # lint
```

Optional extras:

```bash
pip install -e '.[lexical]'         # wordfreq (verse_effort / --effort)
pip install -e '.[semantic]'        # spaCy (--semantic)
python -m spacy download en_core_web_md
```

See [`SPEC.md`](SPEC.md) for the roadmap and design decisions, and
[`CLAUDE.md`](CLAUDE.md) for an architecture orientation.
