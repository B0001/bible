// Phase 10 (P10.2): live verse highlight while chapter audio plays.
//
// The clientside seek callback in dash_app.py keeps window._bibleAudioVerses
// = the playing chapter's [{ref, start, end}] timings. Media events don't
// bubble, so listen in the capture phase; on each timeupdate resolve the
// current verse and push its ref into the `audio-now` store, which drives the
// DataTable row highlight via a clientside callback.
document.addEventListener(
  "timeupdate",
  function (ev) {
    const el = ev.target;
    if (!(el instanceof HTMLAudioElement) || el.id !== "audio-player") return;
    const verses = window._bibleAudioVerses;
    if (!verses || !window.dash_clientside || !window.dash_clientside.set_props) return;
    const t = el.currentTime;
    let ref = null;
    for (const v of verses) {
      if (t >= v.start && t < v.end) {
        ref = v.ref;
        break;
      }
    }
    if (window._bibleAudioNow !== ref) {
      window._bibleAudioNow = ref;
      window.dash_clientside.set_props("audio-now", { data: ref });
    }
  },
  true
);
