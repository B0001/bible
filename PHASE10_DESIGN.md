# Phase 10 — Audio Bible: verse-aligned Hebrew OT audio ("audiobible")

Status: draft · Last updated: 2026-07-02

## 1. Goal

Give every Hebrew OT verse in the reader a **playable audio segment**: tap a
verse to hear it, follow along karaoke-style while a chapter plays, and (stretch)
review vocabulary by ear. Concretely: for each chapter audio file, produce
per-verse `start`/`end` timestamps keyed by the same refs the pipeline already
uses (`Gen 1:1`, `1Chr 1:1`, …), and wire playback into the Dash app and the
static reader.

This absorbs and retires the earlier "audiobible" exploration (Pythonista
scripts scraping BibleGateway audio — 403-blocked and against ToS). The audio
source is now a legitimately obtained corpus we already possess.

## 2. What we have (inventory)

_Inventoried 2026-07-02 via the Google Drive connector._

- **A Google Drive folder** (`drive.google.com/drive/folders/1v4ZrGMyYl6IrWx1Mtws4FxN_RwpyImVE`)
  of Hebrew OT chapter audio derived from a Faith Comes By Hearing download
  (`HBRHMTN1DA.zip`), owned by the user, bulk-uploaded 2025-12-23.
- **Format** (verified on `1_Chronicles_01.wav`): WAV, **mono, 16 kHz, 16-bit
  PCM** — exactly Whisper's native input, i.e. already preprocessed for ASR.
  ~2–22 MB per chapter (1–12 min).
- **Naming:** `<Book_Name>_<NN>.wav`, underscores, zero-padded chapters
  (`Psalms_08.wav`, `2_Chronicles_36.wav`).
- **Chapter numbering is Hebrew/Masoretic**: `Joel_04.wav` exists (Joel has 4
  chapters only in Hebrew numbering; English has 3) and Malachi tops out at 3
  (English has 4). This matches WLC's (morphhb) numbering exactly — refs map
  1:1 with no chapter-offset table needed.
- **The corpus is partial.** Spot checks: Torah, Former/Latter Prophets, and
  Chronicles look complete (`Genesis_50`, `Isaiah_66`, `Jeremiah_52`,
  `Ezekiel_48`, `2_Chronicles_36`, `Numbers_01–36` all present). Missing:
  **Proverbs 17–31**, most of **Psalms** (gappy 1–93: e.g. 7, 9–10, 17–19, 22,
  25, 27 absent; nothing above 93), and all of **Ruth, Song of Songs,
  Zechariah, Zephaniah** (everything sorting after `Psalms_9x` in the upload).
  Estimated ~790–800 of 929 chapters present. The gaps look like an
  interrupted conversion/upload job, so P10.0 must reconcile against the
  original `HBRHMTN1DA.zip` and regenerate/re-upload the missing chapters.
- **Edition gate: PASSED (Masoretic).** Ran 2026-07-02 with MLX Whisper
  large-v3-turbo locally on the M-series GPU (no API key needed for one-off
  checks; `mlx-whisper` + stdlib `wave` since the corpus is already 16 kHz
  mono s16le — no ffmpeg). On `1_Chronicles_01.wav`, prose verses match WLC
  word-for-word (v10 "הוא החל להיות גבור בארץ", v12 "אשר יצאו משם פלשתים") —
  distinctively Biblical constructions a modern translation would rephrase.
  Word-level alignment against WLC is viable. Findings that shape D2:
  - **Orthography drift:** Whisper emits modern plene spelling (גיבור) vs
    WLC's defective spelling (גבור). Exact-token overlap was 54%; dropping
    matres lectionis (י/ו) from both sides ("skeleton" normalization) raised
    it to 66% *on a genealogy chapter*, the worst case for ASR (proper
    names). Anchor matching must compare skeletons, not raw consonant runs.
  - **Chapter announcements:** narration opens with e.g. "ספר דברי הימים א
    פרק א" before verse 1 — alignment must allow a preamble offset.
  - **Dropped spans:** Whisper silently skipped the opening name list
    (vv. 1–6). Anchor-then-interpolate handles interior gaps, but boundary
    verses adjacent to dropped spans must inherit low confidence.
