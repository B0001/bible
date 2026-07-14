// Static Bible reader — scores verses by comprehension against the user's vocab.
// Vanilla JS ES module; per-language Snowball stemmers are dynamically imported
// from site/vendor/ as listed in the manifest's `stemmers` map.

// ---------------------------------------------------------------- state

const PAGE_SIZE = 20;
const READER_RATE = 0.95;   // a passage must read at >=95% at your level
const READER_CHUNK = 120;   // max verses per reader page (long spans get chunked)
const READER_MAX = 10;      // reader queue length
const PAUSE_THRESHOLD = 0.8;  // s; inter-word gap that marks a breathing point (P10.7)
const HEATMAP_CAP = 2;        // word duration >= CAP x chapter median = full intensity

let manifest = null;          // manifest.json content
let bible = null;             // current bible: {id, name, lang, refs, verses, tokens}
let searchText = [];          // per-verse mark-stripped lowercase "ref text" for search
let known = [];               // per-verse known-token counts
let total = [];               // per-verse total-token counts
let rates = [];               // per-verse comprehension rates
let order = [];               // verse indices sorted by rate desc
let filtered = [];            // indices after filters, in `order` order
let reads = new Set();        // read refs for current bible
let page = 0;
let audioIndex = null;        // {audio_base, chapters: {"Gen 1": "Gen_001.json"}} or null
let audioChapter = null;      // sidecar of the chapter loaded in the player
let loopWord = null;          // {start, end} of a double-clicked word to loop
let curWordSpan = null;       // currently highlighted word span (karaoke)
let chapterMedian = 0;        // median word duration for the loaded chapter (heatmap)
let heatmapOn = loadJSON('audio:heatmap', false);  // P10.7 analytics toggles
let pausesOn = loadJSON('audio:pauses', false);
const sidecarCache = new Map();
let corpusRanks = null;       // Phase 12: corpus frequency ranks (dynamically imported)
let verseDifficulty = null;   // Phase 12: verse difficulty calculator (dynamically imported)
let nextWords = null;         // Phase 13: learn-next ranker (dynamically imported)
let ranks = null;             // Map form -> corpus rank, per loaded bible
let difficulty = [];          // per-verse difficulty (number | null)
let learned = new Set();      // stems tapped in the learn-next panel
let levelPos = 50;            // slider position 0..100 (persisted per bible)
let passages = [];    // Phase 15: reader queue of [i, j) verse spans
let passageIdx = 0;   // current position in the queue
let effKnown = [];    // per-verse known count at the current level

// localStorage helpers — can throw in private mode, so wrap everything.
function loadJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}
function saveJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore (private mode / quota) */
  }
}

// ---------------------------------------------------------------- tokenizers

// Per-language stemmer instances (null = no stemmer for that language),
// dynamically imported on first use from the manifest's `stemmers` map.
const stemmerCache = new Map();
let currentStemmer = null;   // stemmer for the loaded bible's language
let currentStops = new Set(); // that language's NLTK stopword set
let rtlLangs = new Set(['he']);

async function stemmerFor(lang) {
  if (stemmerCache.has(lang)) return stemmerCache.get(lang);
  const path = (manifest.stemmers || {})[lang] || null;
  let instance = null;
  if (path) {
    try {
      const mod = await import('./' + path);
      instance = new mod.default();
    } catch (e) {
      showError(`Stemmer for "${lang}" failed to load (${e.message}); ` +
        'matching exact word forms only.');
    }
  }
  stemmerCache.set(lang, instance);
  return instance;
}

function stopsFor(lang) {
  return new Set((manifest.stopwords || {})[lang] || []);
}

// Stem one lowercased word with the current language's stemmer, keeping
// stopwords verbatim (NLTK SnowballStemmer(ignore_stopwords=True) behavior).
function normalizeWord(word) {
  if (currentStops.has(word)) return word;
  return currentStemmer ? currentStemmer.stem(word) : word;
}

