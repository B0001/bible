# Bible Reader — graded by your vocabulary

Find Bible verses you can actually read — in **English, Biblical Hebrew, or
Koine Greek**. Given a list of words you already know, this scores every verse
by its **comprehension rate** — the fraction of its words in your vocabulary —
so you can read at the ~95% "sweet spot" that language research associates
with effective vocabulary growth.

English matching is **stem-aware** (knowing `run` also credits `running` and
`ran`); Hebrew strips nikudim/cantillation so vocab matches any pointed text;
Greek strips diacritics.

## This repo is also a Claude Code skills marketplace

The techniques here are reusable well beyond scripture. `skills/` packages them
as four installable [Agent Skills](https://code.claude.com/docs/en/skills), with
this codebase as the reference implementation behind them:

| Skill | What it teaches |
|---|---|
| [`vocabulary-grading`](skills/vocabulary-grading/SKILL.md) | Comprehension scoring, corpus-internal difficulty ranks, learning order, O(n) longest-readable-span, unlock ranking, half-life recall |
| [`multilingual-text-tokenization`](skills/multilingual-text-tokenization/SKILL.md) | Normalizing Hebrew/Greek/Arabic/Cyrillic for word matching, stemmer selection, Python↔JS parity, RTL |
| [`asr-forced-alignment`](skills/asr-forced-alignment/SKILL.md) | Aligning a known text to long-form audio via cloud ASR + anchor matching — no GPU, ~$0.11/audio-hour |
| [`derived-dataset-publishing`](skills/derived-dataset-publishing/SKILL.md) | Publishing derived metrics from a corpus you don't own, with join keys that survive canon variation |

```bash
/plugin marketplace add B0001/bible
/plugin install graded-reader@graded-reader-skills
```

Skills then invoke as `/graded-reader:vocabulary-grading`, and Claude loads them
automatically when a task matches. No version is pinned in the manifests, so
installs track the repo's latest commit.

## How it works

```
your vocab ─┐
            ├─► parser.py (--lang en|he|el) ─► out/<bible>_graded.csv ─► dash_app.py
Bible text ─┘     tokenize & score verses        ref,verse,rate,counts     multi-Bible UI
```

- **`parser.py`** — scoring pipeline. Input is a Bible text with one
  `verse text -- reference` per line and a whitespace-separated vocab file.
- **`dash_app.py`** — web UI: Bible selector (`bibles.toml`), comprehension
  filter, nikudim-insensitive search, per-verse read tracking (SQLite), and a
  "find longest readable passage" button (O(n) algorithm).
- **`scripts/`** — converters that download source texts (WLC Hebrew OT,
  Byzantine Greek NT, Delitzsch Hebrew NT) into the expected line format.
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

## Hebrew and Greek

Download the original-language texts, grade them against your vocab, and the
web UI picks them up via `bibles.toml`:

```bash
# Fetch source texts (network required; ~10 MB total)
python scripts/convert_wlc.py            # Biblical Hebrew OT  -> data/wlc.txt
python scripts/convert_gnt.py            # Byzantine Greek NT  -> data/gnt.txt
python scripts/convert_delitzsch_nt.py   # Modern Hebrew NT    -> data/modern_he_nt.txt

# Grade each with the right --lang (sample starter vocabs included)
python parser.py --bible data/wlc.txt --vocab sample/hebrew_vocab.txt \
    --out out/wlc_graded.csv --lang he
python parser.py --bible data/gnt.txt --vocab sample/greek_vocab.txt \
    --out out/gnt_graded.csv --lang el
python parser.py --bible data/modern_he_nt.txt --vocab sample/hebrew_vocab.txt \
    --out out/modern_he_nt_graded.csv --lang he
```

Hebrew vocab files can be written with or without nikudim — both are normalized
to bare consonants. In the UI, search is nikudim-insensitive (type `שלום`, match
`שָׁלוֹם`) and Hebrew verses render right-to-left.

## Any other language

`scripts/convert_getbible.py` fetches any of ~117 translations in 63 languages
from getbible.net, and `--lang` accepts any ISO 639-1 code. Stem-aware matching
covers the nine languages in `SNOWBALL_LANGS` (ar, de, en, es, fr, it, nl, pt,
ru); Hebrew/Greek strip marks; anything else matches on exact lowercased word
forms:

```bash
python scripts/convert_getbible.py --list                  # browse translations
python scripts/convert_getbible.py --translation valera    # Spanish Reina Valera
python parser.py --bible data/valera.txt --vocab my_spanish_words.txt \
    --out out/valera_graded.csv --lang es
```

Twelve Bibles ship pre-configured in `bibles.toml`: English, Biblical Hebrew,
Koine Greek, Modern Hebrew, Spanish, German, French, Russian, Portuguese,
Italian, Dutch, and Arabic (harakat-insensitive, RTL).

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

## Rhymed composites

`rhyme.py` makes Bible text rhyme without writing new words: it picks one
existing translation per verse and chooses the line breaks (`rhyme.py joint`),
then reports how much of the rhyme scheme it actually achieved.

```bash
python scripts/convert_scrollmapper.py --translation KJV   # also ASV BBE Darby Webster YLT
python rhyme.py joint --bible kjv=data/kjv.txt --bible asv=data/asv.txt \
    --ref "2 corinthians 6:" --conj --content --scheme AABB,ABAB
```

It does not produce "rhyming Bible text". Over the whole Bible, with 7
translations, about 9% of scheme pairs rhyme, and the longest run of
consecutive rhymes is 6 pairs (the priestly lots of 1 Chronicles 24). See
`CLAUDE.md` for the measurements behind each option.

### Prior work

Every earlier rhymed Bible rewrites the wording to fit the verse, which this
project deliberately does not do.

- Metrical psalters: the Genevan Psalter (1562), Sternhold and Hopkins (1562),
  the Bay Psalm Book (1640), the Scottish Psalter (1650), Tate and Brady (1696),
  Isaac Watts (1719). See [Rhymed psalter](https://en.wikipedia.org/wiki/Rhymed_psalter).
- Rob Bellingham, *The Bible in Verse*: the whole Bible in rhyming couplets,
  about 1,000 pages ([Baptist NZ](https://baptist.nz/the-bible-as-a-poem/)).
- T. J. W. Thornes, *The Bible in Rhyming Verse*: every chapter, Apocrypha
  included, condensed to about two stanzas
  ([Amazon](https://www.amazon.com/Bible-Rhyming-Verse-Tjw-Thornes/dp/1739135717)).
- Amos J. Ferguson, *Bible Poem, or Versified Scripture in Rhyme*
  ([Amazon](https://www.amazon.com/Bible-Poem-Versified-Scripture-Rhyme/dp/0243314604)).
- "From Pastor to Poet: Rhyming the Entire Bible"
  ([Medium](https://medium.com/disco-lightning/from-pastor-to-poet-rhyming-the-entire-bible-58b8abc886b8)).
- Jotham McCauley, *The Bible Rhymes*: the New Testament as a condensed
  rhyming summary ([thebiblerhymes.com](https://www.thebiblerhymes.com/)).
- Brendan Conboy, *The Book of Psalms in Rhyme*
  ([Bible Buying Guide](https://biblebuyingguide.com/the-book-of-psalms-in-rhyme/)).
- Gary W. Parker, *Holy Bible Poetry*: 176 poems, each containing a passage
  word for word
  ([Amazon](https://www.amazon.com/Holy-Bible-Poetry-Biblical-Passages/dp/1796615749)).
- A small GPT trained to write Psalm- and Proverb-style Spanish
  ([GitHub](https://github.com/arturoappp/generative-ai-applications-project)).

## Configuration

`parser.py` flags:

| Flag | Default | Description |
|---|---|---|
| `--bible` | *(required)* | `verse -- ref` text file |
| `--vocab` | *(required)* | whitespace-separated vocab / profile file |
| `--out` | *(required)* | output CSV path |
| `--lang` | `en` | text language: `en`, `he` (Hebrew), `el` (Greek) |
| `--longest-passage-out` | — | write the longest passage at `--known-rate` to a CSV |
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

`dash_app.py` reads `bibles.toml` for the Bible list (falls back to
`BIBLE_GRADED_CSV`, default `out/graded.csv`, when no configured CSV exists).
Other env vars: `READS_DB` (read-tracking SQLite, default `reads.db`),
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
