---
name: asr-forced-alignment
description: >-
  Align a known text to long-form audio — per-sentence or per-word timestamps —
  using cloud ASR plus anchor matching, no local GPU and no training. Use when
  building karaoke/read-along/click-to-seek features, syncing an audiobook or
  narration to its transcript, cutting audio at sentence boundaries, or
  evaluating forced aligners. Includes the anchor algorithm, confidence
  semantics, cost and resumability, a validation harness, and a measured
  negative result on TTS+DTW alignment (aeneas).
---

# Forced alignment via ASR anchors

You have audio and you already have the text. You need timestamps. This is the
cheap, CPU-only way to get them: transcribe with a cloud ASR, then match its
output against your canonical text at points where the match is unambiguous, and
interpolate everything in between.

Extracted from a pipeline that aligned 737 chapters (~60 hours) of cantillated
Biblical Hebrew for **$6.65**, producing 19,310 sentence-level and 269,879
word-level timings with zero non-monotonic or out-of-bounds values.

## The premise

**ASR is a timestamp source, never a source of words.** The output is timings
keyed by *your* refs. ASR errors degrade confidence; they can never corrupt
content. This single decision is what makes the method robust to a 30-40%
word error rate — and it is why this beats "just use the ASR transcript".

## Before writing any code: the edition gate

Transcribe **one** file locally and read the output against your text.

You are checking that the narration is the edition/version/translation you think
it is. The failure this prevents is catastrophic and silent: align a modern
translation's narration against a critical text and you get plausible-looking
timings that are wrong everywhere, with confidence scores that merely look
mediocre.

In the source project this gate passed — and returned three findings that shaped
the algorithm before it existed:

1. **Orthography drift.** The ASR emitted modern *plene* spelling against a
   *defective* canonical text. Exact-token overlap 54%; after collapsing the
   difference, 66%. Step 0 of the algorithm exists only because of this.
2. **Preamble.** Narration opens by announcing the chapter, before the first
   sentence. The aligner must tolerate leading audio that is in no verse.
3. **Dropped spans.** The ASR silently skipped an opening list of names. So
   interior gaps must be survivable, and boundary items adjacent to a dropped
   span must inherit low confidence rather than a confident wrong answer.

Also verify the *segmentation* matches: if your audio is split per chapter, the
chapter numbering must match your text's before you write anything.

## The algorithm

**Step 0 — normalization currency.** Find the cheapest destructive
normalization that collapses the ASR's orthography onto your text's, and
*measure the overlap lift before building anything else*. Here it was dropping
matres lectionis (`re.sub(r"[יו]", "", token)`) on top of the app's existing
tokenizer. Matching is then **exact on the normalized form** — no fuzzy
matching, no edit distance anywhere. All the fuzziness lives in the normalizer
plus the filters below.

**Step 1 — two parallel token streams.** Canonical side emits three aligned
lists: normalized token, index of the sentence it belongs to, and the *original
display word* it came from (so you keep full punctuation/diacritics for
rendering). ASR side emits `(normalized_token, start, end)`; one ASR word may
normalize to zero or several tokens, each inheriting the whole word's timing.
Drop tokens that normalize to empty, on both sides.

**Step 2 — anchors are tokens unique on *both* sides.** A hapax intersection:

```python
def uniques(seq):
    seen, dup = {}, set()
    for i, s in enumerate(seq):
        dup.add(s) if s in seen else seen.setdefault(s, i)
    return {s: i for s, i in seen.items() if s not in dup}

pairs = sorted((v_pos[s], a_pos[s]) for s in v_pos.keys() & a_pos.keys())
```

Appearing exactly once on each side makes the correspondence unambiguous with no
search. Note the happy consequence: rare proper names are the *worst* case for
ASR accuracy and the *best* case for anchoring.

**Step 3 — speech-rate plausibility filter.** Narration is continuous, so a
token at canonical position `vi/N` should be heard near `vi/N * duration`. Drop
pairs deviating by more than ~20% of total duration; they are coincidences.