// Normalize the whole vocab textarea into a Set of forms matching the
// pipeline's pre-normalized verse tokens. NB: JS \w is ASCII-only, so the
// generic path uses \p{L}\p{N}_ to mirror Python's Unicode-aware \w.
function tokenizeVocab(text, lang) {
  const set = new Set();
  if (lang === 'he') {
    const stripped = text.replace(/[֑-ׇ]/g, '');
    for (const m of stripped.match(/[א-ת]+/g) || []) set.add(m);
  } else if (lang === 'el') {
    const stripped = text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    for (const m of stripped.match(/[α-ω]+/g) || []) set.add(m);
  } else {
    let t = text;
    if (lang === 'ar') t = t.replace(/[ً-ْٰـ]/g, ''); // harakat + tatweel
    for (const m of t.toLowerCase().match(/[\p{L}\p{N}_]+/gu) || [])
      set.add(normalizeWord(m));
  }
  return set;
}

// Strip Hebrew marks, combining diacritics, and Arabic harakat/tatweel,
// lowercase — for mark-insensitive search (same class as the Python side).
function stripMarks(s) {
  return s.normalize('NFD').replace(/[֑-ׇ̀-ًͯ-ْٰـ]/g, '').toLowerCase();
}

// ---------------------------------------------------------------- scoring

// Helper: return the union of vocab textarea and learned stems
function knownSet() {
  const vocab = tokenizeVocab(el.vocab.value, bible.lang);
  for (const s of learned) vocab.add(s);
  return vocab;
}

function score() {
  const vocab = knownSet();

  const n = bible.tokens.length;
  known = new Array(n);
  total = new Array(n);
  rates = new Array(n);
  for (let i = 0; i < n; i++) {
    const toks = bible.tokens[i];
    let k = 0;
    for (const t of toks) if (vocab.has(t)) k++;
    known[i] = k;
    total[i] = toks.length;
    rates[i] = toks.length ? k / toks.length : 0;
  }

  // Phase 13: compute per-verse difficulty
  difficulty = bible.tokens.map(toks => verseDifficulty(toks, ranks, 0.95, vocab));

  // Phase 13: sort by learning order (difficulty ascending) instead of comprehension rate
  const inf = Number.POSITIVE_INFINITY;
  order = Array.from({ length: n }, (_, i) => i).sort((a, b) =>
    (difficulty[a] ?? inf) - (difficulty[b] ?? inf) || a - b);
}

// Longest contiguous run of verses whose aggregate comprehension >= minRate.
// 1:1 port of parser.py longest_span (prefix sums + monotonic stack).
function longestSpan(knownArr, totalArr, minRate) {
  const n = knownArr.length;
  const P = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) P[i + 1] = P[i] + knownArr[i] - minRate * totalArr[i];
  const stack = [];
  for (let i = 0; i <= n; i++)
    if (!stack.length || P[i] < P[stack[stack.length - 1]]) stack.push(i);
  let bestLen = 0, bestI = 0, bestJ = 0;
  for (let j = n; j >= 0 && stack.length; j--) {
    while (stack.length && P[j] >= P[stack[stack.length - 1]]) {
      const i = stack.pop();
      if (j - i > bestLen) { bestLen = j - i; bestI = i; bestJ = j; }
    }
  }
  return bestLen > 0 ? [bestI, bestJ] : null;
}

// Phase 13: maps slider position to vocabulary size on a log scale.
function levelN() {
  const max = Math.max(ranks.size, 1);
  if (max <= 50) return max;
  return Math.round(50 * Math.pow(max / 50, levelPos / 100));
}

// Phase 13: update level label with current word count and readable verse count
function updateLevelLabel() {
  if (!el.levelLabel) return;
  const readable = filtered.filter(i => (difficulty[i] ?? Infinity) <= levelN()).length;
  el.levelLabel.textContent = `${levelN()} words · ${readable} of ${filtered.length} verses readable`;
}

// ---------------------------------------------------------------- rendering

const el = {};
for (const id of ['bible-select', 'loading', 'vocab', 'vocab-label',
  'search', 'unread-only',
  'export-data', 'import-data', 'import-file', 'progress',
  'verse-body', 'prev-page', 'next-page', 'page-info', 'error',
  'audio-panel', 'audio-player', 'heatmap-toggle', 'pauses-toggle', 'level',
  'level-label', 'learn-next',
  'route-read', 'route-browse', 'route-settings',
  'reader', 'reader-count', 'reader-prev', 'reader-done', 'reader-next']) {
  el[id.replace(/-(\w)/g, (_, c) => c.toUpperCase())] = document.getElementById(id);
}

function showError(msg) {
  el.error.textContent = msg;
  el.error.hidden = !msg;
}

