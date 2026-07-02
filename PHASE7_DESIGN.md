# Phase 7 Design — Multilingual, Read Tracking, Longest Passage

Status: design (ready to implement) · Date: 2026-07-01

Four new capabilities: multiple Bible languages, read tracking, a smarter passage
algorithm, and a UI that surfaces them. All decisions are locked below; confirm or
override before building.

---

## 0. Decisions

### 0.1 Architecture — flat files + one SQLite file

The pipeline stays file-in / CSV-out. No central DB rewrite. The one mutable
state that genuinely needs persistence across sessions is the read log; that goes
into `reads.db` (stdlib `sqlite3`, single file, no server). Everything else —
graded CSVs, vocab profiles — stays as files.

```
Hebrew OT file ──► parser.py --lang he ──► out/wlc_graded.csv ──┐
Greek NT file  ──► parser.py --lang el ──► out/gnt_graded.csv ──┤
English NASB   ──► parser.py --lang en ──► out/nasb_graded.csv ─┤
                                                                   ▼
                                                             dash_app.py
                                                          (loads all CSVs +
                                                           reads.db)
```

### 0.2 Languages supported — four texts

| id | name | lang | notes |
|----|------|------|-------|
| `wlc` | WLC Biblical Hebrew OT | `he` | Masoretic Text, original language |
| `gnt` | Byzantine Greek NT | `el` | original language |
| `modern-he-ot` | Modern Hebrew OT (Mechon Mamre) | `he` | traditional Masoretic text reformatted for modern reading; same consonants as WLC but without cantillation, making vocabulary more directly applicable to contemporary Israeli Hebrew |
| `modern-he-nt` | Delitzsch Hebrew NT | `he` | public-domain 19th-c. Hebrew NT translation; closest freely-available modern Hebrew NT |

`--lang he` covers all three Hebrew texts — niqqud/cantillation stripping is identical for Biblical and modern Hebrew. The only difference is vocabulary distribution (modern texts include more loanwords and neologisms, which is exactly what makes them valuable for a contemporary Hebrew learner alongside the biblical texts).

### 0.3 Multi-Bible config — YAML config file

A single `bibles.yml` at the repo root registers all active texts. `dash_app.py`
reads it at startup; `parser.py` takes a `--bible-id` to know which ID to use when
writing scores to the graded CSV header.

```yaml
bibles:
  - id: nasb
    name: "NASB (English)"
    lang: en
    graded_csv: out/nasb_graded.csv
  - id: wlc
    name: "Biblical Hebrew OT (WLC)"
    lang: he
    graded_csv: out/wlc_graded.csv
  - id: gnt
    name: "Biblical Greek NT (Byzantine)"
    lang: el
    graded_csv: out/gnt_graded.csv
  - id: modern-he-ot
    name: "Modern Hebrew OT (Mechon Mamre)"
    lang: he
    graded_csv: out/modern_he_ot_graded.csv
  - id: modern-he-nt
    name: "Modern Hebrew NT (Delitzsch)"
    lang: he
    graded_csv: out/modern_he_nt_graded.csv
```

### 0.4 Language-aware tokenization — pluggable per `--lang`

| lang | tokenizer | stemmer |
|------|-----------|---------|
| `en` | `RegexpTokenizer(r"\w+")` + Snowball | existing |
| `he` | strip niqqud (U+0591–U+05C7) + extract Hebrew letters (U+05D0–U+05EA) | none — consonantal roots are shared across forms already |
| `el` | NFD-normalize + strip combining diacritics (U+0300–U+036F) + extract Greek letters | none — Greek lemmatization needs spaCy `el_core_news_md` (optional, P7.4) |

One new function `tokenize(text, lang)` dispatches to the right path. All existing
code stays; `stem_tokens(text)` becomes the English-specific fast path.

### 0.5 Longest readable passage — O(n) prefix-sum algorithm

Current `grade_passages(window=N)` uses a fixed window. The new ask is: given the
user's vocab, find the **single longest contiguous sequence of verses** where the
combined comprehension rate stays above the threshold.

**Algorithm** (classic "longest subarray with average ≥ threshold"):

