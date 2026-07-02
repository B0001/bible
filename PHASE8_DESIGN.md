# Phase 8 Design — Repo-Wide Improvement

Status: design (ready to implement) · Date: 2026-07-01

Phase 7 shipped multilingual fast; this phase pays down what it left behind.
Every item below is a **confirmed defect or gap found by inspecting the repo**,
not speculation. Ordered by value; each item is independently shippable.

---

## P8.0 — Commit Phase 7 (do first)

The entire multilingual feature set is uncommitted (`git status`: modified
parser.py/dash_app.py/SPEC.md/test_parser.py, untracked scripts/, bibles.toml,
PHASE7_DESIGN.md, vocab samples). One hard drive failure loses it all.

- Add `reads.db` to `.gitignore` (user state, like `*.reviews.csv`).
- Move `sample/nasb.txt` (4.7 MB full NASB) → `data/` (already gitignored).
  `sample/` is for small committed fixtures; 4.7 MB doesn't belong in git.
- Commit everything else as one Phase 7 commit.

_Acceptance:_ `git status` clean; `reads.db` and full-Bible texts untracked.

## P8.1 — Fix the WLC display text (morpheme slashes)

`data/wlc.txt` reads `בְּ/רֵאשִׁ֖ית בָּרָ֣א אֱלֹהִ֑ים` — the `/` are morphhb
morpheme-boundary markers leaking from the source XML. They pollute the UI and
make the text look broken to a reader.

- In `scripts/convert_wlc.py`: replace `/` with nothing inside word text
  (`בְּ/רֵאשִׁ֖ית` → `בְּרֵאשִׁ֖ית`) before writing.
- Re-run the converter and re-grade `out/wlc_graded.csv`.
- Tokenizer impact: none — `[א-ת]+` runs currently split on `/`, so joining
  morphemes slightly *changes* token counts (prefixes fuse to their host word,
  matching how a reader actually sees words). This is more correct, not less:
  a learner reads בְּרֵאשִׁית as one word.

_Acceptance:_ `head -1 data/wlc.txt` contains no `/`; UI shows natural Hebrew.

## P8.2 — Drop the Mechon Mamre converter; simplify to four Bibles

`scripts/convert_modern_he_ot.py` self-admits it can't reconstruct verse refs
from the site's HTML (writes `-- Gen p1` chapter blobs) and its `bibles.toml`
entry has never loaded. The modern-Hebrew-OT idea was always shaky: modern
Israelis read the same Masoretic text — the WLC entry already *is* the Hebrew OT.

- Delete `scripts/convert_modern_he_ot.py`.
- Remove the `modern-he-ot` entry from `bibles.toml`.
- Four Bibles remain: NASB (en), WLC Hebrew OT (he), Byzantine Greek NT (el),
  Delitzsch Hebrew NT (he). All four load today.

_Acceptance:_ app starts with zero "CSV not found" warnings.

## P8.3 — Make Phase 5 personalization language-aware (currently English-only)

Confirmed hardcodes that silently break Hebrew/Greek profiles:

| location | bug |
|----------|-----|
| `record_review()` | keys the review log by `stem_tokens(word)` — English Snowball. A Hebrew word with niqqud is stored *with* niqqud, so it never matches `tokenize_and_stem(verse, "he")` tokens. Reviews are recorded but never affect scoring. |
| `load_profile()` | seeds from `load_vocab(path)` with implicit `lang="en"` — Hebrew vocab gets English-stemmed keys. `--decay --lang he` scores everything as unknown. |
| `_word_difficulty()` | `zipf_frequency(word, "en")` hardcoded. wordfreq ships `he` and `el` wordlists — pass the lang through. |
| `load_semantic_model()` | loads `en_core_web_md` unconditionally. For `lang != "en"`, return `None` with a clear warning (spaCy has no usable he/el vector models); don't pretend. |

- Thread `lang` through `record_review`, `load_profile`, `_word_difficulty`,
  `verse_effort` (pass to difficulty), and `load_semantic_model`; `main()`
  passes `args.lang` everywhere it builds a profile.
- Tests: a Hebrew review round-trip (record with niqqud, verse scores by decay);
  `zipf_frequency` called with `he` for `lang="he"` (mock or marker test).

