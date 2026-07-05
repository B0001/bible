#!/usr/bin/env python3
"""Dash front end for the vocabulary-graded Bible reader.

Loads graded CSVs produced by ``parser.py`` (columns ``ref, verse,
comprehension_rate`` plus optional ``known_count, total_count``) and provides:
  - Multi-Bible selection via bibles.toml  (P7.2)
  - Read tracking via SQLite reads.db     (P7.3)
  - Longest readable passage finder       (P7.4)

Configuration (env vars):
    BIBLE_GRADED_CSV   fallback graded CSV if bibles.toml absent (default: out/graded.csv)
    READS_DB           path to SQLite reads database (default: reads.db)
    DASH_HOST          bind host (default: 127.0.0.1)
    DASH_PORT          bind port (default: 8050)
    DASH_DEBUG         "1"/"true" to enable debug mode (default: off)
"""
import json
import logging
import os
import re
import sqlite3
import sys
import tomllib
import unicodedata

import polars as pl
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update
from flask import send_from_directory

from parser import longest_span

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
GRADED_CSV = os.environ.get("BIBLE_GRADED_CSV", "out/graded.csv")
READS_DB = os.environ.get("READS_DB", "reads.db")
KNOWN_RATE = 0.95
# Cap rows sent per callback: page_size only paginates client-side, so without
# this a wide filter ships the entire corpus (~6 MB JSON for NASB) per request.
MAX_TABLE_ROWS = 500

# Strips Hebrew nikudim/cantillation (U+0591–U+05C7), Greek combining diacritics
# (U+0300–U+036F, after NFD decomposition), and Arabic harakat/tatweel
# (U+064B–U+0652, U+0670, U+0640) so searching "שלום" matches "שָׁלוֹם".
_MARK_RE = re.compile(r"[֑-ׇ̀-ًͯ-ْٰـ]")

RTL_LANGS = {"he", "ar"}


def _strip_marks(text):
    return _MARK_RE.sub("", unicodedata.normalize("NFD", text))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_graded_raw(path):
    """Load graded CSV (local or s3://) in file order; return DataFrame."""
    if path.startswith("s3://"):
        try:
            import fsspec
        except ImportError:
            raise ImportError("pip install 'bible-reader[s3]' to read from S3") from None
        with fsspec.open(path, "rb") as f:
            return pl.read_csv(f)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return pl.read_csv(path)


def load_audio_manifest(manifest_path):
    """Load a Phase 10 audio manifest and its chapter sidecar JSONs.

    Returns {"audio_dir": abs path, "by_ref": {ref: {"file", "start"}},
    "chapters": {audio file: [{"ref", "start", "end", "words"?}, ...]}} — or
    None when the manifest, all sidecars, or all audio files are missing (the
    audio UI then stays hidden, same degradation pattern as missing graded
    CSVs). Each verse carries the sidecar's per-word timings
    (``words: [{display, start, end, conf}]``, P10.5) when present, so the
    clientside karaoke panel can render/seek/highlight individual words; a
    verse lacking ``words`` degrades to the verse-only reader.
    """
    manifest_path = os.path.join(_HERE, manifest_path)
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("audio manifest unreadable: %s — no audio", e)
        return None

    audio_dir = os.path.normpath(os.path.join(_HERE, manifest["audio_dir"]))
    by_ref, chapters = {}, {}
    for entry in manifest.get("chapters", []):
        try:
            with open(os.path.join(_HERE, entry["sidecar"]), encoding="utf-8") as f:
                sidecar = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("audio sidecar unreadable (%s) — skipping chapter", e)
            continue
        fname = os.path.basename(sidecar["audio"])
        if not os.path.exists(os.path.join(audio_dir, fname)):
            log.warning("audio file missing: %s — skipping chapter", fname)
            continue
        verses = []
        for v in sidecar["verses"]:
            verse = {"ref": v["ref"], "start": v["start"], "end": v["end"]}
            if "words" in v:
                verse["words"] = v["words"]
            verses.append(verse)
        chapters[fname] = verses
        for v in verses:
            by_ref[v["ref"]] = {"file": fname, "start": v["start"]}
    return {"audio_dir": audio_dir, "by_ref": by_ref, "chapters": chapters} if by_ref else None


