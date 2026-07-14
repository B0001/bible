# Phase 15 — Reader-first UI: routes, auto-passage reader, subtraction

Turn the static reader from a *data explorer* (scored table + filters) into a
*reader*: the longest readable passage at your level IS the home screen.
Three hash routes; the table and setup controls move off the primary screen;
dead filters are deleted. `site/` only — `dash_app.py`, `scripts/`, data
formats, and `parser.py` are untouched. Depends on Phase 14.

## How to execute this phase (read first)

Tasks are grouped into batches by **file ownership** so they can run as
parallel subagents without merge conflicts. Never let two agents touch the
same file.

| Batch | Tasks (parallel within batch)      | Files owned per task                     |
|-------|------------------------------------|------------------------------------------|
| 1     | P15.1 · P15.2 · P15.3              | `site/index.html` · `site/app.js` · `site/style.css` |
| 2     | P15.4 · P15.5                      | `test_site_smoke.py` · `CLAUDE.md` + `test_static_export.py` |
| 3     | P15.6 (alone)                      | anything, integration fixes only         |

**Verification during Batches 1–2:** run `.venv/bin/ruff check .` and
`.venv/bin/python -m pytest -m "not e2e"` only. The e2e browser suite WILL
fail mid-redesign (HTML/JS/CSS land in separate commits) — that is expected;
do not "fix" it from inside P15.1–P15.5. Only P15.6 runs the full
`.venv/bin/python -m pytest` (which includes e2e when playwright + exported
site data are present).

Each task is one commit with the message given in the task. Batch N+1 starts
only after every Batch N task has committed.

## D1 Routes (locked)

Hash-based, no framework. Three `<section>` route containers in `index.html`:
`#route-read` (default), `#route-browse`, `#route-settings`. A `route()`
function toggles their `hidden` attributes from `location.hash`; any hash
other than `#browse` / `#settings` (including empty) shows read. Topbar nav:
`Read · Browse · ⚙`. All existing element ids are preserved — elements move
between sections but are never renamed, so every existing handler keeps
working (hidden elements still hold state; `score()` reads the vocab textarea
even while the settings route is hidden).

What lives where:

- **#route-read**: level slider panel (+ subtitle), learn-next chip strip,
  the new reader (`#reader`), reader action buttons, `#reader-count`,
  `#progress`.
- **#route-browse**: search + unread-only row, audio panel (with the
  heatmap/pauses toggles moved back inside it), verse table, pager.
- **#route-settings**: vocab label + textarea, export/import buttons + file
  input.

Deleted outright (not moved): `rate-min`, `rate-max`, `max-unknown` inputs
(superseded by difficulty ordering since Phase 13), the `find-passage`
button, `passage-rate` input, `#passage-panel`, and the whole
`<details id="advanced">` wrapper.

## D2 Reader model (locked)

- On every rescore *and* every level-slider change, compute the top passages
  at the current level: `topSpans(effKnown, total, READER_RATE=0.95,
  READER_MAX=10)`, where `effKnown[i]` counts tokens that are in the user's
  vocab∪learned set **or** have corpus rank ≤ `levelN()` (same definition the
  level label and passage feature already use).