- Access note: per-file download through the Drive connector is
  base64-encoded and impractical at 8+ GB; bulk download happens via the
  Drive web UI (folder → Download as zip) or `rclone`/Drive for Desktop.

### Licensing constraint (hard)

FCBH audio is free for personal use but **not freely redistributable**. Audio
files and anything derived that embeds them stay local / private storage. The
public static site (Phase 9, GitHub Pages) must never ship the audio; the
static reader gets an *optional local audio mode* instead (§6, P10.3).
`data/audio/` is gitignored like the rest of `data/`.

## 3. Constraints

- **No local GPU.** The Mac (Apple Silicon) can run whisper.cpp, but at
  roughly realtime that's 60–100 hours of compute for the corpus — not
  practical as the primary path. Heavy inference goes to a **cloud API**;
  the local machine does orchestration, matching, and verification.
- The repo's existing Hebrew normalization (`tokenize(text, "he")`: strip
  niqqud/cantillation U+0591–U+05C7, extract consonant runs) is the comparison
  currency between ASR output and verse text. Reuse it; do not invent another.

## 4. Design decisions

### D1. This is forced alignment, not transcription

We already have the target text. ASR is only a means to get **timestamps**,
never the source of truth for words. The product is verse-level timings;
word-level timings are stored when the method yields them, but no feature may
require them.

### D2. Alignment method: cloud ASR + anchor matching (primary)

1. Send each chapter WAV to a hosted Whisper endpoint with word-level
   timestamps, `language=he`.
   - **Groq** `whisper-large-v3`: ≈ $0.111/audio-hour → **$7–11 for the whole
     OT**. Recommended first choice.
   - **OpenAI** `whisper-1` (`verbose_json`, `timestamp_granularities=word`):
     $0.006/min → $22–36 total. Fallback / cross-check.
2. Normalize both sides with `tokenize(_, "he")` (ASR output usually has no
   niqqud; WLC does — stripping makes them comparable), then drop matres
   lectionis (י/ו) to bridge Whisper's modern plene spelling and WLC's
   defective spelling (verified necessary by the edition gate, §2).
3. **Anchor matching:** find high-confidence unique n-gram matches between the
   ASR word stream and the chapter's concatenated verse tokens, then
   interpolate verse boundaries between anchors (standard
   anchor-then-interpolate long-audio alignment). Each verse gets
   `start`, `end`, and a `confidence` = fraction of its tokens matched.
4. Verses under a confidence floor (say 0.5) are flagged in a review report,
   not silently accepted.

Genealogy-heavy chapters (like the sample, 1 Chr 1) are the worst case for ASR
(proper names) but the best case for anchors (rare tokens) — good early test.

### D3. Fallback: CPU forced alignment, fully local

If Whisper's Biblical-Hebrew quality makes anchor matching too sparse:
**aeneas** (TTS + DTW, espeak-ng has Hebrew) computes verse-level boundaries
directly from our verse list, CPU-only, faster than realtime, $0. Coarser but
robust, and it needs exactly the granularity we product-require (verse level).
A GPU forced aligner on rented compute (e.g. MMS/ctc-forced-aligner on Modal)
is the word-level escape hatch; not planned unless D2 and aeneas both fail.

### D4. Serve compressed audio, keep WAV as archive

12 MB/chapter WAV is fine for ASR input but silly to serve. Transcode once with
ffmpeg to mono Opus (`.webm`/`.m4a`) at 32–48 kbps → ~1.5–2.5 MB/chapter,
~2 GB total. WAVs stay in `data/audio/raw/` (or remain only in Drive once
verified); the app serves `data/audio/serve/`.