def load_bibles():
    """Load Bible configs from bibles.toml; fallback to GRADED_CSV env var."""
    bibles = {}

    toml_path = os.path.join(_HERE, "bibles.toml")
    if os.path.exists(toml_path):
        with open(toml_path, "rb") as f:
            config = tomllib.load(f)
        for entry in config.get("bibles", []):
            bid = entry["id"]
            try:
                df_ord = load_graded_raw(entry["graded_csv"])
                df_ord = df_ord.with_columns(
                    pl.col("verse").map_elements(_strip_marks, return_dtype=pl.Utf8).alias("verse_plain")
                )
                df = df_ord.sort("comprehension_rate", descending=True)
                bibles[bid] = {
                    "name": entry["name"],
                    "lang": entry.get("lang", "en"),
                    "df": df,
                    "df_ord": df_ord,
                    "audio": (
                        load_audio_manifest(entry["audio_manifest"])
                        if "audio_manifest" in entry
                        else None
                    ),
                }
                log.info("Loaded bible %r (%d verses)", bid, df.height)
            except FileNotFoundError:
                log.warning("Bible %r CSV not found: %s — skipping", bid, entry["graded_csv"])

    # Fallback: if bibles.toml absent or produced nothing, try GRADED_CSV
    if not bibles and os.path.exists(GRADED_CSV):
        try:
            df_ord = load_graded_raw(GRADED_CSV)
            df_ord = df_ord.with_columns(
                pl.col("verse").map_elements(_strip_marks, return_dtype=pl.Utf8).alias("verse_plain")
            )
            df = df_ord.sort("comprehension_rate", descending=True)
            bibles["nasb"] = {
                "name": "NASB (English)",
                "lang": "en",
                "df": df,
                "df_ord": df_ord,
                "audio": None,
            }
            log.info("Loaded fallback NASB from %s (%d verses)", GRADED_CSV, df.height)
        except Exception as e:
            log.warning("Could not load fallback CSV %s: %s", GRADED_CSV, e)

    return bibles


BIBLES = load_bibles()

if not BIBLES:
    sys.exit(
        "No Bible data found. Run parser.py to produce a graded CSV, e.g.:\n"
        "  python parser.py --bible sample/nasb_sample.txt "
        "--vocab sample/my_vocab.txt --out out/graded.csv"
    )

DEFAULT_BIBLE = next(iter(BIBLES))
HAS_AUDIO = any(b["audio"] for b in BIBLES.values())


# ---------------------------------------------------------------------------
# SQLite reads tracking (P7.3)
# ---------------------------------------------------------------------------

