// Static Bible reader — scores verses by comprehension against the user's vocab.
// Vanilla JS ES module; per-language Snowball stemmers are dynamically imported
// from site/vendor/ as listed in the manifest's `stemmers` map.

// ---------------------------------------------------------------- state

const PAGE_SIZE = 20;
const PASSAGE_RATE = 0.95;
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

function score() {
  const vocab = tokenizeVocab(el.vocab.value, bible.lang);
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
  order = Array.from({ length: n }, (_, i) => i).sort((a, b) => rates[b] - rates[a]);
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

// ---------------------------------------------------------------- rendering

const el = {};
for (const id of ['bible-select', 'loading', 'vocab', 'vocab-label', 'rate-min',
  'rate-max', 'max-unknown', 'search', 'unread-only', 'find-passage',
  'export-data', 'import-data', 'import-file', 'progress', 'passage-panel',
  'verse-body', 'prev-page', 'next-page', 'page-info', 'error',
  'audio-panel', 'audio-player', 'heatmap-toggle', 'pauses-toggle']) {
  el[id.replace(/-(\w)/g, (_, c) => c.toUpperCase())] = document.getElementById(id);
}

function showError(msg) {
  el.error.textContent = msg;
  el.error.hidden = !msg;
}

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

function renderProgress() {
  let sweet = 0, read = 0;
  for (let i = 0; i < rates.length; i++) {
    if (rates[i] >= PASSAGE_RATE) {
      sweet++;
      if (reads.has(bible.refs[i])) read++;
    }
  }
  el.progress.textContent = `${read} of ${sweet} verses at ≥95% read`;
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
  renderProgress();
  renderTable();
}

function rescore() {
  if (!bible) return;
  score();
  page = 0;
  refresh();
}

function renderPassage() {
  const panel = el.passagePanel;
  panel.textContent = '';
  panel.hidden = false;
  const span = longestSpan(known, total, PASSAGE_RATE);
  if (!span) {
    panel.textContent = 'No passage at ≥95% found.';
    return;
  }
  const [i, j] = span;
  let k = 0, t = 0;
  for (let v = i; v < j; v++) { k += known[v]; t += total[v]; }
  const rate = t ? (100 * k / t).toFixed(1) : '0.0';
  const head = document.createElement('p');
  head.className = 'passage-head';
  head.textContent = `Longest passage: ${bible.refs[i]} – ${bible.refs[j - 1]} (${j - i} verses, ${rate}% comprehension)`;
  panel.appendChild(head);
  const rtl = rtlLangs.has(bible.lang);
  for (let v = i; v < j; v++) {
    const line = document.createElement('p');
    line.className = 'passage-line';
    line.textContent = `${bible.refs[v]}  ${bible.verses[v]}`;
    if (rtl) { line.dir = 'rtl'; line.classList.add('rtl'); }
    panel.appendChild(line);
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
  el.vocabLabel.textContent =
    `Your vocabulary (${langName(bible.lang)} words, whitespace-separated)`;
  el.vocab.value = loadJSON('vocab:' + bible.lang, '');
  el.passagePanel.hidden = true;
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

for (const input of [el.rateMin, el.rateMax, el.maxUnknown, el.search]) {
  input.addEventListener('input', debounce(() => { page = 0; refresh(); }, 200));
}
el.unreadOnly.addEventListener('change', () => { page = 0; refresh(); });

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

el.findPassage.addEventListener('click', () => { if (bible) renderPassage(); });

el.exportData.addEventListener('click', () => {
  const data = {};
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key === 'bible' || key.startsWith('vocab:') || key.startsWith('reads:'))
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
      if (key === 'bible' || key.startsWith('vocab:') || key.startsWith('reads:'))
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
  try {
    const resp = await fetch('data/manifest.json');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    manifest = await resp.json();
  } catch (e) {
    showError(`Failed to load manifest: ${e.message}`);
    return;
  }
  rtlLangs = new Set(manifest.rtl || ['he']);
  for (const b of manifest.bibles) {
    const opt = document.createElement('option');
    opt.value = b.id;
    opt.textContent = `${b.name} (${b.verses.toLocaleString()} verses)`;
    el.bibleSelect.appendChild(opt);
  }
  const saved = loadJSON('bible', manifest.bibles[0].id);
  const id = manifest.bibles.some(b => b.id === saved) ? saved : manifest.bibles[0].id;
  el.bibleSelect.value = id;
  await loadBible(id);
}

init();
