#!/usr/bin/env python3
"""Transcribe chapter audio via a hosted Whisper API (PHASE10_DESIGN.md P10.1, D2).

The network half of the alignment pipeline: sends each chapter audio file
(``<osis>_<NNN>.opus``/``.wav``, as produced by scripts/ingest_audio.py) to an
OpenAI-compatible transcription endpoint with ``language=he``,
``response_format=verbose_json`` and word-level timestamps, and saves one
transcript JSON per chapter for scripts/align_audio.py to consume.

Both Groq and OpenAI return word timings as a *top-level* ``words`` list
(``timestamp_granularities[]=word``) — segments never carry words in their
APIs, unlike local Whisper. Saved transcripts are therefore normalized: when
``segments[].words`` is absent, the top-level words are wrapped as
``{"segments": [{"words": [...]}], ...}`` so align_audio.py's
``tr["segments"][…]["words"]`` read works unchanged.

Resumable: chapters whose transcript already exists are skipped. Failed
requests are retried once, then skipped (nonzero exit at the end). Uses only
the stdlib (hand-built multipart body) — no new dependencies.

Usage:
    GROQ_API_KEY=... python scripts/transcribe_audio.py --limit 3   # trial run
    GROQ_API_KEY=... python scripts/transcribe_audio.py             # full corpus
    OPENAI_API_KEY=... python scripts/transcribe_audio.py --provider openai
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3",
        "key_env": "GROQ_API_KEY",
        "usd_per_hour": 0.111,
    },
    "openai": {
        "url": "https://api.openai.com/v1/audio/transcriptions",
        "model": "whisper-1",
        "key_env": "OPENAI_API_KEY",
        "usd_per_hour": 0.36,  # $0.006/min
    },
}

# ingest_audio.py transcodes to mono Opus at 40 kbps -> ~5000 bytes/second.
OPUS_BYTES_PER_SEC = 5000

CHAPTER_RE = re.compile(r"^[A-Za-z0-9]+_\d{3}\.(opus|wav)$")


def multipart_body(fields, file_field, filename, file_bytes,
                   file_content_type="application/octet-stream", boundary=None):
    """Build a multipart/form-data body; returns (body_bytes, content_type).

    ``fields`` is a list of (name, value) pairs — a list, not a dict, because
    ``timestamp_granularities[]`` must repeat.
    """
    boundary = boundary or uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f"\r\n\r\n{value}\r\n".encode("utf-8")
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: {file_content_type}\r\n\r\n'.encode("utf-8")
        + file_bytes + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def request_fields(model):
    """Form fields for one transcription request (file added separately)."""
    return [
        ("model", model),
        ("language", "he"),
        ("response_format", "verbose_json"),
        # Both providers put word timings in a *top-level* "words" list;
        # "segment" is requested too so the saved JSON keeps segment text.
        ("timestamp_granularities[]", "word"),
        ("timestamp_granularities[]", "segment"),
    ]


def normalize_transcript(resp):
    """Ensure ``segments[].words`` exists (what align_audio.py main() reads).

    Groq/OpenAI verbose_json returns word timings as a top-level ``words``
    list and segments *without* words; wrap those words as a single synthetic
    segment. Responses that already carry per-segment words (e.g. local
    Whisper output) pass through untouched.
    """
    segments = resp.get("segments") or []
    if any(seg.get("words") for seg in segments):
        return resp
    words = resp.get("words")
    if not words:
        return resp  # nothing to normalize; align_audio will see zero words
    out = dict(resp)
    out["segments"] = [{"words": words}]
    return out


def estimate_seconds(path):
    """Estimated audio duration: exact from WAV headers, size-based for Opus."""
    if path.lower().endswith(".wav"):
        with wave.open(path, "rb") as w:
            return w.getnframes() / w.getframerate()
    return os.path.getsize(path) / OPUS_BYTES_PER_SEC


def estimate_cost(seconds, provider):
    return seconds / 3600 * PROVIDERS[provider]["usd_per_hour"]


def find_chapters(audio_dir):
    """Sorted [(stem, path)] of chapter audio files; .opus preferred over .wav."""
    by_stem = {}
    for name in sorted(os.listdir(audio_dir)):
        if not CHAPTER_RE.match(name):
            continue
        stem, ext = os.path.splitext(name)
        if stem not in by_stem or ext == ".opus":
            by_stem[stem] = os.path.join(audio_dir, name)
    return sorted(by_stem.items())


def transcribe(path, provider, api_key, timeout=600):
    """POST one audio file; returns the parsed (un-normalized) JSON response."""
    cfg = PROVIDERS[provider]
    with open(path, "rb") as f:
        audio = f.read()
    ctype = "audio/wav" if path.lower().endswith(".wav") else "audio/ogg"
    body, content_type = multipart_body(
        request_fields(cfg["model"]), "file", os.path.basename(path), audio, ctype
    )
    req = urllib.request.Request(
        cfg["url"],
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            # Groq/OpenAI sit behind Cloudflare, which 403s (error 1010) the
            # default "Python-urllib/x" agent; any real UA passes.
            "User-Agent": "bible-reader/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio-dir", default="data/audio/serve",
                    help="directory of <osis>_<NNN>.opus/.wav chapter files")
    ap.add_argument("--out-dir", default="out/audio/transcripts")
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="groq")
    ap.add_argument("--limit", type=int, default=None,
                    help="process at most N missing chapters (cheap trial runs)")
    ap.add_argument("--retry-sleep", type=float, default=5.0,
                    help="seconds to sleep before the single retry")
    args = ap.parse_args()

    key_env = PROVIDERS[args.provider]["key_env"]
    api_key = os.environ.get(key_env)
    if not api_key:
        sys.exit(f"{key_env} not set")

    chapters = find_chapters(args.audio_dir)
    if not chapters:
        sys.exit(f"no chapter audio files in {args.audio_dir}")
    os.makedirs(args.out_dir, exist_ok=True)

    todo, skipped = [], 0
    for stem, path in chapters:
        if os.path.exists(os.path.join(args.out_dir, f"{stem}.json")):
            skipped += 1
        else:
            todo.append((stem, path))
    if args.limit is not None:
        todo = todo[: args.limit]
    print(f"{len(chapters)} chapters found, {skipped} already transcribed, "
          f"{len(todo)} to send ({args.provider})")

    done_seconds, failed = 0.0, []
    for stem, path in todo:
        secs = estimate_seconds(path)
        resp = None
        for attempt in (1, 2):
            try:
                resp = transcribe(path, args.provider, api_key)
                break
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
                detail = e.read().decode("utf-8", "replace")[:200] if hasattr(e, "read") else e
                print(f"  {stem}: attempt {attempt} failed: {detail}")
                if attempt == 1:
                    time.sleep(args.retry_sleep)
        if resp is None:
            failed.append(stem)
            continue
        out_path = os.path.join(args.out_dir, f"{stem}.json")
        tmp = out_path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(normalize_transcript(resp), f, ensure_ascii=False, indent=1)
        os.replace(tmp, out_path)
        done_seconds += secs
        n_words = sum(len(s.get("words", [])) for s in normalize_transcript(resp)["segments"])
        print(f"  {stem}: ~{secs / 60:.1f} min, {n_words} words -> {out_path}")

    total_seconds = sum(
        estimate_seconds(path) for stem, path in chapters
        if os.path.exists(os.path.join(args.out_dir, f"{stem}.json"))
    )
    print(f"this run: {len(todo) - len(failed)} chapters, ~{done_seconds / 60:.1f} audio-min, "
          f"est ${estimate_cost(done_seconds, args.provider):.2f}")
    print(f"cumulative: {skipped + len(todo) - len(failed)} transcripts, "
          f"~{total_seconds / 60:.1f} audio-min, est ${estimate_cost(total_seconds, args.provider):.2f}")
    if failed:
        sys.exit(f"{len(failed)} chapters failed: {', '.join(failed[:10])}"
                 + ("…" if len(failed) > 10 else ""))


if __name__ == "__main__":
    main()