function applyFilters() {
  const needle = stripMarks(el.search.value.trim());
  const unreadOnly = el.unreadOnly.checked;
  filtered = order.filter(i =>
    (!needle || searchText[i].includes(needle)) &&
    (!unreadOnly || !reads.has(bible.refs[i])));
}

function renderProgress() {
  // "Readable" matches the level label: difficulty <= the slider's N.
  const N = levelN();
  let readable = 0, read = 0;
  for (let i = 0; i < difficulty.length; i++) {
    if (difficulty[i] !== null && difficulty[i] <= N) {
      readable++;
      if (reads.has(bible.refs[i])) read++;
    }
  }
  el.progress.textContent = readable
    ? `${read} of ${readable} readable verses read`
    : '';
}

function renderTable() {
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  page = Math.min(Math.max(0, page), pages - 1);
  const rtl = rtlLangs.has(bible.lang);
  const body = el.verseBody;
  body.textContent = '';
  for (const i of filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)) {
    const tr = document.createElement('tr');
    tr.dataset.ref = bible.refs[i]; // audio highlight targets rows by ref

    const tdRef = document.createElement('td');
    tdRef.textContent = bible.refs[i];
    if (audioIndex) {
      const btn = document.createElement('button');
      btn.className = 'play';
      btn.textContent = '▶';
      btn.title = 'Play this verse';
      btn.addEventListener('click', () => playVerse(bible.refs[i]));
      tdRef.prepend(btn);
    }

    const tdVerse = document.createElement('td');
    tdVerse.className = 'verse';
    if (rtl) { tdVerse.dir = 'rtl'; tdVerse.classList.add('rtl'); }
    renderVerseCell(tdVerse, bible.refs[i], bible.verses[i]);

    const tdRate = document.createElement('td');
    tdRate.textContent = (rates[i] * 100).toFixed(1);
    tdRate.className = 'rate';

    const tdUnknown = document.createElement('td');
    tdUnknown.textContent = total[i] - known[i];
    tdUnknown.className = 'rate';

    const tdRead = document.createElement('td');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = reads.has(bible.refs[i]);
    cb.addEventListener('change', () => {
      if (cb.checked) reads.add(bible.refs[i]);
      else reads.delete(bible.refs[i]);
      saveJSON('reads:' + bible.id, [...reads]);
      renderProgress();
      if (el.unreadOnly.checked) refresh();
    });
    tdRead.appendChild(cb);
    tdRead.className = 'read';

    tr.append(tdRef, tdVerse, tdRate, tdUnknown, tdRead);
    body.appendChild(tr);
  }
  el.pageInfo.textContent = `page ${page + 1} of ${pages}`;
  el.prevPage.disabled = page === 0;
  el.nextPage.disabled = page >= pages - 1;
}

function refresh() {
  applyFilters();
  updateLevelLabel();
  renderProgress();
  renderTable();
}

function rescore() {
  if (!bible) return;
  score();
  page = 0;
  refresh();
  renderLearnNext();
  computePassages();
  renderReader();
}

// Top-K non-overlapping passages, longest first: greedily take the longest
// span, split the surrounding segments, repeat. Reuses longestSpan per
// segment with the result cached, so each iteration only rescans the two
// segments the previous split created.
function topSpans(knownArr, totalArr, minRate, k = 10) {
  const spanOf = (lo, hi) => {
    const s = longestSpan(knownArr.slice(lo, hi), totalArr.slice(lo, hi), minRate);
    return s ? [lo + s[0], lo + s[1]] : null;
  };
  const segs = [{ lo: 0, hi: knownArr.length, span: spanOf(0, knownArr.length) }];
  const out = [];
  while (out.length < k) {
    let bi = -1;
    for (let s = 0; s < segs.length; s++) {
      const sp = segs[s].span;
      if (sp && (bi < 0 || sp[1] - sp[0] > segs[bi].span[1] - segs[bi].span[0])) bi = s;
    }
    if (bi < 0) break;
    const { lo, hi, span } = segs[bi];
    out.push(span);
    const repl = [];
    if (span[0] > lo) repl.push({ lo, hi: span[0], span: spanOf(lo, span[0]) });
    if (hi > span[1]) repl.push({ lo: span[1], hi, span: spanOf(span[1], hi) });
    segs.splice(bi, 1, ...repl);
  }
  return out;
}

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

