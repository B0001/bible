# Phase 13 — Learning mode: the level slider and the learn-next ranker

Make the static reader's primary experience "read in learning order": a
vocabulary-level slider that drags from simple to hard, and a "what to learn
next" ranker as the headline feature. Works in **all 12 languages** because it
sits on Phase 12's corpus ranks (dependency: Phase 12 must be done, including
`site/rank.js`). All changes are in `site/` only — `dash_app.py` is untouched.

## D1 Mental model (locked)

- Every verse has a `difficulty` (Phase 12): the vocabulary size at which it
  becomes ≥95% readable.
- The **level slider** sets N = "assume I know the top-N most frequent words
  of this Bible". Verses with `difficulty ≤ N` are *readable now*; the rest
  are dimmed. Dragging right = harder. The verse list is always sorted in
  learning order (difficulty ascending), so the slider is a scrubber through
  the whole Bible from simplest to hardest.
- The user's own words (vocab textarea + tapped chips) are *free*: they lower
  difficulties via the `known` set, recomputed client-side on every change.
- **Learn next** (the priority feature, shown above the verse list): the top
  10 stems, ranked by how many currently-unreadable verses each would unlock
  at the current level. Tapping a chip marks the stem as learned and rescores
  instantly.

## P13.1 app.js: state and scoring changes

Import once at startup (top of the init flow, before the first `loadBible`):
`const { corpusRanks, verseDifficulty } = await import('./rank.js');` — follow
the existing dynamic-import style used by `stemmerFor`.

New module-level state (near the existing `let known = []` block):

```js
let ranks = null;        // Map form -> corpus rank, per loaded bible
let difficulty = [];     // per-verse difficulty (number | null)
let learned = new Set(); // stems tapped in the learn-next panel
let levelPos = 50;       // slider position 0..100 (persisted per bible)
```

In `loadBible(id)`, after `bible` is fetched: `ranks =
corpusRanks(bible.tokens);`, `learned = new Set(loadJSON('learned:' +
bible.id, []));`, `levelPos = loadJSON('level:' + bible.id, 50);`,
`el.level.value = levelPos;`.

In `score()`: build the known set as the union of the vocab textarea and
`learned`: `const vocab = tokenizeVocab(el.vocab.value, bible.lang); for
(const s of learned) vocab.add(s);` (add learned stems verbatim — they are
already stems; never re-stem them). After the existing per-verse loop, add:
`difficulty = bible.tokens.map(toks => verseDifficulty(toks, ranks, 0.95,
vocab));` and replace the `order` sort with learning order:

```js
const inf = Number.POSITIVE_INFINITY;
order = Array.from({ length: n }, (_, i) => i).sort((a, b) =>
  (difficulty[a] ?? inf) - (difficulty[b] ?? inf) || a - b);
```

Add `levelN()`: maps slider position to vocabulary size on a log scale.

```js
function levelN() {
  const max = Math.max(ranks.size, 1);
  if (max <= 50) return max;
  return Math.round(50 * Math.pow(max / 50, levelPos / 100));
}
```

## P13.2 index.html + app.js: the level slider

In `site/index.html`, directly under the Bible `<select>` block, add:

```html
<section class="level-panel">
  <label for="level"><span id="level-label">Vocabulary level</span></label>
  <input type="range" id="level" min="0" max="100" step="1" value="50">
</section>
<section id="learn-next" class="learn-next"></section>
```

Register `'level'`, `'level-label'`, `'learn-next'` in the `el` id list in
app.js.

Behavior (app.js):