- Spans longer than `READER_CHUNK = 120` verses are split into consecutive
  ≤120-verse chunks (so slider-at-100 → whole-Bible span becomes ten
  readable pages instead of one 31k-verse DOM). The queue is capped at
  `READER_MAX` entries total, in span order (longest span's chunks first).
- The reader shows passage `passageIdx` as continuous prose: an `<h2>` with
  the ref range, a muted meta line `N verses · M words · R% readable`, then
  one `.reader-text` block of `<sup>verseNum</sup>verse text ` runs (verse
  number = the ref segment after the last `:`). RTL bibles get `dir="rtl"`.
- Buttons: `← ` (prev), `✓ Done` (adds every ref in the current span to the
  read set, persists, advances), `→` (next, without marking). `#reader-count`
  shows `Passage K of N`. Empty queue → a friendly `.reader-empty` message
  and all three buttons disabled.

## P15.1 site/index.html — replace `<body>` (owns index.html only)

Keep `<head>` exactly as it is today. Replace everything from `<body>` to
`</body>` with exactly:

```html
<body>
  <header class="topbar">
    <select id="bible-select" aria-label="Bible"></select>
    <nav>
      <a href="#read" class="active">Read</a>
      <a href="#browse">Browse</a>
      <a href="#settings" aria-label="Settings">⚙</a>
    </nav>
    <span id="loading" hidden>Loading…</span>
  </header>

  <main>
    <div id="error" class="error" hidden></div>

    <section id="route-read">
      <section class="level-panel">
        <label for="level"><span id="level-label">Vocabulary level</span></label>
        <input type="range" id="level" min="0" max="100" step="1" value="50">
        <p class="subtitle">Drag the slider from simple to hard — passages grow as your vocabulary does.</p>
      </section>

      <section id="learn-next" class="learn-next"></section>

      <article id="reader" class="reader"></article>
      <div class="reader-actions">
        <button id="reader-prev" aria-label="Previous passage">←</button>
        <button id="reader-done">✓ Done</button>
        <button id="reader-next" aria-label="Next passage">→</button>
      </div>
      <p id="reader-count" class="reader-count"></p>
      <p id="progress" class="progress"></p>
    </section>

    <section id="route-browse" hidden>
      <div class="row">
        <label class="grow">Search
          <input type="search" id="search" placeholder="reference or text…">
        </label>
        <label class="check">
          <input type="checkbox" id="unread-only"> Unread only
        </label>
      </div>

      <!-- Local-only audio mode (P10.3): appears when the site was exported with
           `export_static.py --audio` AND is served locally with the audio
           directory in place (e.g. `python -m http.server` from the repo root).
           The deployed site ships no audio metadata, so this stays hidden. -->
      <section id="audio-panel" hidden>
        <audio id="audio-player" controls preload="none"></audio>
        <div class="row">
          <label class="check">
            <input type="checkbox" id="heatmap-toggle"> Emphasis heatmap
          </label>
          <label class="check">
            <input type="checkbox" id="pauses-toggle"> Pause markers
          </label>
        </div>
      </section>

      <table id="verse-table">
        <thead>
          <tr><th>Reference</th><th>Verse</th><th>%</th><th>Unknown</th><th>Read</th></tr>
        </thead>
        <tbody id="verse-body"></tbody>
      </table>
      <div class="pager">
        <button id="prev-page">Prev</button>
        <span id="page-info"></span>
        <button id="next-page">Next</button>
      </div>
    </section>

    <section id="route-settings" hidden>
      <label for="vocab">
        <span id="vocab-label">Words you already know (optional)</span>
      </label>
      <textarea id="vocab" rows="8" placeholder="paste the words you know…"></textarea>

      <div class="row">
        <button id="export-data">Export data</button>
        <button id="import-data">Import data</button>
        <input type="file" id="import-file" accept="application/json" hidden>
      </div>
    </section>
  </main>

  <script type="module" src="app.js"></script>
</body>
```

Sanity check before committing: `grep -c 'id="' site/index.html` — every id
referenced by P15.2's element list (below) must exist exactly once, and
`rate-min`, `rate-max`, `max-unknown`, `find-passage`, `passage-rate`,
`passage-panel`, `advanced` must not appear at all.

Commit: `P15.1 Route-based body: read / browse / settings`

## P15.2 site/app.js — reader, router, deletions (owns app.js only)

Apply the following edits. Line numbers are approximate — locate by the
quoted code, which is current and exact.

**E1 — constants.** Replace:

```js
const PAGE_SIZE = 20;
const PAUSE_THRESHOLD = 0.8;
```

with:

```js
const PAGE_SIZE = 20;
const READER_RATE = 0.95;   // a passage must read at >=95% at your level
const READER_CHUNK = 120;   // max verses per reader page (long spans get chunked)
const READER_MAX = 10;      // reader queue length
const PAUSE_THRESHOLD = 0.8;
```

**E2 — state.** Immediately after the line `let levelPos = 50;` (and its
trailing comment), add:

```js
let passages = [];    // Phase 15: reader queue of [i, j) verse spans
let passageIdx = 0;   // current position in the queue
let effKnown = [];    // per-verse known count at the current level
```

**E3 — element registration.** Replace the whole `for (const id of [...])`
list (currently starts `'bible-select', 'loading', 'vocab', 'vocab-label',
'rate-min',`) with exactly:

```js
for (const id of ['bible-select', 'loading', 'vocab', 'vocab-label',
  'search', 'unread-only',
  'export-data', 'import-data', 'import-file', 'progress',
  'verse-body', 'prev-page', 'next-page', 'page-info', 'error',
  'audio-panel', 'audio-player', 'heatmap-toggle', 'pauses-toggle', 'level',
  'level-label', 'learn-next',
  'route-read', 'route-browse', 'route-settings',
  'reader', 'reader-count', 'reader-prev', 'reader-done', 'reader-next']) {
```

(The camelCase accessors become `el.routeRead`, `el.routeBrowse`,
`el.routeSettings`, `el.reader`, `el.readerCount`, `el.readerPrev`,
`el.readerDone`, `el.readerNext`.)

**E4 — applyFilters.** Replace the entire current function:

```js
function applyFilters() {
  const loNum = parseFloat(el.rateMin.value);
  const hiNum = parseFloat(el.rateMax.value);
  const lo = (Number.isNaN(loNum) ? 0 : loNum) / 100;
  const hi = (Number.isNaN(hiNum) ? 100 : hiNum) / 100;
  const maxUnknown = parseInt(el.maxUnknown.value, 10);
  const capUnknown = !Number.isNaN(maxUnknown);
  const needle = stripMarks(el.search.value.trim());
  const unreadOnly = el.unreadOnly.checked;
  filtered = order.filter(i =>
    rates[i] >= lo && rates[i] <= hi &&
    (!capUnknown || total[i] - known[i] <= maxUnknown) &&
    (!needle || searchText[i].includes(needle)) &&
    (!unreadOnly || !reads.has(bible.refs[i])));
}
```

with:

```js
function applyFilters() {
  const needle = stripMarks(el.search.value.trim());
  const unreadOnly = el.unreadOnly.checked;
  filtered = order.filter(i =>
    (!needle || searchText[i].includes(needle)) &&
    (!unreadOnly || !reads.has(bible.refs[i])));
}
```

**E5 — delete the passage panel renderer.** Delete the line
`const MAX_PASSAGE_LINES = 30;` and the entire `function renderPassages() {
... }`. Do **not** delete `topSpans` or `longestSpan` — the reader uses them.

**E6 — reader functions.** Where `renderPassages` was, insert:

```js
// ---------------------------------------------------------------- reader (Phase 15)

// Per-verse known-token counts at the current level: the user's own words
// (vocab textarea + tapped chips) are free, plus the top-N most frequent
// words the level slider grants. Same definition as the level label.
function effectiveKnownCounts() {
  const vocab = knownSet();
  const N = levelN();
  const n = bible.tokens.length;
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    let k = 0;
    for (const t of bible.tokens[i])
      if (vocab.has(t) || (ranks.get(t) ?? Infinity) <= N) k++;
    out[i] = k;
  }
  return out;
}

// Rebuild the reader queue: top spans at the current level, long spans
// chunked to READER_CHUNK verses so slider-at-100 (whole-Bible span) stays
// renderable. See PHASE15_DESIGN.md D2.
function computePassages() {
  effKnown = effectiveKnownCounts();
  const spans = topSpans(effKnown, total, READER_RATE, READER_MAX);
  passages = [];
  for (const [i, j] of spans)
    for (let s = i; s < j && passages.length < READER_MAX; s += READER_CHUNK)
      passages.push([s, Math.min(s + READER_CHUNK, j)]);
  passageIdx = 0;
}

function renderReader() {
  const box = el.reader;
  box.textContent = '';
  if (!passages.length) {
    const p = document.createElement('p');
    p.className = 'reader-empty';
    p.textContent = 'Nothing readable at this level yet — drag the slider right, or tap a word above to learn it.';
    box.appendChild(p);
    el.readerCount.textContent = '';
    el.readerPrev.disabled = el.readerDone.disabled = el.readerNext.disabled = true;
    return;
  }
  passageIdx = Math.min(passageIdx, passages.length - 1);
  const [i, j] = passages[passageIdx];
  let k = 0, t = 0;
  for (let v = i; v < j; v++) { k += effKnown[v]; t += total[v]; }
  const rate = t ? (100 * k / t).toFixed(1) : '0.0';

  const head = document.createElement('h2');
  head.className = 'reader-head';
  head.textContent = `${bible.refs[i]} – ${bible.refs[j - 1]}`;
  const meta = document.createElement('p');
  meta.className = 'reader-meta';
  meta.textContent = `${j - i} verses · ${t} words · ${rate}% readable`;

  const text = document.createElement('div');
  text.className = 'reader-text';
  if (rtlLangs.has(bible.lang)) { text.dir = 'rtl'; text.classList.add('rtl'); }
  for (let v = i; v < j; v++) {
    const sup = document.createElement('sup');
    sup.textContent = bible.refs[v].split(':').pop();
    text.appendChild(sup);
    text.appendChild(document.createTextNode(bible.verses[v] + ' '));
  }
  box.append(head, meta, text);

  el.readerCount.textContent = `Passage ${passageIdx + 1} of ${passages.length}`;
  el.readerPrev.disabled = passageIdx === 0;
  el.readerNext.disabled = passageIdx >= passages.length - 1;
  el.readerDone.disabled = false;
}

// ---------------------------------------------------------------- router (Phase 15)

function route() {
  const h = ['#read', '#browse', '#settings'].includes(location.hash)
    ? location.hash : '#read';
  el.routeRead.hidden = h !== '#read';
  el.routeBrowse.hidden = h !== '#browse';
  el.routeSettings.hidden = h !== '#settings';
  for (const a of document.querySelectorAll('.topbar nav a'))
    a.classList.toggle('active', a.getAttribute('href') === h);
}
```

**E7 — rescore.** Replace:

```js
function rescore() {
  if (!bible) return;
  score();
  page = 0;
  refresh();
  renderLearnNext();
}
```

with:

```js
function rescore() {
  if (!bible) return;
  score();
  page = 0;
  refresh();
  renderLearnNext();
  computePassages();
  renderReader();
}
```

**E8 — level label copy.** In `updateLevelLabel`, replace the assignment
with:

```js
  el.levelLabel.textContent = `${levelN()} words · ${readable} of ${filtered.length} verses readable`;
```

**E9 — slider handler.** The passage queue depends on N. Replace the body of
the level `input` listener:

```js
    levelPos = Number(el.level.value);
    saveJSON('level:' + bible.id, levelPos);
    refresh();
    renderLearnNext();
```

with:

```js
    levelPos = Number(el.level.value);
    saveJSON('level:' + bible.id, levelPos);
    refresh();
    renderLearnNext();
    computePassages();
    renderReader();
```

**E10 — dead listeners.** Replace:

```js
for (const input of [el.rateMin, el.rateMax, el.maxUnknown, el.search]) {
  input.addEventListener('input', debounce(() => { page = 0; refresh(); }, 200));
}
```

with:

```js
el.search.addEventListener('input', debounce(() => { page = 0; refresh(); }, 200));
```

Delete both of these listeners entirely:

```js
el.findPassage.addEventListener('click', () => { if (bible) renderPassages(); });
el.passageRate.addEventListener('change', () => {
  if (bible && !el.passagePanel.hidden) renderPassages();
});
```

In their place add the reader + router listeners:

```js
el.readerPrev.addEventListener('click', () => {
  passageIdx--; renderReader(); el.reader.scrollIntoView();
});
el.readerNext.addEventListener('click', () => {
  passageIdx++; renderReader(); el.reader.scrollIntoView();
});
el.readerDone.addEventListener('click', () => {
  if (!passages.length) return;
  const [i, j] = passages[passageIdx];
  for (let v = i; v < j; v++) reads.add(bible.refs[v]);
  saveJSON('reads:' + bible.id, [...reads]);
  renderProgress();
  if (passageIdx < passages.length - 1) passageIdx++;
  renderReader();
  el.reader.scrollIntoView();
});

window.addEventListener('hashchange', route);
```

**E11 — loadBible.** Delete the line `el.passagePanel.hidden = true;`.

**E12 — init.** In `init()`, immediately before `await loadBible(id);`, add
one line: `route();`.

Sanity check before committing:
`grep -n "rateMin\|rateMax\|maxUnknown\|findPassage\|passageRate\|passagePanel\|renderPassages\|MAX_PASSAGE_LINES" site/app.js`
must return nothing.

Commit: `P15.2 Reader-first app.js: router, auto-passage reader, filter deletions`

## P15.3 site/style.css — reader styles, nav, chip strip (owns style.css only)

**Delete** these rule blocks (they styled the removed passage panel and
button): `button#find-passage { ... }`, `button#find-passage:hover... { ... }`,
`#passage-panel.passage { ... }`, `details.passage { ... }`, `.passage { ... }`,
`.passage-head { ... }`, `.passage-line { ... }`. Keep `.rtl { ... }`.

**Append** (or place where the deleted passage block was):

```css
/* Topbar nav (Phase 15 routes) */
.topbar nav { display: flex; gap: 4px; margin-left: auto; }
.topbar nav a {
  min-height: 48px;
  display: flex;
  align-items: center;
  padding: 0 12px;
  border-radius: 10px;
  color: var(--muted);
  text-decoration: none;
  font-weight: 600;
}
.topbar nav a.active { color: var(--accent); background: var(--accent-weak); }

/* Reader (Phase 15) */
.reader {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px;
  margin: 16px 0;
}
.reader-head { font-size: 1.25rem; margin: 0 0 4px; text-transform: capitalize; }
.reader-meta { color: var(--muted); font-size: 0.8rem; margin: 0 0 12px; }
.reader-text { font-size: 1.15rem; line-height: 1.7; max-width: 65ch; word-break: break-word; }
.reader-text sup { color: var(--muted); font-size: 0.7em; margin: 0 2px 0 4px; }
.reader-empty { color: var(--muted); margin: 0; }
.reader-actions { display: flex; gap: 8px; margin: 12px 0; }
.reader-actions button { flex: 1; }
#reader-done { background: var(--accent); color: #fff; border-color: var(--accent); }
#reader-done:hover:not(:disabled) { filter: brightness(1.1); }
#reader-done:disabled { opacity: 0.5; }
.reader-count { color: var(--muted); font-size: 0.8rem; text-align: center; margin: 0 0 8px; }

/* Learn-next: one horizontally scrollable row (Phase 15) */
#learn-next { flex-wrap: nowrap; overflow-x: auto; padding-bottom: 4px; }
#learn-next h3 { display: none; }
#learn-next .chip { flex: 0 0 auto; }
#learn-next p { flex: 0 0 auto; align-self: center; white-space: nowrap; }
```

If the existing `#learn-next` rule sets `flex-wrap: wrap`, the appended
override wins by order — verify the appended block comes *after* it in the
file; move the block lower if not.

Commit: `P15.3 Reader styles, topbar nav, chip strip`

## P15.4 test_site_smoke.py — rewrite for the reader UI (owns that file only)

Replace the six route-dependent tests. Keep the module docstring (update its
prose to mention routes), imports, `_free_port`, `site_url`, and
`loaded_page` fixtures as they are, with ONE change to `loaded_page`: the
readiness wait becomes the reader, not the table:

```python
        page.wait_for_function(
            "document.querySelectorAll('#reader .reader-text, #reader .reader-empty').length > 0",
            timeout=60000,
        )
```

Then replace every test function (everything after the fixtures) with
exactly:

```python
def test_no_js_errors(loaded_page):
    _, errors = loaded_page
    assert errors == []


def test_bible_dropdown_populates(loaded_page):
    page, _ = loaded_page
    with open(_MANIFEST, encoding="utf-8") as f:
        expected = len(json.load(f)["bibles"])
    options = page.eval_on_selector("#bible-select", "el => el.options.length")
    assert options == expected


def test_read_route_shows_passage_by_default(loaded_page):
    page, _ = loaded_page
    assert not page.eval_on_selector("#route-read", "el => el.hidden")
    assert page.eval_on_selector("#route-browse", "el => el.hidden")
    head = page.eval_on_selector("#reader .reader-head", "el => el.textContent")
    assert head  # a ref range like "john 13:33 – john 16:18"
    count = page.eval_on_selector("#reader-count", "el => el.textContent")
    assert count.startswith("Passage 1 of")


def test_learn_next_chips_render(loaded_page):
    page, _ = loaded_page
    assert page.eval_on_selector_all("#learn-next .chip", "els => els.length") > 0


def test_done_marks_read_and_advances(loaded_page):
    page, errors = loaded_page
    page.click("#reader-done")
    page.wait_for_function(
        "document.getElementById('reader-count').textContent.startsWith('Passage 2 of')",
        timeout=15000,
    )
    progress = page.eval_on_selector("#progress", "el => el.textContent")
    assert progress and not progress.startswith("0 of")
    assert errors == []


def test_slider_rebuilds_reader_queue(loaded_page):
    page, errors = loaded_page
    page.fill("#level", "100")
    page.dispatch_event("#level", "input")
    page.wait_for_function(
        "document.getElementById('reader-count').textContent.startsWith('Passage 1 of')",
        timeout=15000,
    )
    meta = page.eval_on_selector("#reader .reader-meta", "el => el.textContent")
    assert "verses" in meta
    assert errors == []


def test_browse_route_shows_table(loaded_page):
    page, _ = loaded_page
    page.evaluate("location.hash = '#browse'")
    page.wait_for_function("!document.getElementById('route-browse').hidden", timeout=5000)
    assert page.eval_on_selector_all("#verse-body tr", "els => els.length") > 0
    page.evaluate("location.hash = '#read'")


def test_settings_route_has_vocab_and_export(loaded_page):
    page, _ = loaded_page
    page.evaluate("location.hash = '#settings'")
    page.wait_for_function("!document.getElementById('route-settings').hidden", timeout=5000)
    assert page.eval_on_selector("#vocab", "el => !el.closest('section').hidden")
    assert page.eval_on_selector("#export-data", "el => !!el")
    page.evaluate("location.hash = '#read'")


def test_no_horizontal_scroll_at_360(loaded_page):
    page, _ = loaded_page
    assert page.evaluate("document.documentElement.scrollWidth") <= 360


def test_dark_mode_background(loaded_page):
    page, _ = loaded_page
    page.emulate_media(color_scheme="dark")
    bg = page.eval_on_selector("body", "el => getComputedStyle(el).backgroundColor")
    assert bg == "rgb(15, 17, 21)"
    page.emulate_media(color_scheme="light")
```

Notes: the tests share one page (module-scoped fixture) and are order
dependent by design — Done runs before the slider test, which resets the
queue. Do not run this file during this task (`pytest -m "not e2e"` only);
P15.6 runs it against the integrated site.

Commit: `P15.4 Rewrite e2e smoke tests for the reader UI`

## P15.5 docs + stale assertions (owns CLAUDE.md and test_static_export.py only)

1. `CLAUDE.md`, in the `test_site_smoke.py` bullet: replace the feature list
   ("the Bible dropdown populates, verses + learn-next chips render, chip
   taps rescore, no horizontal scroll at 360px, dark-mode background") with:
   "the read route auto-renders the longest passage at the current level,
   Done marks verses read and advances the queue, the slider rebuilds the
   queue, browse/settings routes show the table and vocab box, no horizontal
   scroll at 360px, dark-mode background".
2. Run `.venv/bin/python -m pytest test_static_export.py -q`. If
   `test_mobile_ui_structure` fails because it asserts an id or attribute
   this phase removed (`rate-min`, `rate-max`, `max-unknown`,
   `find-passage`, `passage-rate`, `passage-panel`, `advanced`), delete only
   those assertions — do not re-add elements, do not touch other assertions.
   If it passes untouched, leave the file alone.

Commit: `P15.5 Docs + assertion cleanup for the reader UI`

## P15.6 Integration verification (single agent, after Batches 1–2)

1. `.venv/bin/ruff check .` and full `.venv/bin/python -m pytest` (e2e
   included — requires `pip install '.[e2e]'`, `playwright install
   chromium`, and existing `site/data/`; run `.venv/bin/python
   scripts/export_static.py` first if `site/data/manifest.json` is missing).
2. Manual browser sweep at 360×800 (playwright script or devtools),
   documenting each result in the commit message:
   - Read route on load: a passage renders with prose + sup verse numbers;
     `#reader-count` says `Passage 1 of N` (N ≥ 2 for NASB at default level).
   - Slider to 100: queue becomes chunks of ≤120 verses starting
     `genesis 1:1`; slider back to 50: queue rebuilds.
   - `✓ Done`: progress line increments by the passage's verse count;
     reader advances.
   - `#browse`: table renders, search filters, unread-only hides the verses
     just marked done. `#settings`: vocab edits rescore the reader (type a
     word, return to `#read`, queue may change).
   - Hebrew (wlc): reader text renders RTL. No console errors anywhere.
3. Fix whatever the sweep finds (any file), one commit:
   `P15.6 Integration fixes for reader UI` (or, if nothing to fix, skip the
   commit and note it).

## Out of scope

- No service worker / offline; no cache-busting for app.js (separate task).
- No per-word tap-to-define inside the reader (future phase).
- `dash_app.py`, `parser.py`, `scripts/`, CI, and data formats unchanged.
- Do not edit `site/rank.js` or `site/vendor/`.

## Definition of done

- All three routes work by hash, read is default, and every P15.4 e2e test
  passes locally with the `[e2e]` extra.
- `git grep -l "rate-min\|find-passage\|passage-panel"` returns only design
  docs (PHASE*.md).
- `ruff check .` clean; full `pytest` green.
- Deployed site (after merge) opens straight into a readable passage.
