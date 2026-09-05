"use strict";

const BOOK_ORDER = ["Gen","Exod","Lev","Num","Deut","Josh","Judg","Ruth","1Sam","2Sam",
  "1Kgs","2Kgs","1Chr","2Chr","Ezra","Neh","Esth","Job","Ps","Prov","Eccl","Song",
  "Isa","Jer","Lam","Ezek","Dan","Hos","Joel","Amos","Obad","Jonah","Mic","Nah",
  "Hab","Zeph","Hag","Zech","Mal"];

const PAUSE_GAP = 0.8;       // seconds; SPEC §6 pause markers
const HIGHLIGHT_SLACK = 0.15; // keep a word lit briefly through micro-gaps

const $ = (sel) => document.querySelector(sel);
const audio = $("#audio");
const reader = $("#reader");

let manifest = null;
let chapters = {};        // chap_id -> manifest entry (aligned/validated only)
let words = [];           // current chapter's word records
let spans = [];           // parallel array of <span> elements
let currentIdx = -1;
let loopIdx = -1;
let medianDur = 0.3;
let rafId = null;

// ---------- boot ----------

async function init() {
  manifest = await (await fetch("manifest.json", { cache: "no-store" })).json();
  for (const [cid, entry] of Object.entries(manifest.chapters || {})) {
    if (["aligned", "validated"].includes(entry.status)) chapters[cid] = entry;
  }
  populateBooks();

  const saved = JSON.parse(localStorage.getItem("mikra-state") || "{}");
  const first = Object.keys(chapters).sort()[0];
  const start = chapters[saved.chapter] ? saved.chapter : first;
  if (!start) {
    reader.textContent = "No aligned chapters yet. Run: python -m mikra align";
    return;
  }
  await loadChapter(start, saved.chapter === start ? saved.time : 0);
}

function populateBooks() {
  const present = new Set(Object.values(chapters).map((e) => e.book));
  const sel = $("#bookSelect");
  sel.innerHTML = "";
  for (const b of BOOK_ORDER.filter((b) => present.has(b))) {
    sel.appendChild(new Option(b, b));
  }
  sel.onchange = () => { populateChapters(sel.value); $("#chapterSelect").onchange(); };
  $("#chapterSelect").onchange = () =>
    loadChapter($("#chapterSelect").value, 0);
}

function populateChapters(book) {
  const sel = $("#chapterSelect");
  sel.innerHTML = "";
  for (const cid of Object.keys(chapters).filter((c) => chapters[c].book === book).sort()) {
    sel.appendChild(new Option(chapters[cid].chapter, cid));
  }
}

// ---------- chapter loading & rendering ----------

async function loadChapter(chapId, resumeTime = 0) {
  const entry = chapters[chapId];
  if (!entry) return;

  $("#bookSelect").value = entry.book;
  populateChapters(entry.book);
  $("#chapterSelect").value = chapId;

  const data = await (await fetch(entry.alignment || `alignments/${chapId}.json`,
                                  { cache: "no-store" })).json();
  words = data.words;
  clearLoop();
  currentIdx = -1;

  const durs = words.map((w) => w.end - w.start).sort((a, b) => a - b);
  medianDur = durs[Math.floor(durs.length / 2)] || 0.3;

  renderWords();
  applyHeatmap();

  audio.src = entry.audio || data.audio;
  audio.currentTime = resumeTime || 0;
  localStorage.setItem("mikra-state", JSON.stringify({ chapter: chapId, time: resumeTime }));
}

function renderWords() {
  reader.innerHTML = "";
  spans = [];
  let lastVerse = 0;
  const frag = document.createDocumentFragment();

  words.forEach((w, i) => {
    if (w.verse !== lastVerse) {
      const v = document.createElement("span");
      v.className = "verse-num";
      v.textContent = w.verse;
      frag.appendChild(v);
      lastVerse = w.verse;
    }
    const s = document.createElement("span");
    s.className = "word";
    s.textContent = w.display;
    s.title = `${w.start.toFixed(2)}–${w.end.toFixed(2)}s  (${(w.end - w.start).toFixed(2)}s)`;
    s.onclick = () => { audio.currentTime = w.start; audio.play(); };
    s.ondblclick = (e) => { e.preventDefault(); setLoop(i); };
    frag.appendChild(s);
    spans.push(s);

    const next = words[i + 1];
    if (next && next.start - w.end > PAUSE_GAP) {
      const p = document.createElement("span");
      p.className = "pause-marker";
      p.textContent = "‖";
      p.title = `${(next.start - w.end).toFixed(2)}s pause`;
      frag.appendChild(p);
    }
    frag.appendChild(document.createTextNode(" "));
  });
  reader.appendChild(frag);
}