def _db():
    con = sqlite3.connect(READS_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS reads (
            bible_id TEXT NOT NULL,
            ref      TEXT NOT NULL,
            read_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (bible_id, ref)
        )
    """)
    con.commit()
    return con


def get_read_refs(bible_id):
    """Return set of refs marked read for the given bible_id."""
    try:
        con = _db()
        rows = con.execute(
            "SELECT ref FROM reads WHERE bible_id = ?", (bible_id,)
        ).fetchall()
        con.close()
        return {r[0] for r in rows}
    except Exception as e:
        log.warning("reads.db read error: %s", e)
        return set()


def _mark_read(bible_id, refs):
    try:
        con = _db()
        con.executemany(
            "INSERT OR IGNORE INTO reads (bible_id, ref) VALUES (?, ?)",
            [(bible_id, r) for r in refs],
        )
        con.commit()
        con.close()
    except Exception as e:
        log.warning("mark_read error: %s", e)


def _mark_unread(bible_id, refs):
    try:
        con = _db()
        con.executemany(
            "DELETE FROM reads WHERE bible_id = ? AND ref = ?",
            [(bible_id, r) for r in refs],
        )
        con.commit()
        con.close()
    except Exception as e:
        log.warning("mark_unread error: %s", e)


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Dash(__name__)
app.title = "Bible Reader — graded by your vocabulary"
server = app.server  # gunicorn entry point: `gunicorn dash_app:server`


@server.route("/health")
def health():
    return "ok", 200


@server.route("/audio/<bible_id>/<path:filename>")
def audio_file(bible_id, filename):
    """Serve chapter audio for bibles that have an audio manifest (P10.2).

    send_from_directory rejects path traversal and honors Range requests
    (conditional=True), which <audio> seeking depends on.
    """
    audio = (BIBLES.get(bible_id) or {}).get("audio")
    if not audio:
        return "not found", 404
    return send_from_directory(audio["audio_dir"], filename, conditional=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def has_count_cols(df):
    return "known_count" in df.columns and "total_count" in df.columns


def to_records(frame, read_refs):
    """polars DataFrame -> list of dicts for DataTable, with Unknown and Read columns."""
    frame = frame.with_columns(
        (pl.col("comprehension_rate") * 100).round(1).alias("comprehension_%")
    )
    cols = ["ref", "verse", "comprehension_%"]
    if has_count_cols(frame):
        frame = frame.with_columns(
            (pl.col("total_count") - pl.col("known_count")).alias("unknown")
        )
        cols.append("unknown")
    dicts = frame.select(cols).to_dicts()
    for row in dicts:
        row["read"] = "✓" if row["ref"] in read_refs else ""
        row["id"] = row["ref"]  # row_id in active_cell: audio click-to-seek
    return dicts


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

bible_options = [{"label": v["name"], "value": k} for k, v in BIBLES.items()]

# Audio player (P10.2): only in the layout when at least one bible has audio,
# so bibles without it get a byte-identical UI. `audio-chapter` carries the
# clicked verse's chapter (src/seek/timings/words/rtl) to the clientside seek
# callback; `audio-now` is written by assets/audio.js on timeupdate and drives
# the currently-read row highlight. The `karaoke` panel (P10.6) is rendered
# clientside by assets/audio.js — the DataTable can't hold interactive spans,
# so the currently-playing verse's words appear here as clickable spans
# (click = seek, double-click = loop that word) with a live word highlight.
audio_layout = (
    [
        html.Div(
            id="audio-panel",
            style={"display": "none"},
            children=[
                html.Audio(id="audio-player", controls=True, style={"width": "100%"}),
                html.Div(
                    id="karaoke",
                    style={
                        "marginTop": "0.5rem",
                        "lineHeight": "2",
                        "fontSize": "1.3rem",
                    },
                ),
            ],
        ),
        dcc.Store(id="audio-chapter"),
        dcc.Store(id="audio-now"),
        html.Div(id="audio-dummy", style={"display": "none"}),
    ]
    if HAS_AUDIO
    else []
)

app.layout = html.Div(
    style={"maxWidth": "900px", "margin": "0 auto", "fontFamily": "sans-serif"},
    children=[
        html.H3("Bible verses graded by your vocabulary"),
        html.P(
            "Filter by comprehension rate — the share of a verse’s words you already "
            "know. ~95 % is the language-learning sweet spot."
        ),
        html.Label("Bible"),
        dcc.Dropdown(
            id="bible-select",
            options=bible_options,
            value=DEFAULT_BIBLE,
            clearable=False,
            style={"marginBottom": "1rem"},
        ),
        html.Label("Comprehension rate (%)"),
        dcc.RangeSlider(
            id="rate-range",
            min=0,
            max=100,
            step=5,
            value=[90, 100],
            marks={i: str(i) for i in range(0, 101, 10)},
            tooltip={"placement": "bottom", "always_visible": False},
        ),
        html.Label("Max unknown words per verse (blank = off; 1 = the i+1 sweet spot)"),
        dcc.Input(
            id="max-unknown",
            type="number",
            min=0,
            placeholder="e.g. 1",
            style={"width": "100%", "marginBottom": "1rem"},
        ),
        html.Label("Search reference or text"),
        dcc.Input(
            id="search",
            type="text",
            value="",
            placeholder="e.g. Psalm, or ‘light’",
            style={"width": "100%", "marginBottom": "1rem"},
        ),
        dcc.Checklist(
            id="unread-toggle",
            options=[{"label": " Show unread only", "value": "unread"}],
            value=[],
            style={"marginBottom": "0.5rem"},
        ),
        html.Div(id="count"),
        html.Div(id="progress", style={"marginBottom": "0.5rem", "color": "#555"}),
        dash_table.DataTable(
            id="table",
            columns=[
                {"name": "Reference", "id": "ref"},
                {"name": "Verse", "id": "verse"},
                {"name": "Comprehension %", "id": "comprehension_%", "type": "numeric"},
                {"name": "Unknown", "id": "unknown", "type": "numeric"},
                {"name": "Read", "id": "read"},
            ],
            data=[],
            sort_action="native",
            page_size=20,
            row_selectable="multi",
            selected_rows=[],
            style_cell={
                "textAlign": "left",
                "whiteSpace": "normal",
                "height": "auto",
                "padding": "6px",
            },
            style_cell_conditional=[
                {"if": {"column_id": "verse"}, "width": "60%"},
                {"if": {"column_id": "comprehension_%"}, "textAlign": "right"},
                {"if": {"column_id": "read"}, "textAlign": "center", "width": "5%"},
            ],
        ),
        *audio_layout,
        html.Div(
            style={"display": "flex", "gap": "0.5rem", "margin": "0.75rem 0"},
            children=[
                html.Button("Mark selected as read", id="mark-read", n_clicks=0),
                html.Button("Mark selected as unread", id="mark-unread", n_clicks=0),
            ],
        ),
        html.Button(
            "Find longest passage",
            id="find-passage",
            n_clicks=0,
            style={"marginBottom": "0.75rem"},
        ),
        html.Div(id="passage-panel", style={"display": "none"}),
        # Incremented by mark callbacks to trigger table refresh
        dcc.Store(id="reads-store", data=0),
    ],
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def _cell_styles(lang):
    """Column styles; RTL scripts (Hebrew, Arabic) render right-to-left."""
    styles = [
        {"if": {"column_id": "verse"}, "width": "60%"},
        {"if": {"column_id": "comprehension_%"}, "textAlign": "right"},
        {"if": {"column_id": "unknown"}, "textAlign": "right", "width": "7%"},
        {"if": {"column_id": "read"}, "textAlign": "center", "width": "5%"},
    ]
    if lang in RTL_LANGS:
        styles[0] = {
            "if": {"column_id": "verse"},
            "width": "60%",
            "direction": "rtl",
            "textAlign": "right",
        }
    return styles


@app.callback(
    Output("table", "data"),
    Output("count", "children"),
    Output("progress", "children"),
    Output("table", "style_cell_conditional"),
    Input("bible-select", "value"),
    Input("rate-range", "value"),
    Input("max-unknown", "value"),
    Input("search", "value"),
    Input("unread-toggle", "value"),
    Input("reads-store", "data"),
)
def update_table(bible_id, rate_range, max_unknown, search, unread_toggle, _reads_store):
    if not bible_id or bible_id not in BIBLES:
        return [], "No bible loaded.", "", _cell_styles("en")

    df = BIBLES[bible_id]["df"]
    read_refs = get_read_refs(bible_id)

    low, high = rate_range
    rate = pl.col("comprehension_rate") * 100
    filtered = df.filter((rate >= low) & (rate <= high))

    if max_unknown is not None and has_count_cols(df):
        filtered = filtered.filter(
            pl.col("total_count") - pl.col("known_count") <= max_unknown
        )

    if search:
        needle = _strip_marks(search).lower()
        filtered = filtered.filter(
            pl.col("ref").str.to_lowercase().str.contains(needle, literal=True)
            | pl.col("verse_plain").str.to_lowercase().str.contains(needle, literal=True)
        )

    if unread_toggle and "unread" in unread_toggle and read_refs:
        filtered = filtered.filter(~pl.col("ref").is_in(list(read_refs)))

    if filtered.height > MAX_TABLE_ROWS:
        count = (
            f"Showing first {MAX_TABLE_ROWS} of {filtered.height} matches "
            f"({df.height} verses total) — narrow the filter to see the rest."
        )
        filtered = filtered.head(MAX_TABLE_ROWS)
    else:
        count = f"{filtered.height} of {df.height} verses match."

    # Progress: verses at >=95% comprehension that have been read
    threshold_df = df.filter(pl.col("comprehension_rate") >= KNOWN_RATE)
    total_at_threshold = threshold_df.height
    read_at_threshold = sum(1 for r in threshold_df["ref"].to_list() if r in read_refs)
    progress = (
        f"{read_at_threshold} of {total_at_threshold} verses at "
        f"≥{int(KNOWN_RATE * 100)}% read."
    )

    return to_records(filtered, read_refs), count, progress, _cell_styles(BIBLES[bible_id]["lang"])


@app.callback(
    Output("reads-store", "data"),
    Input("mark-read", "n_clicks"),
    Input("mark-unread", "n_clicks"),
    State("table", "data"),
    State("table", "selected_rows"),
    State("bible-select", "value"),
    State("reads-store", "data"),
    prevent_initial_call=True,
)
def handle_mark(n_read, n_unread, table_data, selected_rows, bible_id, store_val):
    if not selected_rows or not table_data or not bible_id:
        return no_update

    refs = [table_data[i]["ref"] for i in selected_rows if i < len(table_data)]
    if not refs:
        return no_update

    trigger = ctx.triggered_id
    if trigger == "mark-read":
        _mark_read(bible_id, refs)
    elif trigger == "mark-unread":
        _mark_unread(bible_id, refs)

    # Increment store to signal update_table to re-run
    return (store_val or 0) + 1


@app.callback(
    Output("passage-panel", "children"),
    Output("passage-panel", "style"),
    Input("find-passage", "n_clicks"),
    State("bible-select", "value"),
    prevent_initial_call=True,
)
def find_passage(n_clicks, bible_id):
    panel_style = {
        "background": "#f5f5f5",
        "padding": "1rem",
        "borderRadius": "4px",
        "marginTop": "0.5rem",
    }

    if not bible_id or bible_id not in BIBLES:
        return "No bible selected.", panel_style

    df_ord = BIBLES[bible_id]["df_ord"]

    if not has_count_cols(df_ord):
        return (
            "Regrade with parser.py to enable this feature "
            "(needs known_count and total_count columns).",
            panel_style,
        )

    known = df_ord["known_count"].to_list()
    total = df_ord["total_count"].to_list()
    refs = df_ord["ref"].to_list()
    verses = df_ord["verse"].to_list()

    span = longest_span(known, total, KNOWN_RATE)
    if span is None:
        return f"No passage at ≥{int(KNOWN_RATE * 100)}% found.", panel_style

    best_i, best_j = span
    passage_known = sum(known[best_i:best_j])
    passage_total = sum(total[best_i:best_j])
    rate_pct = round(100 * passage_known / passage_total, 1) if passage_total else 0.0
    passage_text = "\n".join(
        f"{refs[i]}  {verses[i]}" for i in range(best_i, best_j)
    )

    pre_style = {"whiteSpace": "pre-wrap", "margin": 0}
    if BIBLES[bible_id]["lang"] in RTL_LANGS:
        pre_style.update({"direction": "rtl", "textAlign": "right"})

    children = [
        html.P(
            f"Longest passage: {refs[best_i]} – {refs[best_j - 1]} "
            f"({best_j - best_i} verses, {rate_pct}% comprehension)",
            style={"fontWeight": "bold", "marginBottom": "0.5rem"},
        ),
        html.Pre(passage_text, style=pre_style),
    ]
    return children, panel_style


# ---------------------------------------------------------------------------
# Audio playback (P10.2) — callbacks exist only when some bible has audio, so
# the app is unchanged otherwise.
# ---------------------------------------------------------------------------

def audio_for_click(bible_id, row_ref):
    """Chapter payload for a clicked verse: {"src", "seek", "verses", "rtl"} or
    None (bible has no audio, or this verse has no aligned chapter).

    ``verses`` carries each verse's per-word timings (``words``) when the
    sidecar had them, so the clientside karaoke panel can render them; ``rtl``
    drives the panel's text direction for Hebrew/Arabic bibles (P10.6)."""
    bible = BIBLES.get(bible_id) or {}
    audio = bible.get("audio")
    if not audio or not row_ref:
        return None
    hit = audio["by_ref"].get(row_ref)
    if hit is None:
        return None
    return {
        "src": f"/audio/{bible_id}/{hit['file']}",
        "seek": hit["start"],
        "verses": audio["chapters"][hit["file"]],
        "rtl": bible.get("lang") in RTL_LANGS,
    }


if HAS_AUDIO:

    @app.callback(
        Output("audio-chapter", "data"),
        Output("audio-panel", "style"),
        Input("table", "active_cell"),
        Input("bible-select", "value"),
        prevent_initial_call=True,
    )
    def audio_on_click(active_cell, bible_id):
        has_audio = bool((BIBLES.get(bible_id) or {}).get("audio"))
        style = {"margin": "0.75rem 0"} if has_audio else {"display": "none"}
        if ctx.triggered_id == "bible-select":
            return None, style  # clear stale chapter on bible switch
        chapter = audio_for_click(bible_id, (active_cell or {}).get("row_id"))
        return (chapter, style) if chapter else (no_update, style)

    # Seeking must not round-trip to the server: set src (if changed), wait for
    # metadata, then jump to the verse start. The chapter's verse timings are
    # parked on window for assets/audio.js's timeupdate highlighter.
    app.clientside_callback(
        """
        function(chapter) {
            const el = document.getElementById("audio-player");
            if (!el) return window.dash_clientside.no_update;
            window._bibleAudioVerses = chapter ? chapter.verses : null;
            window._bibleAudioRtl = chapter ? !!chapter.rtl : false;
            // Reset karaoke state (P10.6) on every chapter change / clear;
            // the timeupdate handler re-renders the panel for the new verse.
            window._bibleLoopWord = null;
            window._bibleKaraokeRef = null;
            window._bibleActiveWord = null;
            const karaoke = document.getElementById("karaoke");
            if (karaoke) karaoke.textContent = "";
            if (!chapter) {
                el.pause();
                el.removeAttribute("src");
                return "";
            }
            const seek = () => { el.currentTime = chapter.seek; el.play(); };
            if (el.getAttribute("src") !== chapter.src) {
                el.setAttribute("src", chapter.src);
                el.addEventListener("loadedmetadata", seek, {once: true});
                el.load();
            } else {
                seek();
            }
            return "";
        }
        """,
        Output("audio-dummy", "children"),
        Input("audio-chapter", "data"),
        prevent_initial_call=True,
    )

    # Highlight the verse currently being read (audio-now is set by
    # assets/audio.js). Ref strings never contain quotes.
    app.clientside_callback(
        """
        function(ref) {
            if (!ref) return [];
            return [{
                "if": {"filter_query": '{ref} = "' + ref + '"'},
                "backgroundColor": "#fff3b0"
            }];
        }
        """,
        Output("table", "style_data_conditional"),
        Input("audio-now", "data"),
        prevent_initial_call=True,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _env_bool(name):
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


if __name__ == "__main__":
    app.run(
        host=os.environ.get("DASH_HOST", "127.0.0.1"),
        port=int(os.environ.get("DASH_PORT", "8050")),
        debug=_env_bool("DASH_DEBUG"),
    )
