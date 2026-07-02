# Phase 9 Design — Static Client-Side Reader ("viral-proof")

Status: design (ready to implement) · Date: 2026-07-02

If traffic ever spikes, the winning architecture is less backend, not more.
All Bible data is read-only after grading; per-user state (vocab, read list) is
small and private. So: a **static site on a CDN, scoring in the browser, state
in localStorage, zero servers**. Survives any traffic spike at ~$0/month and
solves the multi-user problem (SPEC §5's auth blocker) by never having shared
state at all.

The Dash app is not deleted — it remains the local power tool for the Phase 5
personalization stack (decay, effort, semantic, study queue). The static site
is the shareable *reader*.

---

## 0. Decisions

### 0.1 Division of labor

| | Dash app (existing) | Static site (new) |
|---|---|---|
| audience | you, locally | everyone, via URL |
| vocab | file on disk (`--vocab`) | textarea → localStorage |
| scoring | Python/polars at grade time | JS at page load, re-scored on vocab edit |
| read tracking | SQLite `reads.db` | localStorage, JSON export/import |
| Phase 5 SRS (decay/effort/semantic/study) | ✅ full | ❌ not ported |
| longest passage | ✅ | ✅ (same algorithm, ported) |

### 0.2 No framework, no bundler

One `site/index.html` + one `site/app.js` + one `site/style.css`, vanilla JS.
The only vendored dependency is an English Snowball stemmer (~10 KB JS; the
classic snowball.js port). Hebrew/Greek tokenization is two regexes — ports
1:1 from `parser.tokenize`. No React, no build step for the frontend itself.

### 0.3 Data format: pre-tokenized JSON per Bible

The browser must score verses against *any* user vocab, so it needs each
verse's normalized token list, not just its text. A build script exports, per
Bible:

```json
{
  "id": "wlc", "name": "Biblical Hebrew OT (WLC)", "lang": "he",
  "refs":   ["Gen 1:1", ...],
  "verses": ["בְּרֵאשִׁ֖ית בָּרָ֣א ...", ...],
  "tokens": [["בראשית", "ברא", ...], ...]
}
```

- Tokens are the same normalized forms `parser.tokenize_and_stem` produces
  (English: Snowball stems; Hebrew: bare consonants; Greek: stripped lowercase),
  so browser scoring is definitionally identical to the pipeline's.
- One JSON per Bible, **lazy-loaded on selection** (~1–3 MB gzipped each; only
  the chosen Bible is fetched). A tiny `manifest.json` lists available Bibles.
- User's vocab is tokenized *in the browser* with the same rules (Snowball JS
  for en; regex strip for he/el), then scoring is set membership + counting.
  31k verses × ~20 tokens ≈ 600k set lookups — a few ms in JS.

<!-- ponytail: plain string arrays; if payload ever matters, intern tokens to
     int ids (~40% smaller). Not worth it at 3 MB gzipped. -->

### 0.4 Per-user state: localStorage + file export

- `vocab:<lang>` — the raw vocab text per language (he/el/en share nothing).
- `reads:<bible_id>` — JSON array of read refs.
- localStorage is fragile (cleared with browsing data), so one "Export data" /
  "Import data" button pair round-trips all keys as a downloaded JSON file.
  That's the whole backup story — no accounts, no sync.

### 0.5 Hosting: GitHub Pages

Free, CDN-backed, in-repo. A CI job builds the data JSONs (runs the
`scripts/convert_*.py` fetchers + the new export script) and deploys `site/`
to Pages on push to master. NASB text is fetched from `tushortz/variety-bible-text`
in CI the same way the converters fetch theirs — no large files committed.

### 0.6 Stopgap for the current Dash app

Independent of the static site: the table callback returns **every** matching
row (~6 MB JSON for a wide filter on NASB); `page_size=20` only paginates
client-side. Cap the callback at the top 500 rows with a "showing 500 of N —
narrow your filter" note. One-line class of fix, do it first.

---

## 1. Work items

### P9.0 — Cap the Dash callback payload (stopgap)

- In `update_table`: `filtered.head(500)`; count line says
  "showing first 500 of N matches" when truncated.
- Test in `test_dash_app.py` (pure logic: records length ≤ 500).

_Acceptance:_ full-range slider on NASB returns ≤ 500 rows per callback.

### P9.1 — `scripts/export_static.py`

- Reads `bibles.toml` + the `data/*.txt` sources; for each available Bible
  writes `site/data/<id>.json` (refs/verses/tokens per §0.3) and a
  `site/data/manifest.json` (id, name, lang, verse count, file size).
- Reuses `parser.tokenize_and_stem` — no duplicated normalization logic.
- Skips Bibles whose source text is absent (same warning pattern as dash_app).

_Acceptance:_ `python scripts/export_static.py` produces loadable JSON for all
four Bibles; token lists match `tokenize_and_stem` output exactly (test).

### P9.2 — The static reader (`site/`)

- `index.html` + `app.js` + `style.css`, vanilla JS, RTL-aware.
- Controls (mirrors the Dash UI): Bible dropdown (from manifest, lazy-fetch),
  vocab textarea (persisted to localStorage, re-scores on change),
  comprehension range slider, mark-insensitive search (port `_strip_marks`),
  paginated table (client-side, 20/page — the data is already local),
  read checkboxes, unread-only toggle, progress line.
- Scoring in JS: `rate = |tokens ∩ vocabSet| / |tokens|`; vocab tokenized with
  vendored Snowball (en) or the strip-regexes (he/el).
- Hebrew Bibles render `dir=rtl` on the verse column (reuse the `lang` field).

_Acceptance:_ open `site/index.html` over a local static server, paste a vocab,
see the same comprehension rates the Python pipeline produces for that vocab.

### P9.3 — Longest passage + data export in JS

- Port `longest_span` (≈25 lines, ports 1:1) into `app.js`; "Find longest
  passage" button renders the passage panel, RTL-aware.
- "Export data" / "Import data" buttons round-trip all localStorage keys as a
  JSON file download/upload (§0.4).

_Acceptance:_ JS `longestSpan` returns the same span as Python `longest_span`
on a shared fixture (checked by a test that runs the fixture through both —
the JSON fixture lives in `site/` and a pytest reads it).

### P9.4 — CI deploy to GitHub Pages

- New workflow job: run converters (network) + `export_static.py`, upload
  `site/` as the Pages artifact, deploy on push to master.
- Converter failures (source repo moved, network flake) fail the deploy job
  but not the test jobs.

_Acceptance:_ pushing to master publishes the reader at the Pages URL; the
existing test jobs are unaffected.

## 2. Sequencing

```
P9.0 stopgap (independent, do first)
P9.1 export ─► P9.2 reader ─► P9.3 passage/export ─► P9.4 deploy
```

## 3. Out of scope

- Porting Phase 5 SRS (decay, effort, semantic, study queue) to JS — the Dash
  app keeps those; the static site is a reader, not a flashcard engine.
- Accounts / cross-device sync — export/import file covers backup; revisit only
  if real users ask.
- Service worker / offline mode — cheap to add later; not needed to survive
  traffic.
- WASM / compiling the Python pipeline — the scoring is a set intersection;
  JS is plenty.
- Retiring the Dash app — it stays as the local analytics tool.