// ---------- sync loop ----------

function wordAt(t) {
  // binary search: largest i with words[i].start <= t
  let lo = 0, hi = words.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].start <= t) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  if (ans >= 0 && t <= words[ans].end + HIGHLIGHT_SLACK) return ans;
  return -1;
}

function tick() {
  const t = audio.currentTime;

  if (loopIdx >= 0 && t > words[loopIdx].end + 0.05) {
    audio.currentTime = Math.max(0, words[loopIdx].start - 0.05);
  }

  const idx = loopIdx >= 0 ? loopIdx : wordAt(t);
  if (idx !== currentIdx) {
    if (currentIdx >= 0) spans[currentIdx].classList.remove("current");
    currentIdx = idx;
    if (idx >= 0) {
      spans[idx].classList.add("current");
      const w = words[idx];
      $("#wordInfo").textContent =
        `${w.display}  ·  v${w.verse}  ·  ${w.start.toFixed(2)}–${w.end.toFixed(2)}s  ·  ${(w.end - w.start).toFixed(2)}s`;
      if ($("#autoscrollToggle").checked) {
        spans[idx].scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
  }
  if (!audio.paused) rafId = requestAnimationFrame(tick);
}

audio.addEventListener("play", () => { cancelAnimationFrame(rafId); rafId = requestAnimationFrame(tick); });
audio.addEventListener("seeked", () => tick());
audio.addEventListener("pause", saveState);
window.addEventListener("beforeunload", saveState);

function saveState() {
  localStorage.setItem("mikra-state",
    JSON.stringify({ chapter: $("#chapterSelect").value, time: audio.currentTime }));
}

// ---------- loop mode ----------

function setLoop(i) {
  clearLoop();
  loopIdx = i;
  spans[i].classList.add("looping");
  $("#loopInfo").textContent = `looping: ${words[i].display} (Esc to stop)`;
  audio.currentTime = Math.max(0, words[i].start - 0.05);
  audio.play();
}

function clearLoop() {
  if (loopIdx >= 0 && spans[loopIdx]) spans[loopIdx].classList.remove("looping");
  loopIdx = -1;
  $("#loopInfo").textContent = "";
}

// ---------- analytics toggles ----------

function applyHeatmap() {
  const on = $("#heatmapToggle").checked;
  spans.forEach((s, i) => {
    if (!on) { s.style.background = ""; return; }
    const ratio = (words[i].end - words[i].start) / medianDur;
    const alpha = Math.min(0.55, Math.max(0, (ratio - 1) * 0.28));
    s.style.background = alpha > 0.02 ? `rgba(217, 164, 65, ${alpha})` : "";
  });
}

$("#heatmapToggle").onchange = applyHeatmap;
$("#pausesToggle").onchange = (e) =>
  document.body.classList.toggle("show-pauses", e.target.checked);

// ---------- keyboard ----------

document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
  switch (e.key) {
    case " ": e.preventDefault(); audio.paused ? audio.play() : audio.pause(); break;
    case "ArrowLeft": audio.currentTime = Math.max(0, audio.currentTime - 2); tick(); break;
    case "ArrowRight": audio.currentTime += 2; tick(); break;
    case "Escape": clearLoop(); break;
    case "-": setSpeed(audio.playbackRate - 0.1); break;
    case "=": case "+": setSpeed(audio.playbackRate + 0.1); break;
  }
});

function setSpeed(rate) {
  audio.playbackRate = Math.min(2.5, Math.max(0.4, Math.round(rate * 10) / 10));
  $("#speedLabel").textContent = audio.playbackRate.toFixed(2) + "×";
}

init().catch((err) => { reader.textContent = "Failed to load: " + err; });