**Step 4 — monotonicity by longest increasing subsequence.** `pairs` is sorted
by canonical index; run patience-sort LIS on the ASR index so any crossed pair is
discarded. O(n log n), and it leaves a correspondence strictly increasing on both
axes.

**Step 5 — synthetic boundary anchors.** If the first anchor is not token 0,
prepend `(0, 0.0)`; if the last is not the final token, append
`(N-1, duration)`. Without this, unanchored leading and trailing sentences
collapse to zero width — and ASR reliably garbles openings, which is exactly
where the anchor-free runs are.

**Step 6 — interpolate every token, then clamp.** Piecewise-linear interpolation
over token index, clamped at both ends, then a non-decreasing pass
(`xs[i] = max(xs[i], xs[i-1])`). The narrator reads in order, so a stray anchor
must never produce a backward jump. Gaps between anchors are filled at an assumed
constant speech rate — that is the whole model, and it is sufficient.

**Step 7 — derive boundaries from the same token grid.** Sentence start = its
first token's start; sentence end = the *next* sentence's start (continuous
narration, no silence model); the last ends at `duration`. Deriving word edges
and sentence edges from one array is what makes them agree exactly.

**Step 8 — confidence = fraction of an item's tokens actually heard inside its
own window.** Binary-search the ASR stream for the window, take the set of
skeletons heard there, and score each item's tokens against it.

**Degenerate path:** zero anchors → every item gets `start=0, end=duration,
confidence=0`. Nothing is silently dropped; the item is flagged by construction.

Word-level timings come free, because step 6 already interpolated *every* token.
Anchor words carry an observed ASR time (`conf 1.0`); interpolated words inherit
their sentence's confidence. So per-word `conf` means **"observation or
estimate"**, not "how accurate". Keep the `words` key optional so consumers
degrade to sentence-level cleanly.

## Confidence measures anchor density, not error

The most important non-obvious finding. Across 737 chapters:
`corr(conf, sentence_count) = +0.32`, `corr(conf, token_count) = +0.43`.

| items in chapter | chapters | mean conf | below 0.5 |
|---|---|---|---|
| 0–8 | 25 | 0.488 | 15 |
| 9–15 | 102 | 0.658 | 4 |
| 16–25 | 263 | 0.706 | 4 |
| 26–40 | 272 | 0.728 | 3 |
| 41+ | 75 | 0.746 | 0 |

Short chapters offer fewer unique tokens to anchor on, so a smaller fraction can
match. The initial hypothesis — "poetry aligns badly" — was wrong; it was a
length artifact. And every chapter had monotonic, in-bounds timings regardless of
score, i.e. **low confidence coexists with correct timings**.

Act on it as *sparse evidence*, not *detected error*: route low scores to
spot-listening, never to automatic reprocessing or a fallback aligner. Report it;
never silently accept it.

## Cost and operations

| provider | model | $/audio-hour | 60 h corpus |
|---|---|---|---|
| Groq | `whisper-large-v3` | 0.111 | **$6.65** |
| OpenAI | `whisper-1` | 0.36 | ~$21 |

- **Get to 16 kHz mono before paying for anything.** That is Whisper's native
  input.
- **Send the compressed file.** Opus at 40 kbps transcribes with no accuracy
  complaint and cuts upload time ~10×. Serve that same file: ~1 GB instead of
  ~8-11 GB of WAV.
- **Write to `.part` then `os.replace`.** An interrupt must never leave a
  truncated file that a resumable run then skips as "done".
- **Skip-if-exists at every stage.** Transcription costs money and is the only
  irreversible step; alignment is local, free, and re-runnable. Structure the
  pipeline so a bad alignment change costs nothing to fix.
- **A retry re-bills the whole file.** Whisper bills per audio-second submitted;
  there is no partial resume. Keep units small (1-12 min ≈ $0.002-0.02 each).
- **Run 3 files first.** Always have a `--limit`.
- **Cloudflare 403s urllib's default User-Agent.** Groq and OpenAI sit behind
  Cloudflare, which rejects `Python-urllib/3.x` with error 1010 — a bare 403 with
  no auth message, which reads exactly like a bad API key. Set any real UA
  string.