1. For each verse i, compute `a[i] = known[i] - threshold × total[i]`.  
   (`known` and `total` are the known-stem count and total-stem count for the verse.)
2. Build prefix sums `P[0..n]` where `P[0] = 0`, `P[i] = P[i-1] + a[i-1]`.
3. The combined rate for passage `[i, j)` is  
   `(K[j] - K[i]) / (T[j] - T[i]) >= threshold`  
   iff `P[j] - P[i] >= 0` iff `P[j] >= P[i]`.
4. Find max `j - i` such that `P[j] >= P[i]` using a **monotone deque**:
   - Left-to-right pass: push indices onto a stack while `P[index] < P[stack.top()]`
     (candidates for leftmost minimum).
   - Right-to-left pass: for each j from n down, pop from stack while `P[j] >= P[stack.top()]`,
     updating the best `j - stack.top()`.
5. **O(n) time, O(n) space.**

Returns a single `(start_ref, end_ref, passage_text, n_verses, comprehension_rate)` row.
Expose as `grade_longest_passage(bible_df, vocab_stems, min_rate, lang, min_verse_length)`.
Add `--longest-passage-out PATH` CLI flag.

### 0.6 Read tracking — SQLite, single table

`reads.db` alongside `bibles.yml` (or configurable via `READS_DB` env var):

```sql
CREATE TABLE IF NOT EXISTS reads (
    bible_id TEXT NOT NULL,
    ref      TEXT NOT NULL,
    read_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (bible_id, ref)
);
```

`PRIMARY KEY (bible_id, ref)` means re-clicking "mark as read" is idempotent
(upsert, not duplicate). A row exists = read; absent = unread.

### 0.7 UI changes

| Element | Change |
|---------|--------|
| Bible dropdown | New — selects which graded CSV to display |
| Unread toggle | New — "Show unread only" checkbox; filters rows not in `reads` |
| Mark as read | New — checkbox column in the DataTable; click upserts/deletes `reads` row |
| Progress line | New — "N of M verses at ≥95% read" beneath the slider |
| Longest passage | New — button that triggers a callback returning the longest passage above threshold |
| Passage panel | New — expandable `html.Pre` below the table showing the full passage text |

No tab overhaul yet — add to the existing single-page layout. Scope creep lives here.

---

## 1. Data sources

### 1.1 Biblical Hebrew OT — Westminster Leningrad Codex (WLC)

Source: `https://raw.githubusercontent.com/openscriptures/morphhb/master/wlc/`
(one XML file per book). Needs a converter to produce `verse text -- reference` format.

Pre-processing:
- Strip XML tags, keep `<w>` element text (consonants + optional niqqud).
- Optionally strip niqqud at this stage or leave for the tokenizer.
- Reference format: `Gen 1:1` (English book names for cross-Bible consistency).

Write `scripts/convert_wlc.py` → `data/wlc.txt`.

### 1.2 Biblical Greek NT — Byzantine text

Source: `https://raw.githubusercontent.com/byztxt/byzantine-majority-text/master/`
(plain-text per book, available in various encodings).

Alternative: `https://github.com/morphgnt/sblgnt` (SBL Greek NT, XML).

Pre-processing:
- Strip Strong's numbers and morphological tags if present.
- Reference format: `Matt 1:1` etc.

Write `scripts/convert_gnt.py` → `data/gnt.txt`.

### 1.3 Modern Hebrew OT — Mechon Mamre

Source: Mechon Mamre (`mechon-mamre.org`) publishes the traditional Masoretic text in
Unicode Hebrew, formatted for modern reading, without cantillation marks. Plain-text
files are available per book. This is the same consonantal text as WLC but presents
it as modern Israelis actually read it (no trope, optional niqqud), making it the best
freely-available proxy for contemporary Hebrew OT vocabulary.

Write `scripts/convert_modern_he_ot.py` → `data/modern_he_ot.txt`.

### 1.4 Modern Hebrew NT — Delitzsch