_Acceptance:_ `--review שָׁלוֹם correct --decay --lang he` changes the score of
verses containing שלום; English behavior byte-identical (default `lang="en"`).

## P8.4 — Deduplicate the longest-passage algorithm

The O(n) prefix-sum + monotone-deque algorithm exists **twice**: once in
`parser.grade_longest_passage()` (tested), once inline in `dash_app.find_passage()`
(untested copy). Classic drift risk.

- Extract the core to `parser.longest_span(known, total, min_rate) ->
  (start, end) | None` — pure lists in, indices out.
- `grade_longest_passage()` calls it; `dash_app.find_passage()` imports and
  calls it on the `known_count`/`total_count` columns.
- Existing parser tests cover it; add one direct `longest_span` test for the
  empty input edge.

_Acceptance:_ the algorithm body appears exactly once in the repo.

## P8.5 — Grade in one pass instead of three

`main()` tokenizes every verse three times: once for `comprehension_rate`,
once for `known_count`, once for `total_count` (three separate `map_elements`).
On the 23k-verse WLC that's 3× the dominant cost.

- One `map_elements` returning a struct `{rate, known, total}` (or one Python
  loop building three lists — whichever reads simpler), unnested into the three
  columns. Same output schema, byte-identical CSV.

_Acceptance:_ CSV output unchanged (diff against pre-change output); wall time
on WLC roughly ⅓.

## P8.6 — RTL rendering for Hebrew in the UI

Hebrew verses render left-aligned LTR in the DataTable — punctuation and
maqaf-joined words display in the wrong visual order.

- `bibles.toml` already carries `lang` per Bible; `dash_app` already loads it
  but never uses it. On Bible switch, set the verse column's style:
  `direction: rtl; textAlign: right` when `lang == "he"` (a
  `style_cell_conditional` output on the existing callback).
- Same for the longest-passage `html.Pre` (`dir="rtl"` when Hebrew).

_Acceptance:_ WLC/Delitzsch verses read right-to-left; NASB/GNT unchanged.

## P8.7 — Docs and Docker catch up to Phase 7

- `Dockerfile`: `COPY bibles.toml scripts ./` is missing — the image still
  serves only the 12-verse English sample. Also pre-grade nothing extra
  (converters need network; document `docker run -v` mounting instead).
- `README.md` (149 lines) and `CLAUDE.md`: no mention of `--lang`, converters,
  `bibles.toml`, read tracking, or longest passage. Add a short multilingual
  quickstart (the three converter + grade commands) and update the feature list.
- `pyproject.toml`: `requires-python = ">=3.11"` if not already (dash_app uses
  stdlib `tomllib`).
- CI: no change needed (3.12 already).

_Acceptance:_ a new reader can go from clone → Hebrew Bible in browser using
README alone.

## P8.8 — Test the Dash layer's pure logic

`dash_app.py` (478 lines) has zero tests. Don't test Dash plumbing; test the
pure functions: `_strip_marks` (niqqud + Greek diacritics), `to_records` (Read
column ✓ logic), `get_read_refs`/`_mark_read`/`_mark_unread` round-trip against
a tmp SQLite file, and the count-columns fallback message path.

- New `test_dash_app.py`, ~8 tests, no Dash test client needed (plain function
  calls; point `READS_DB` at tmp_path via env or parameter).

_Acceptance:_ read-tracking round-trip and mark-stripping covered in CI.

---

## Sequencing

```
P8.0 commit ─► P8.1 WLC slashes ─► P8.2 drop mamre     (data quality)
            ├► P8.3 multilingual Phase 5                (correctness)
            ├► P8.4 dedupe algorithm ─► P8.8 dash tests (structure)
            ├► P8.5 one-pass grading                    (performance)
            └► P8.6 RTL ─► P8.7 docs/docker             (polish)
```

P8.0 first; everything after is independent.

## Explicitly not doing

- Splitting `parser.py` (884 lines) into modules — it's one coherent pipeline
  with a flat namespace; splitting adds import ceremony for no reader benefit yet.
- Greek lemmatization / Hebrew root extraction — still P7.5-territory, needs a
  real lemmatizer dependency; revisit when a Greek learner asks.
- User accounts, auth, multi-user reads — SPEC §5, unchanged.
- A database for verses — 23k rows in CSV loads in milliseconds; polars is fine.