- **Accept both env-var spellings for the key** (`GROQ_API_KEY` *or* `GROQ`), or
  print the names you looked for in the error. Two names for one secret costs an
  hour every time.
- Cloud ASR returns word timings as a **top-level `words` list** with segments
  carrying none — unlike local Whisper, which nests them. Normalize the response
  shape at the boundary so downstream code has one format.

## Validation harness

**Build ground truth from your own output.** Items with `confidence >= 0.8` are
pinned to real matched tokens at real observed timestamps — they are
observations, not interpolations. That makes them a free labeled set for
evaluating any *other* aligner, with no human annotation.

**Two reference-free sanity checks** that catch a broken aligner outright:

1. **Overrun** — does any timestamp exceed the audio duration?
2. **Degenerate fragments** — count segments under 0.5 s. A cluster of
   near-zero-width segments is the fingerprint of an aligner that ran out of
   audio and stacked the tail.

Plus corpus-wide **monotonicity** and **in-bounds** assertions. The anchor
method cannot overrun by construction (interpolation clamps at the terminal
anchor, which is `duration`).

Unit tests worth writing, each pinning one invariant: perfect reading → every
item anchored at conf 1.0; adjacent boundaries contiguous (`a.end == b.start`);
garbled middle item → conf 0.0 but **nonzero width**; rate-outlier anchor
rejected; no anchors → flat output, conf 0.0; stray late word cannot reorder
starts; preamble tolerated; LIS drops duplicates and crossings; interpolation
clamps both ends; a corpus re-run is a no-op.

## Do not use TTS+DTW (aeneas). Measured.

The obvious-looking alternative — synthesize the known text with TTS, align the
synthetic reference to the recording with DTW — was tested properly and **fails
badly** on this material. Scored against the high-confidence ground truth above,
14 chapters / 100 items:

| metric | TTS+DTW vs anchors |
|---|---|
| median abs. error in start time | **51.2 s** |
| p90 | **166.0 s** |
| off by > 2 s | 99% |
| off by > 5 s | 98% |

It failed both reference-free sanity checks on *every* chapter: overran duration
by 0.4-1.9 s, emitted 1-14 sub-0.5 s fragments. The failure mode is **DTW drift
that saturates** — in one chapter it starts ~12 s early, crosses over midway,
drifts to +90 s, then assigns the last five items all to the final timestamp,
having run out of audio. Error scales with length (12 s → 37 s → 89 s across
chapters), the signature of progressive slip rather than noise.

Root cause: the synthetic voice was in a different register than the recording
(modern Israeli Hebrew TTS vs cantillated liturgical Biblical Hebrew), and the
tooling had no matching phonetic mapping at all. Install cost is also real —
aeneas 1.7.3 is unmaintained since 2017, imports `numpy.distutils`, and pins you
to numpy<2, Python≤3.11, setuptools<60.

**The generalizable lesson:** TTS+DTW assumes the synthetic voice shares the
recording's register, dialect and pronunciation tradition. When it doesn't, DTW
degrades *monotonically and silently* — it always returns a full, well-formed,
plausible answer. ASR+anchoring degrades *locally and visibly* — a bad region
simply has no anchors and reports low confidence. **Prefer the method whose
failures are legible.**

If timings genuinely need improving, the remaining option is a GPU forced
aligner (MMS / `ctc-forced-aligner`) on rented compute — not TTS+DTW.

## Consuming the output

Sidecar JSON per audio file: `{audio, duration, items: [{ref, start, end,
confidence, words?}]}`. Make the manifest optional at every level — absent
manifest, missing sidecar, or missing audio file should all degrade to "no audio,
unchanged UI".

For click-to-seek and highlight in a browser: resolve the current item by
`t >= start && t < end` (half-open, which is why ends chain to the next start),
listen for `timeupdate` in the **capture phase** (media events don't bubble), and
re-render word spans only when the *item* changes, so click handlers survive.

If the audio is licensed and the text isn't, export **timing data only** — and
enforce it structurally: parse and re-emit named fields rather than copying
files, so a new upstream field cannot leak bytes you have no right to publish.
