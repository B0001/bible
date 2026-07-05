// Phase 10: live verse highlight (P10.2) + word-level karaoke (P10.6) while a
// chapter plays.
//
// The clientside seek callback in dash_app.py parks the playing chapter on
// window: `_bibleAudioVerses` = [{ref, start, end, words?}], `_bibleAudioRtl` =
// text direction. Media events don't bubble, so we listen in the capture
// phase; on each timeupdate we (a) push the current verse's ref into the
// `audio-now` store (drives the DataTable row highlight, P10.2) and (b) render
// that verse's words as clickable spans in the #karaoke panel and highlight the
// spoken word (P10.6). Dash DataTable cells can't hold interactive spans, so
// the panel is the home for word-level interaction. All seeking, looping, and
// highlighting is clientside — no server round-trip.

// Render the current verse's words into the #karaoke panel as clickable spans.
// Only called when the verse changes (not every timeupdate), so click/dblclick
// listeners survive; per-tick work is just the active-word highlight below.
function _renderKaraoke(verse) {
  const panel = document.getElementById("karaoke");
  if (!panel) return;
  panel.textContent = "";
  panel.dir = window._bibleAudioRtl ? "rtl" : "ltr";
  window._bibleKaraokeRef = verse ? verse.ref : null;
  window._bibleKaraokeSpans = null;
  window._bibleActiveWord = null;
  if (!verse || !verse.words) return;
  const player = document.getElementById("audio-player");
  const spans = [];
  for (const w of verse.words) {
    const span = document.createElement("span");
    span.className = "kw";
    span.textContent = w.display;
    span.style.cursor = "pointer";
    span.style.padding = "0 2px";
    span.style.borderRadius = "3px";
    // Click = seek to this word; double-click = loop it (drill mode).
    span.addEventListener("click", function () {
      if (player) { player.currentTime = w.start; player.play(); }
    });
    span.addEventListener("dblclick", function (e) {
      e.preventDefault();
      const lw = window._bibleLoopWord;
      window._bibleLoopWord =
        lw && lw.start === w.start ? null : { start: w.start, end: w.end };
      if (player) { player.currentTime = w.start; player.play(); }
    });
    panel.append(span, document.createTextNode(" "));
    spans.push(span);
  }
  window._bibleKaraokeSpans = spans;
}

// Highlight the word being spoken at time t; clears the previously active one.
function _highlightWord(verse, t) {
  const spans = window._bibleKaraokeSpans;
  if (!spans || !verse || !verse.words) return;
  let active = -1;
  for (let k = 0; k < verse.words.length; k++) {
    const w = verse.words[k];
    if (t >= w.start && t < w.end) { active = k; break; }
  }
  if (window._bibleActiveWord === active) return;
  const prev = window._bibleActiveWord;
  if (prev != null && prev >= 0 && spans[prev]) {
    spans[prev].style.background = "";
    spans[prev].style.fontWeight = "";
  }
  if (active >= 0 && spans[active]) {
    spans[active].style.background = "#fff3b0";
    spans[active].style.fontWeight = "bold";
  }
  window._bibleActiveWord = active;
}

document.addEventListener(
  "timeupdate",
  function (ev) {
    const el = ev.target;
    if (!(el instanceof HTMLAudioElement) || el.id !== "audio-player") return;
    const verses = window._bibleAudioVerses;
    if (!verses) return;
    const t = el.currentTime;

    // Word loop (P10.6): a double-clicked word repeats until double-clicked
    // again. Enforced here so it needs no server round-trip.
    const lw = window._bibleLoopWord;
    if (lw && t >= lw.end) { el.currentTime = lw.start; return; }

    // Resolve the current verse.
    let cur = null;
    for (const v of verses) {
      if (t >= v.start && t < v.end) { cur = v; break; }
    }
    const ref = cur ? cur.ref : null;

    // Verse-level row highlight via the audio-now store (P10.2).
    if (
      window._bibleAudioNow !== ref &&
      window.dash_clientside &&
      window.dash_clientside.set_props
    ) {
      window._bibleAudioNow = ref;
      window.dash_clientside.set_props("audio-now", { data: ref });
    }

    // Word-level karaoke (P10.6): (re)render on verse change, then highlight
    // the spoken word every tick.
    if (window._bibleKaraokeRef !== ref) _renderKaraoke(cur);
    _highlightWord(cur, t);
  },
  true
);