function renderLearnNext() {
  if (!bible || !ranks) return;
  const vocab = knownSet();
  const words = nextWords(bible.tokens, ranks, levelN(), vocab);
  if (words.length === 0) {
    el.learnNext.innerHTML = '';
    return;
  }
  el.learnNext.innerHTML = '';
  const heading = document.createElement('h3');
  heading.textContent = 'Learn next';
  el.learnNext.appendChild(heading);
  const subtitle = document.createElement('p');
  subtitle.className = 'learn-next-subtitle';
  subtitle.textContent = 'tap a word once you know it';
  el.learnNext.appendChild(subtitle);
  for (const { stem, count } of words) {
    const btn = document.createElement('button');
    btn.className = 'chip';
    btn.textContent = `${stem} +${count}`;
    btn.title = `unlocks ${count} verse(s) at your level`;
    btn.addEventListener('click', () => {
      learned.add(stem);
      saveJSON('learned:' + bible.id, [...learned]);
      rescore();
    });
    el.learnNext.appendChild(btn);
  }
  if (learned.size > 0) {
    const muted = document.createElement('p');
    muted.style.fontSize = '0.9em';
    muted.style.color = '#888';
    muted.textContent = `Learned: ${learned.size} words `;
    const resetBtn = document.createElement('button');
    resetBtn.id = 'reset-learned';
    resetBtn.textContent = 'Reset';
    resetBtn.style.marginLeft = '0.5em';
    resetBtn.addEventListener('click', () => {
      learned.clear();
      saveJSON('learned:' + bible.id, []);
      rescore();
    });
    muted.appendChild(resetBtn);
    el.learnNext.appendChild(muted);
  }
}

// ---------------------------------------------------------------- audio (P10.3)

// Local-only mode: timings ship with the export (--audio); the audio files
// themselves are resolved via audioIndex.audio_base, which only resolves when
// the site is served locally (e.g. `python -m http.server` from the repo
// root — file:// blocks fetch, so a local server is required). On the
// deployed site there is no audio metadata and none of this runs.

