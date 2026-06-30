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

`parser.py` can also surface two learning aids alongside the per-verse grading:

```bash
# Score contiguous 3-verse passages (find a readable multi-verse stretch)
python parser.py --bible nasb.txt --vocab my_words.txt --out out/graded.csv \
    --passage-window 3 --passage-out out/passages.csv

# Rank the 20 highest-leverage words to learn next (each ranked by how many
# almost-readable verses learning it alone would unlock)
python parser.py --bible nasb.txt --vocab my_words.txt --out out/graded.csv \
    --next-words 20 --next-words-out out/next_words.csv
```

## Configuration

`parser.py` flags: `--bible`, `--vocab`, `--out`, `--known-rate` (summary
threshold, default 0.95), `--min-verse-length` (verses shorter than this score 0),
`--passage-window` (verses per passage, 1 = off) + `--passage-out`,
`--next-words` (top N words to learn, 0 = off) + `--next-words-out`.

`dash_app.py` env vars: `BIBLE_GRADED_CSV` (default `out/graded.csv`),
`DASH_HOST` (default `127.0.0.1`), `DASH_PORT` (default `8050`), `DASH_DEBUG`.

## Docker

```bash
docker build -t bible-reader .
docker run -p 8050:8050 bible-reader   # serves the pre-graded sample data
```

## Development

```bash
.venv/bin/pytest        # tests
.venv/bin/ruff check .  # lint
```

See [`SPEC.md`](SPEC.md) for the roadmap and design decisions, and
[`CLAUDE.md`](CLAUDE.md) for an architecture orientation.
