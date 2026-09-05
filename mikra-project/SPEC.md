# Spec: Mikra Sync — Hebrew Bible Karaoke Reader

**Version:** 0.2
**Platform:** Desktop only (macOS / Linux / Windows), Python 3.10+
**Root directory:** `~/Downloads/bible`
**Source audio:** Faith Comes By Hearing Hebrew narration set `HBRHMTN1DA.zip`
**Playback UI:** Local web app served by the tool itself, opened in the default browser

> v0.1 targeted Pythonista on iOS for playback. v0.2 drops iOS entirely: the
> pipeline *and* the player both run on the desktop. A browser-based player was
> chosen over tkinter/Qt because browsers give free, correct right-to-left
> Hebrew shaping, sub-frame audio seeking, and zero extra dependencies.

---

## 1. Purpose

Turn a directory of narrated Hebrew Bible MP3s into a word-synchronized,
clickable reading experience:

1. Audio plays; the currently spoken Hebrew word is highlighted in real time.
2. Clicking any word seeks the audio to that word's start time.
3. Double-clicking a word loops it (language-learning drill mode).
4. Word-duration and pause data are visualized (emphasis heatmap, pause markers).

## 2. Non-Goals (v1)

- No translation display or interlinear English.
- No streaming; all audio is local.
- No model training or fine-tuning.
- No mobile UI.

## 3. Architecture

One installable Python package, `mikra`, exposing a single CLI:

```
python -m mikra <command> [--root ~/Downloads/bible]
```

| Command    | Purpose                                                        |
|------------|----------------------------------------------------------------|
| `scaffold` | Create the canonical directory layout under the root           |
| `rename`   | Parse vendor filenames in `raw/`, copy normalized to `audio/`  |
| `align`    | Forced-align `text/` against `audio/` → `alignments/*.json`    |
| `validate` | Sanity-check every alignment; flag bad chapters                |
| `build`    | Copy the web player into the root, ready to serve              |
| `serve`    | Start a local HTTP server and open the player in a browser     |
| `status`   | Print a per-book pipeline progress table                       |

Every stage is idempotent and records its results in `manifest.json`, which is
the single source of truth for pipeline state.

## 4. Directory Layout (canonical)

```
~/Downloads/bible/
├── raw/                      # untouched HBRHMTN1DA.zip contents (never modified)
├── audio/                    # renamed MP3s: Gen_01.mp3 ... Mal_04.mp3 (Ps uses 3 digits: Ps_001)
├── text/                     # UTF-8 Hebrew source, one file per chapter, ONE VERSE PER LINE
│   └── Gen_01.txt ...
├── alignments/               # pipeline output, one JSON per chapter
├── manifest.json             # pipeline state: checksums, statuses, errors
├── index.html  app.js  style.css   # web player (written by `build`)
└── (the mikra package lives wherever you cloned it, not inside the root)
```

## 5. Pipeline Stages

### 5.1 `rename`

- Parses `B{book}___{chapter}_{BookName}_____{setID}.mp3` (tolerant of
  variable underscore runs and 1–3 digit chapter numbers).
- Book name → OSIS-style abbreviation via a normalized lookup table
  (`"SongofSongs"`, `"1Samuel"`, `"Psalms"` all resolve correctly).
- Copies (never moves) into `audio/`; `raw/` stays pristine.
- Records `sha256`, paths, and `status: "renamed"` in the manifest.
- Re-running skips files already present with matching checksums.
- Unparseable filenames are logged to `manifest.errors`, not silently dropped.

### 5.2 `align`

- Engine: **stable-ts** (`stable_whisper`) over Whisper `large-v3`
  (configurable via `--model`; `medium` is ~3× faster for drafts).
- Mode: **forced alignment against known text** (`model.align(...)`), language
  pinned to `he`. The canonical text wins; ASR only places timestamps.
- Text normalization before alignment:
  - Cantillation marks (U+0591–U+05AF) always stripped for matching.
  - Niqqud stripped by default; keep with `--keep-niqqud`.
  - Maqqef-joined compounds are ONE token.
  - Sof pasuq, paseq, and Latin punctuation removed.
  - A parallel token list maps every normalized token back to its fully
    pointed display form, so the UI always shows the original text.
- Output schema (`alignments/Gen_01.json`):

```json
{
  "book": "Gen", "chapter": 1, "audio": "audio/Gen_01.mp3",
  "duration": 312.4, "model": "large-v3", "keep_niqqud": false,
  "words": [
    {"i": 0, "display": "בְּרֵאשִׁית", "start": 0.00, "end": 0.82, "conf": 0.94, "verse": 1}
  ]
}
```

- Manifest status → `"aligned"` (or `"align_failed"` with the error recorded).

### 5.3 `validate`

Per chapter: JSON word count == text token count; timestamps monotonic
non-decreasing; no word longer than 5 s; mean confidence ≥ 0.7; last word ends
within the audio duration + 0.5 s. Failures → `status: "needs_review"` plus a
reason list. Nothing bad ships silently.

## 6. Web Player

Static, dependency-free HTML/CSS/JS served from the root by `mikra serve`
(so `audio/`, `alignments/`, and `manifest.json` are all same-origin fetches).

- **Rendering:** `dir="rtl"` flowed text, one `<span>` per word, verse numbers
  inline; the browser handles all Hebrew shaping and niqqud stacking.
- **Sync:** `requestAnimationFrame` loop while playing, binary search over word
  start times → O(log n) highlight updates with no drift (never `sleep`).
- **Interactions:** click = seek to word; double-click = loop word;
  Space = play/pause; ←/→ = ±2 s; `-`/`=` = playback speed; Esc = clear loop.
- **Analytics toggles:**
  - *Emphasis heatmap:* word background intensity ∝ duration / chapter median.
  - *Pause markers:* visible dividers where inter-word gap > 800 ms
    (candidate atnach / sof-pasuq breathing points).
- **State:** last book/chapter/position saved in `localStorage`; resume on load.

## 7. Error Handling & Edge Cases

- Missing text file → chapter skipped, `status: "missing_text"`.
- Token count mismatch after alignment → `"needs_review"` with counts recorded.
- Ketiv/Qere: the text edition's printed form is used as-is.
- Psalms chapter numbers are zero-padded to 3 digits so files sort correctly.
- The aligner import is lazy: every other command works without torch installed.

## 8. Milestones

1. **M1** — `scaffold` + `rename` over the full set; `status` shows 39 books.
2. **M2** — `align` Genesis 1; < 100 ms average deviation on 10 spot-checked words.
3. **M3** — Player: highlight + click-to-seek + resume for Genesis 1.
4. **M4** — Full Torah batch; `validate` pass rate ≥ 95 %.
5. **M5** — Analytics layer (heatmap, pause markers, word loop).

## 9. Resolved / Open Questions

- **Text edition (critical):** the `HMT` in `HBRHMTN1DA` indicates a *Modern
  Hebrew* translation recording. The files in `text/` must be the same edition
  the narrator reads, or alignment quality collapses. Verify with one chapter
  before batch-running. (Aligning WLC Biblical Hebrew against a Modern Hebrew
  narration will fail validation loudly, which is the intended behavior.)
- **Verse boundaries:** derived from the text files — one verse per line.
- **GPU:** `--device cuda` supported; CPU works but expect ~real-time or slower
  with `large-v3`. Use `--model medium` for iteration.