- `el.level` `input` event (debounced 150 ms): set `levelPos =
  Number(el.level.value)`, `saveJSON('level:' + bible.id, levelPos)`, then
  `refresh()` and `renderLearnNext()` (no re-`score()` needed — difficulty
  doesn't depend on N), then jump the page to the frontier: find the first
  index `f` in `filtered` with `(difficulty[idx] ?? Infinity) > levelN()`;
  set `page = Math.max(0, Math.floor((f === -1 ? filtered.length - 1 : f) /
  PAGE_SIZE) )` before `renderTable()`.
- Label text, updated on every slider/vocab change:
  `Vocabulary level: N words — M of ${total} verses readable` where `M` is
  the count of verses with `difficulty !== null && difficulty <= levelN()`.
- In `renderTable()`, add a class to each row: `tr.classList.add(
  (difficulty[i] ?? Infinity) <= levelN() ? 'readable' : 'beyond')`, and
  append a small difficulty badge to the ref cell:
  `<span class="diff">#${difficulty[i] ?? '—'}</span>`.
- CSS (site/style.css): `.beyond { opacity: 0.45; }` and `.diff { color:
  #888; font-size: 0.8em; margin-left: 0.35em; }` (Phase 14 restyles; keep
  minimal here).

## P13.3 The learn-next ranker (headline feature)

Add to `site/rank.js` (exported, so the parity test can cover it):

```js
// Top stems by how many unreadable-at-N verses learning each one unlocks.
// A verse is readable at N iff (# tokens with effective rank > N) <=
// floor((1 - target) * len). See PHASE13_DESIGN.md P13.3.
export function nextWords(tokenLists, ranks, N, known, target = 0.95, topN = 10) {
  const fallback = ranks.size + 1;
  const unlocks = new Map();
  for (const toks of tokenLists) {
    if (!toks.length) continue;
    const slack = toks.length - Math.max(1, Math.ceil(target * toks.length));
    const over = new Map(); // stem -> occurrences with eff rank > N
    let overTotal = 0;
    for (const t of toks) {
      const r = known.has(t) ? 0 : (ranks.get(t) ?? fallback);
      if (r > N) { over.set(t, (over.get(t) || 0) + 1); overTotal++; }
    }
    if (overTotal <= slack) continue;            // already readable
    for (const [stem, c] of over)
      if (overTotal - c <= slack)                // learning stem unlocks it
        unlocks.set(stem, (unlocks.get(stem) || 0) + 1);
  }
  return [...unlocks.entries()]
    .map(([stem, count]) => ({ stem, count, rank: ranks.get(stem) ?? fallback }))
    .sort((a, b) => b.count - a.count || a.rank - b.rank)
    .slice(0, topN);
}
```

app.js `renderLearnNext()` — called from `rescore()` and the slider handler:

- Compute the same known set used by `score()` (factor the union into a
  helper `knownSet()` so score and this panel can't drift).
- `const words = nextWords(bible.tokens, ranks, levelN(), knownSet());`
- Render into `#learn-next`: a heading `Learn next` and one `<button
  class="chip">` per entry with text `${stem} +${count}` and `title="unlocks
  ${count} verse(s) at your level"`. Empty result → hide the section.
- Chip click: `learned.add(stem); saveJSON('learned:' + bible.id,
  [...learned]); rescore();` (rescore re-runs score → difficulty → panel, so
  the chip disappears and newly readable verses light up immediately).
- After the chips, render a muted line `Learned: K words · <button
  id="reset-learned">reset</button>` when `learned.size > 0`; reset clears
  `learned`, persists, rescores.

Sanity fixture for `nextWords` (add to the P12.5 node parity test): with the
D2 fixture, `N=1`, `known=∅`, `target=0.95` → v1 needs `b` (slack 0, over={b:1});
v2 needs `b` and `c` each with the other still over → v2 unlocked by neither
(over-total 2, removing one leaves 1 > slack 0); v3 over={c:1,d:1}, same, no
single unlock. Expected: `[{stem:"b", count:1, rank:2}]`.

## P13.4 Simplify the controls (learning mode is the default)

- `site/index.html`: wrap the comprehension min/max inputs, max-unknown,
  unread-only checkbox, find-passage button, export/import buttons, and the
  audio analytics toggles in `<details id="advanced"><summary>Advanced
  </summary>…</details>` below the verse table. Do not change any element
  ids — all existing handlers keep working. The vocab textarea, search box,
  bible select, level slider, and learn-next panel stay outside (primary UI),
  in that order: bible → level → learn-next → vocab → search → table.
- Change the rate inputs' default values in index.html to `0` and `100` (the
  learning-order view must show all verses by default; users narrow via the
  slider now). If the defaults live in app.js instead, change them there.
- `localStorage` export/import (app.js `exportData`/`importFile` handlers):
  add `'learned:'` and `'level:'` to the accepted key prefixes in both.

## P13.5 Tests

- Extend the P12.5 node parity test with the `nextWords` fixture above
  (expected `[{stem:"b",count:1,rank:2}]` — assert stem list and counts).
- Playwright smoke (optional but preferred; follow the pattern in the session
  scratchpad `karaoke_check.py` if present, else skip): serve the repo, load
  the site, assert `#level` exists, drag it to 100 (`page.fill`), assert at
  least one `tr.readable` row and that the learn-next panel renders chips for
  a small vocabulary.

## Definition of done

- `python scripts/export_static.py` + `python -m http.server` from repo root:
  the site loads, default view is sorted simplest-first, the slider dims/
  undims verses and moves the page to the frontier, tapping a learn-next chip
  immediately shrinks the panel and lights up newly readable verses, and all
  of it works identically after switching to the Hebrew (wlc) and Greek (gnt)
  bibles — that is the multi-language acceptance test.
- All Phase 12 tests still green; node parity test covers `nextWords`.
- No regressions: vocab paste, search, read tracking, pagination, and the
  audio karaoke (when exported with `--audio`) still work.
