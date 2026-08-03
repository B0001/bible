---
name: run-bible-reader
description: >-
  Build, launch, screenshot, and drive the vocabulary-graded Bible reader — both
  front ends (the static reader in site/ and the Dash app) plus the parser CLI.
  Use when asked to run, start, serve, smoke-test, screenshot, or manually verify
  this app; to check a UI change in the real browser; or to reproduce what the
  e2e tests cover without running pytest.
---

# Run the bible reader

Two front ends over one scoring pipeline. **Both are browser apps with nothing
useful in their server-rendered HTML** — the static reader scores verses in
client-side JS, and Dash paints its table from a callback. `curl` gets you an
empty shell from either, so drive them with the committed driver:

```
.claude/skills/run-bible-reader/driver.py
```

It launches its own server on a free port, drives real headless Chromium via
Playwright, writes screenshots to `out/run/`, prints a `PASS`/`FAIL` line per
check, and shuts the server down. All paths below are relative to the repo root.

## Prerequisites

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,e2e]'
.venv/bin/playwright install chromium
.venv/bin/python -m nltk.downloader stopwords
brew install node   # required by the Python<->JS parity tests, see Test below
```

If `.venv` already exists and has no `pip` (it was created by `uv` — this repo
carries a `uv.lock`), use `uv pip install -e '.[dev,e2e]'` instead. The
`playwright` and `nltk` commands are the same either way, and both are
idempotent.

## Run: the static reader (`site/`) — the primary product

Needs `site/data/*.json`, built from `data/*.txt` (~60 s, writes ~107 MB):

```bash
.venv/bin/python scripts/export_static.py
.venv/bin/python .claude/skills/run-bible-reader/driver.py site
```

Verified output:

```
serving http://127.0.0.1:51261/
  PASS  bible dropdown populated — 12 translations
  PASS  read route auto-renders a passage — 'john 13:33 – john 16:18' / 'Passage 1 of 10'
  PASS  learn-next chips render — 10 chips
  shot  out/run/site-read.png
  PASS  Done advances the queue — 51 of 5352 readable verses read
  PASS  level slider rebuilds the queue — 120 verses · 2963 words · 100.0% readable
  PASS  browse route lists verses — 20 rows
  shot  out/run/site-browse.png
  PASS  no JS errors
```

To rebuild and drive without touching the working `site/data/` (that export is
destructive and slow):

```bash
mkdir -p /tmp/site && cp site/index.html site/*.css site/*.js /tmp/site/
.venv/bin/python scripts/export_static.py --site-dir /tmp/site
.venv/bin/python .claude/skills/run-bible-reader/driver.py site --site-dir /tmp/site
```

## Run: the Dash app

Needs a graded CSV for at least one `bibles.toml` entry. Grade the **full** text,
not `sample/nasb_sample.txt` — see the gotcha below:

```bash
.venv/bin/python parser.py --bible data/nasb.txt \
    --vocab sample/my_vocab.txt --out out/nasb_graded.csv
# → Graded 31102 verses -> out/nasb_graded.csv; 0 at >= 95% comprehension.
.venv/bin/python .claude/skills/run-bible-reader/driver.py dash
```

Verified output — note the passage result, which is correct behavior, not a
failure (see Gotchas):

```
serving http://127.0.0.1:51340/
  PASS  verse table renders — 60 cells / '10 of 31102 verses match.'
  PASS  bible selector usable — NASB (English)
  PASS  find-passage returns a span — No passage at ≥95% found.
        note: that is the app working — the graded CSV for this
        Bible has no span at >=95%. See Gotchas in SKILL.md.
  shot  out/run/dash.png
  PASS  no JS errors
```

`--bible SUBSTRING` switches translation before driving (matches the dropdown
label). Against a translation graded with a realistic vocabulary, the same run
produces a real span:

```
$ .venv/bin/python .claude/skills/run-bible-reader/driver.py dash --bible Valera
serving http://127.0.0.1:51352/
  PASS  verse table renders — 60 cells / '10 of 31102 verses match.'
  PASS  bible selector usable — Reina Valera (Spanish)
  PASS  find-passage returns a span — Longest passage: Lucas 21:16 – Juan 19:5 (989 verses, 95.0% comprehension)
  shot  out/run/dash.png
  shot  out/run/dash-passage.png
  PASS  no JS errors
```

## Ad-hoc poking

```bash
# leave a server up and drive it yourself
.venv/bin/python .claude/skills/run-bible-reader/driver.py site --keep --port 8123

# screenshot any URL, optionally waiting on a JS predicate first
.venv/bin/python .claude/skills/run-bible-reader/driver.py shot \
    "http://127.0.0.1:8123/#settings" out/run/settings.png \
    --wait "!document.getElementById('route-settings').hidden"

# evaluate JS in the page and print the JSON result
.venv/bin/python .claude/skills/run-bible-reader/driver.py eval \
    http://127.0.0.1:8123/ \
    "({bibles: document.querySelectorAll('#bible-select option').length, \
       level: document.getElementById('level').value})"
# → { "bibles": 12, "level": "50" }
```

## Run: the parser CLI (no browser)

```bash
.venv/bin/python parser.py --bible sample/nasb_sample.txt \
    --vocab sample/my_vocab.txt --out /tmp/graded.csv
# → Graded 12 verses -> /tmp/graded.csv; 0 at >= 95% comprehension.
```

Sub-second on the 12-verse sample; ~5 s on a full 31k-verse translation.

## Test

```bash
.venv/bin/python -m pytest      # 185 passed, 6 skipped
.venv/bin/ruff check .          # must be clean
```

Confirm what skipped rather than trusting the count — `pytest -q -rs` prints
reasons. The 6 expected skips are the `lexical`/`semantic` extras.

**If you see 8 skipped, `node` is missing.** The two extra skips are
`test_vendored_js_stemmer_matches_nltk` and `test_rank_js_matches_python` in
`test_static_export.py` — the only guards keeping the browser's vendored
Snowball stemmer and `site/rank.js` byte-identical to `parser.py`. A green
suite without node is weaker than it looks, and this is the repo's most
fragile invariant (`site/rank.js:1-3`: "change both sides together").
`brew install node` fixes it.

`test_site_smoke.py` (10 e2e tests) needs `[e2e]` + `playwright install
chromium` + exported site data; without them it skips itself silently, which
looks identical to passing. Verify with:

```bash
.venv/bin/python -m pytest -m e2e -q   # → 10 passed, 181 deselected
```

## Gotchas

- **`.venv/bin/pip` may not exist.** The checked-out venv was built by `uv`, and
  uv venvs ship no `pip` — `.venv/bin/pip install …` dies with "no such file or
  directory" and `python -m pip` reports "No module named pip". Use
  `uv pip install`, or build a fresh venv with `python3 -m venv`.
- **`dcc.Dropdown` is not a `<select>`.** Playwright's `select_option()` silently
  does nothing. You must click the control open, then click the option. In Dash
  3.x the options render as `.dash-options-list-option-text` **outside** the
  `#bible-select` element (older React-Select builds used `.Select-option`
  inside it) — the driver tries both.
- **Do not grade `sample/nasb_sample.txt` into `out/nasb_graded.csv`.** That is
  the path `bibles.toml` maps the NASB entry to, and the sample is 12 verses. It
  overwrites a full 31,102-verse grading with 12 rows, all below the default rate
  filter, so the Dash table renders empty and the driver times out waiting for
  `#table td`. (This bit me while writing this skill.) Grade the sample to a
  throwaway path instead: `--out /tmp/graded.csv`.
- **"No passage at ≥95% found" is the app working, not a bug.** The checked-in
  graded CSVs are scored against `sample/my_vocab.txt`, the EF top-100 words, so
  nothing in them reaches 95%. To see the feature actually produce a span, grade
  a translation against a realistic vocabulary — the top-N frequency list is what
  the static site's level slider uses:

  ```bash
  .venv/bin/python -c "
  import parser as P
  bible = P.load_bible('data/valera.txt')
  ranks = P.corpus_ranks([P.tokenize_and_stem(v, 'es') for v in bible['verse']])
  top = [f for f, r in sorted(ranks.items(), key=lambda kv: kv[1])[:2000]]
  open('out/run/vocab_es_top2000.txt','w').write('\n'.join(top))"
  .venv/bin/python parser.py --bible data/valera.txt \
      --vocab out/run/vocab_es_top2000.txt --out out/valera_graded.csv --lang es
  # → Graded 31084 verses -> out/valera_graded.csv; 11775 at >= 95% comprehension.
  .venv/bin/python .claude/skills/run-bible-reader/driver.py dash --bible Valera
  ```

- **The site's readability comes from the level slider, not a vocab file.** The
  `#vocab` textarea is empty by default; "675 words · 5352 of 31102 verses
  readable" is the slider granting the top-N corpus words. Don't go looking for a
  default vocab file — there isn't one.
- **Clicking `#find-passage` scrolls the page to the bottom**, so a naive
  screenshot right after the click captures the table footer and misses the
  result. The driver scrolls back to the top, then scrolls the panel into view
  for a second shot.
- **`bibles.toml` entries whose CSV is missing are skipped with a warning**, not
  an error. A translation silently absent from the dropdown means you never
  graded it.
- **`scripts/export_static.py` writes ~107 MB into `site/data/` and takes ~60 s.**
  Use `--site-dir` to target a scratch copy when you only want to verify the
  export.
- **`out/` and `site/data/` are gitignored**, so everything the driver writes is
  safe to leave behind. `reads.db` (Dash read-tracking) is too.
- **Run pytest from the repo root.** There is an unrelated `conftest.py` in the
  parent `~/Downloads` that stubs out `polars` with a mock; `pyproject.toml`'s
  `[tool.pytest.ini_options]` anchors rootdir here to keep it out.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `.venv/bin/pip: no such file or directory` | uv-built venv, no pip. `uv pip install -e '.[dev,e2e]'`. |
| `site/data/manifest.json missing` | `.venv/bin/python scripts/export_static.py` first. |
| `dash never became healthy` | Read `out/run/dash.log`, which the driver writes. Usually every `bibles.toml` CSV is missing — grade one. |
| `Timeout … waiting for locator(".Select-option:has-text('X')")` | Dash 3.x option class changed; the driver's combined selector handles it. Confirm the label substring matches the dropdown text exactly. |
| `page.goto: net::ERR_CONNECTION_REFUSED` | Server not up yet. The driver polls for readiness; if you started `http.server` yourself, poll before driving. |
| e2e tests "pass" suspiciously fast | They skipped. Check `[e2e]` is installed, chromium is installed, and `site/data/` exists. |