### D5. One sidecar file per chapter, keyed by existing refs

Alignment output is decoupled from the graded CSVs (no schema change to the
scoring pipeline). Per chapter:

```json
{
  "bible_id": "wlc",
  "book": "1Chr",
  "chapter": 1,
  "audio": "data/audio/serve/1Chr_001.opus",
  "duration": 377.0,
  "verses": [
    {"ref": "1Chr 1:1", "start": 0.42, "end": 3.91, "confidence": 0.97}
  ]
}
```

Written to `out/audio/wlc/<book>_<chapter>.json` plus a single
`out/audio/wlc_manifest.json` index. `bibles.toml` entries gain one optional
key: `audio_manifest = "out/audio/wlc_manifest.json"` — absent means no audio,
everything degrades gracefully (same pattern as missing graded CSVs).

### D6. Canonical book-ID mapping lives in one table

Drive names (`1_Chronicles`), WLC refs (`1Chr`), and full display names
(`1 Chronicles`) differ. One dict in the ingest script,
`BOOK_IDS = {"1_Chronicles": "1Chr", ...}` (39 entries), is the only place the
mapping exists. Chapter counts per book ship in the same table and double as
the completeness check.

## 5. Architecture

```
Drive folder (WAV, chapter-level)
      │  P10.0 ingest: download → rename to <osis>_<NNN>.wav → verify counts
      ▼
data/audio/raw/*.wav ──ffmpeg──► data/audio/serve/*.opus     (D4)
      │
      │  P10.1 align: cloud ASR (Groq/OpenAI) → anchor match vs data/wlc.txt
      ▼
out/audio/wlc/<book>_<chapter>.json + wlc_manifest.json      (D5)
      │
      ├──► dash_app.py: <audio> player, click-verse-to-seek, live highlight (P10.2)
      └──► site/: optional local audio mode, never deployed with audio (P10.3)
```

## 6. Work items

### P10.0 — Ingest, inventory, and the edition gate

`scripts/ingest_audio.py`:
- Input: a local copy of the Drive folder (download the folder/zip manually or
  via the claude.ai Google Drive connector / `rclone`; the folder is not
  link-public, so no anonymous scripted fetch).
- Inventory the actual file list; report which books/testaments are present
  and any gaps against the 929-chapter OT table (D6).
- Rename to `data/audio/raw/<osis>_<NNN>.wav`; transcode to
  `data/audio/serve/<osis>_<NNN>.opus` (skip-if-exists, resumable).
- ~~**Edition check (gate)**~~ ✅ done 2026-07-02 — narration is Masoretic;
  see §2 for the numbers and the three alignment-relevant findings.
- **Gap reconciliation:** diff the Drive inventory against the original
  `HBRHMTN1DA.zip` (and the 929-chapter table); regenerate/re-upload the
  ~130 missing chapters (Proverbs 17–31, most Psalms, Ruth, Song, Zechariah,
  Zephaniah) or accept a partial corpus and let missing chapters degrade to
  no-audio.

_Done when:_ inventory report printed, files renamed/transcoded, gap list
reconciled or accepted.

### P10.1 — Alignment pipeline