async function playVerse(ref) {
  const chapterKey = ref.slice(0, ref.lastIndexOf(':'));
  const file = audioIndex && audioIndex.chapters[chapterKey];
  if (!file) return;
  let sidecar = sidecarCache.get(file);
  if (!sidecar) {
    try {
      const resp = await fetch(`data/audio/${bible.id}/${file}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      sidecar = await resp.json();
    } catch (e) {
      showError(`Audio timings failed to load: ${e.message}`);
      return;
    }
    sidecarCache.set(file, sidecar);
  }
  const v = sidecar.verses.find(v => v.ref === ref);
  if (!v) return;
  audioChapter = sidecar;
  chapterMedian = medianDuration(sidecar.verses.flatMap(x => x.words || []));
  renderTable();  // re-render so this chapter's verses show clickable word spans
  el.audioPanel.hidden = false;
  const player = el.audioPlayer;
  const src = audioIndex.audio_base + '/' + sidecar.audio.split('/').pop();
  const seek = () => { player.currentTime = v.start; player.play(); };
  if (player.getAttribute('src') !== src) {
    player.setAttribute('src', src);
    player.addEventListener('loadedmetadata', seek, { once: true });
    player.load();
  } else {
    seek();
  }
}

// Render a verse cell: when the loaded chapter has word timings for this ref,
// draw each canonical word as a clickable span (click = seek, double-click =
// loop that word); otherwise plain text. Word spans are what make the karaoke
// highlight and click-to-seek possible.
function renderVerseCell(td, ref, text) {
  td.textContent = '';
  const v = audioChapter && audioChapter.verses.find(x => x.ref === ref);
  if (!v || !v.words) { td.textContent = text; return; }
  v.words.forEach((w, k) => {
    const span = document.createElement('span');
    span.className = 'w';
    span.textContent = w.display;
    span.dataset.start = w.start;
    span.dataset.end = w.end;
    // Emphasis heatmap (P10.7): background intensity ~ word duration vs the
    // chapter median; CAP x median = full intensity, clamped to [0, 1].
    if (heatmapOn && chapterMedian > 0) {
      const a = Math.min(1, Math.max(0, (w.end - w.start) / (HEATMAP_CAP * chapterMedian)));
      span.style.backgroundColor = `rgba(255,120,0,${a.toFixed(3)})`;
    }
    span.addEventListener('click', () => {
      el.audioPlayer.currentTime = w.start;
      el.audioPlayer.play();
    });
    span.addEventListener('dblclick', (e) => {
      e.preventDefault();
      loopWord = (loopWord && loopWord.start === w.start) ? null : { start: w.start, end: w.end };
      el.audioPlayer.currentTime = w.start;
      el.audioPlayer.play();
    });
    td.append(span, document.createTextNode(' '));
    // Pause marker (P10.7): a divider where the gap to the next word exceeds
    // the breathing-point threshold (candidate atnach / sof-pasuq).
    if (pausesOn && k + 1 < v.words.length &&
        v.words[k + 1].start - w.end > PAUSE_THRESHOLD) {
      const mark = document.createElement('span');
      mark.className = 'pause';
      mark.setAttribute('aria-hidden', 'true');
      td.append(mark, document.createTextNode(' '));
    }
  });
}

// Median of positive word durations across [{start, end}] (for the heatmap).
function medianDuration(words) {
  const durs = words.map(w => w.end - w.start).filter(d => d > 0).sort((a, b) => a - b);
  if (!durs.length) return 0;
  const mid = Math.floor(durs.length / 2);
  return durs.length % 2 ? durs[mid] : (durs[mid - 1] + durs[mid]) / 2;
}

function highlightPlaying(ref) {
  for (const tr of el.verseBody.querySelectorAll('tr.playing'))
    if (tr.dataset.ref !== ref) tr.classList.remove('playing');
  if (ref) {
    const tr = el.verseBody.querySelector(`tr[data-ref="${CSS.escape(ref)}"]`);
    if (tr) tr.classList.add('playing');
  }
}

function highlightWord(verse, t) {
  if (curWordSpan) { curWordSpan.classList.remove('wplay'); curWordSpan = null; }
  if (!verse || !verse.words) return;
  const tr = el.verseBody.querySelector(`tr[data-ref="${CSS.escape(verse.ref)}"]`);
  if (!tr) return;
  const spans = tr.querySelectorAll('span.w');
  verse.words.forEach((w, k) => {
    if (t >= w.start && t < w.end && spans[k]) {
      spans[k].classList.add('wplay');
      curWordSpan = spans[k];
    }
  });
}

el.audioPlayer.addEventListener('timeupdate', () => {
  if (!audioChapter) return;
  const t = el.audioPlayer.currentTime;
  if (loopWord && t >= loopWord.end) { el.audioPlayer.currentTime = loopWord.start; return; }
  const cur = audioChapter.verses.find(v => t >= v.start && t < v.end);
  highlightPlaying(cur ? cur.ref : null);
  highlightWord(cur, t);
});

el.audioPlayer.addEventListener('error', () => {
  // Timings exist but the audio files don't (deployed site, or audio dir
  // moved): hide the feature instead of leaving a broken player.
  el.audioPanel.hidden = true;
  audioIndex = null;
  highlightPlaying(null);
  renderTable();
});

// ---------------------------------------------------------------- data loading

function langName(code) {
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) || code;
  } catch {
    return code;
  }
}

async function loadBible(id) {
  const entry = manifest.bibles.find(b => b.id === id) || manifest.bibles[0];
  el.loading.hidden = false;
  showError('');
  try {
    const resp = await fetch(entry.file);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    bible = await resp.json();
  } catch (e) {
    showError(`Failed to load ${entry.name}: ${e.message}`);
    el.loading.hidden = true;
    return;
  }
  el.loading.hidden = true;
  saveJSON('bible', bible.id);

  // Phase 13: initialize corpus ranks and learning state per bible
  ranks = corpusRanks(bible.tokens);
  learned = new Set(loadJSON('learned:' + bible.id, []));
  levelPos = loadJSON('level:' + bible.id, 50);
  if (el.level) el.level.value = levelPos;

  // Audio (P10.3): the manifest entry carries an "audio" index path only when
  // exported with --audio; a failed fetch (deployed site) hides the feature.
  audioIndex = null;
  audioChapter = null;
  loopWord = null;
  curWordSpan = null;
  chapterMedian = 0;
  sidecarCache.clear();
  el.audioPanel.hidden = true;
  el.audioPlayer.pause();
  el.audioPlayer.removeAttribute('src');
  if (entry.audio) {
    try {
      const resp = await fetch(entry.audio);
      if (resp.ok) audioIndex = await resp.json();
    } catch { /* no local audio — feature stays hidden */ }
  }

  // Language plumbing: stemmer + stopwords must be in place before scoring.
  currentStemmer = await stemmerFor(bible.lang);
  currentStops = stopsFor(bible.lang);

  // Precompute mark-stripped search text once per bible load.
  searchText = bible.refs.map((r, i) => stripMarks(r + ' ' + bible.verses[i]));
  reads = new Set(loadJSON('reads:' + bible.id, []));
  el.vocab.value = loadJSON('vocab:' + bible.lang, '');
  rescore();
}

// ---------------------------------------------------------------- events

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

el.bibleSelect.addEventListener('change', () => loadBible(el.bibleSelect.value));

el.vocab.addEventListener('input', debounce(() => {
  if (!bible) return;
  saveJSON('vocab:' + bible.lang, el.vocab.value);
  rescore();
}, 300));

el.search.addEventListener('input', debounce(() => { page = 0; refresh(); }, 200));
el.unreadOnly.addEventListener('change', () => { page = 0; refresh(); });

// Phase 13: level slider (no debounce needed — difficulty doesn't change, just filtering)
if (el.level) {
  el.level.addEventListener('input', debounce(() => {
    if (!bible) return;
    levelPos = Number(el.level.value);
    saveJSON('level:' + bible.id, levelPos);
    refresh();
    renderLearnNext();
    computePassages();
    renderReader();
  }, 150));
}

// Audio analytics toggles (P10.7): persist and re-render so word spans pick up
// the heatmap background / pause markers. No-op when no chapter has word data.
el.heatmapToggle.checked = heatmapOn;
el.heatmapToggle.addEventListener('change', () => {
  heatmapOn = el.heatmapToggle.checked;
  saveJSON('audio:heatmap', heatmapOn);
  if (bible) renderTable();
});
el.pausesToggle.checked = pausesOn;
el.pausesToggle.addEventListener('change', () => {
  pausesOn = el.pausesToggle.checked;
  saveJSON('audio:pauses', pausesOn);
  if (bible) renderTable();
});

el.prevPage.addEventListener('click', () => { page--; renderTable(); });
el.nextPage.addEventListener('click', () => { page++; renderTable(); });

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

el.exportData.addEventListener('click', () => {
  const data = {};
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key === 'bible' || key.startsWith('vocab:') || key.startsWith('reads:') ||
          key.startsWith('learned:') || key.startsWith('level:'))
        data[key] = localStorage.getItem(key);
    }
  } catch { /* ignore */ }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'bible-reader-data.json';
  a.click();
  URL.revokeObjectURL(a.href);
});

el.importData.addEventListener('click', () => el.importFile.click());
el.importFile.addEventListener('change', async () => {
  const file = el.importFile.files[0];
  el.importFile.value = '';
  if (!file) return;
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch {
    showError('Import failed: not a valid JSON file.');
    return;
  }
  try {
    for (const [key, value] of Object.entries(data)) {
      if (key === 'bible' || key.startsWith('vocab:') || key.startsWith('reads:') ||
          key.startsWith('learned:') || key.startsWith('level:'))
        localStorage.setItem(key, String(value));
    }
  } catch { /* ignore */ }
  showError('');
  const id = loadJSON('bible', manifest.bibles[0].id);
  el.bibleSelect.value = id;
  loadBible(id);
});

// ---------------------------------------------------------------- init

async function init() {
  ({ corpusRanks, verseDifficulty, nextWords } = await import('./rank.js'));
  if (!el.bibleSelect) {
    showError('Bible selector element not found');
    return;
  }
  try {
    const resp = await fetch('data/manifest.json');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    manifest = await resp.json();
  } catch (e) {
    showError(`Failed to load manifest: ${e.message}`);
    return;
  }
  rtlLangs = new Set(manifest.rtl || ['he']);
  if (!manifest.bibles || manifest.bibles.length === 0) {
    showError('No bibles found in manifest');
    return;
  }
  for (const b of manifest.bibles) {
    const opt = document.createElement('option');
    opt.value = b.id;
    opt.textContent = `${b.name} (${b.verses.toLocaleString()} verses)`;
    el.bibleSelect.appendChild(opt);
  }
  const saved = loadJSON('bible', manifest.bibles[0].id);
  const id = manifest.bibles.some(b => b.id === saved) ? saved : manifest.bibles[0].id;
  el.bibleSelect.value = id;
  route();
  await loadBible(id);
}

init();