Source: The Delitzsch Hebrew NT (Franz Delitzsch, 1877) is public domain and widely
mirrored on GitHub, e.g. `https://github.com/bibleforge/HebrewNT` or via SWORD module.
This is a scholarly Hebrew translation of the Greek NT and is the most widely used
freely-available Hebrew NT for study.

Write `scripts/convert_delitzsch_nt.py` → `data/modern_he_nt.txt`.

---

## 2. Work items

### P7.0 — Language-aware tokenizer

- Add `tokenize(text, lang="en") -> list[str]` to `parser.py`.
- Hebrew: strip niqqud regex + extract `[א-ת]+` tokens.
- Greek: NFD + strip combining diacritics + extract `[α-ωΑ-Ω]+` tokens.
- Thread `lang` through `comprehension_rate`, `weighted_comprehension_rate`,
  `verse_effort`, `next_words_to_learn`, `grade_passages`, `grade_longest_passage`.
- Add `--lang` CLI flag (default `en`).
- Tests: Hebrew tokenization strips niqqud; Greek tokenization strips diacritics;
  existing English tests unchanged (backwards-compatible default).

### P7.1 — Data converters

Four standalone scripts, each fetches and writes:

| script | source | output |
|--------|--------|--------|
| `scripts/convert_wlc.py` | openscriptures/morphhb XML | `data/wlc.txt` |
| `scripts/convert_gnt.py` | byztxt/byzantine-majority-text | `data/gnt.txt` |
| `scripts/convert_modern_he_ot.py` | Mechon Mamre plain-text | `data/modern_he_ot.txt` |
| `scripts/convert_delitzsch_nt.py` | bibleforge/HebrewNT or equivalent | `data/modern_he_nt.txt` |

All produce `verse text -- reference` format. Hebrew scripts leave niqqud in
the output (tokenizer strips it at scoring time).

Acceptance: all four scripts run without error; `parser.py --lang he` on any Hebrew
output produces a valid CSV; `parser.py --lang el` on `gnt.txt` produces a valid CSV.

### P7.2 — Multi-Bible config + UI Bible selector

- Add `bibles.yml` (schema from §0.3).
- `dash_app.py`: load all configured CSVs at startup; add a `dcc.Dropdown` for
  Bible selection; filter callback uses the selected Bible's DataFrame.
- `READS_DB` env var (default: `reads.db`).
- Acceptance: switching dropdown changes the verse table; all Bibles load without
  error even if a graded CSV is missing (log warning, exclude from dropdown).

### P7.3 — Read tracking

- `reads.db` created on first mark-as-read.
- Add a checkbox column to the DataTable; clicking it calls a server-side callback
  that upserts / deletes the `reads` row and returns updated data.
- "Show unread only" toggle filters the DataFrame by refs absent from `reads`.
- Progress line: `"{n_read} of {n_threshold} readable verses read"`.
- Acceptance: check a verse, refresh page → still checked. Uncheck → gone from DB.

### P7.4 — Longest readable passage

- `grade_longest_passage(bible_df, vocab_stems, min_rate, lang, min_verse_length)`
  in `parser.py` using the O(n) deque algorithm from §0.5.
- `--longest-passage-out PATH` CLI flag (writes single-row CSV).
- Dash UI: "Find longest passage" button → callback calls the algorithm and
  renders result in an `html.Pre` panel below the table.
- Tests: all-known corpus → returns entire corpus; no-known corpus → returns
  empty; known/unknown alternating → returns longest contiguous known run.

---

## 3. Sequencing

```
P7.0 tokenizer ──► P7.1 converters ──► P7.2 multi-Bible UI
                                    └──► P7.3 read tracking
                                    └──► P7.4 longest passage
```

P7.0 is the prerequisite. P7.2–P7.4 are independent once P7.1 proves the
pipeline accepts Hebrew and Greek text end-to-end.

## 4. Out of scope for Phase 7

- Greek lemmatization (spaCy `el_core_news_md`) — can follow P7.4 as P7.5.
- Multi-user auth — still out of scope (SPEC §5).
- Additional Hebrew NT translations (e.g. Salkinson-Ginsburg) — add as `bibles.yml` entries, no code change.
- UI overhaul / tabs — add only what §0.7 lists; full redesign is a separate phase.