`scripts/align_audio.py`:
- Per chapter: ASR (D2) → anchor match → sidecar JSON (D5). Idempotent and
  resumable (skip chapters whose JSON exists — same pattern as the old
  downloader's resume logic). API key via env var, provider selectable
  (`--provider groq|openai`), `--limit N` for cheap trial runs.
- Emits a corpus-wide confidence report (per-book mean confidence, list of
  flagged verses) and a running cost estimate.
- Unit tests for the pure parts (anchor matching on synthetic token streams,
  boundary interpolation, low-confidence flagging) — no network in tests,
  fixtures with canned ASR JSON.

_Done when:_ full OT aligned for < $30, ≥95% of verses above the confidence
floor, spot-check of 10 random verses sounds right.

### P10.2 — Dash app playback

- `dcc.Audio` doesn't exist; use `html.Audio(src=...)` served from a small
  Flask route (`/audio/<file>`) registered on `app.server` (same pattern as
  `/health`), reading from `data/audio/serve/`.
- Selecting a verse row scrolls/seeks audio to `start` (clientside callback —
  seeking must not round-trip to the server).
- While playing, highlight the current verse row from the timings (clientside
  `timeupdate` listener writing to a dcc.Store).
- Only appears for Bibles whose `bibles.toml` entry has an `audio_manifest`
  and whose files exist; otherwise UI is unchanged.

_Done when:_ click 1 Chr 1:1 in the WLC table → hear it; playing a chapter
highlights verses as they're read; NASB/Greek UIs unaffected.

### P10.3 — Static reader local audio mode

- `export_static.py` gains `--audio`: copies sidecar JSONs (not audio) into
  `site/data/audio/` and records, per Bible, a *relative* audio base path.
- The static reader, when opened locally (file:// or localhost) with the
  audio directory present, shows the player; on the deployed Pages site the
  fetch for the manifest 404s and the feature hides itself. Deployment
  workflow explicitly excludes `data/audio/` (licensing, §2).

_Done when:_ local static site plays verse audio; deployed site has zero audio
bytes and no broken UI.

### P10.4 — Listening review (stretch)

"Listen mode": play a verse's segment *before* showing its text, user
self-grades recognition, result feeds the existing `record_review()` /
half-life model. Turns the Phase 5 SRS stack into listening comprehension
practice. No new model — just a different prompt order in the UI.

### P10.5 — Word-level karaoke (done 2026-07-05)

Prompted by an alternate spec (`~/Downloads/files0.zip`, "Mikra Sync"), the
alignment now also emits **per-word** timings on demand: `align_audio.py
--words` interpolates a time for every canonical WLC word (not just the
verse-first token) through the same anchor correspondence and pairs each with
its fully-pointed display form. Whitespace words map 1:1 to tokens (verified on
174k words), maqqef compounds included. Anchor words carry an exact ASR time
(conf 1.0); interpolated words inherit their verse confidence. Corpus regen:
269,879 words, 0 non-monotonic, 0 out-of-bounds, 29.6% anchored. Sidecars gain
an optional `words: [{display, start, end, conf}]` per verse (verse-level
schema unchanged; absent `words` degrades to the verse-only reader). The static
reader renders each word as a clickable span: **click = seek**, **double-click
= loop that word** (drill mode), with a live word highlight while playing. This
supersedes design §8's "word-level as opportunistic only" for the OT corpus.

## 7. Costs and storage

| Item | Size / cost |
|---|---|
| Raw WAV corpus | ~8–11 GB (archive; can live only in Drive after transcode) |
| Served Opus corpus | ~2 GB local |
| Sidecar JSON | ~5 MB total |
| Cloud ASR, full OT | ~$8 (Groq) / ~$25 (OpenAI), one-time |
| Re-runs | resumable; only failed/flagged chapters re-billed |

## 8. Out of scope

- NT audio (the corpus at hand is OT; revisit if the Drive inventory says
  otherwise).
- Word-level karaoke as a *requirement* (verse-level is the product; word
  timings kept opportunistically, D1).
- Hosting audio publicly in any form (licensing, §2).
- Local Whisper inference on the Mac (no GPU; see §3).

## 9. Open questions

1. ~~**Edition**~~ — resolved: Masoretic (§2).
2. **Bulk download path** — the Drive connector works for inventory but not
   for bulk transfer (base64, 8+ GB); use the Drive web UI folder download or
   `rclone`. Ingest starts from a local directory either way.
3. **Missing ~130 chapters** — regenerate from the original zip, or ship
   partial? (P10.0 gap reconciliation.)
