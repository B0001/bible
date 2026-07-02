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
import logging
import os
import re
import sqlite3
import sys
import tomllib
import unicodedata

import polars as pl
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update

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
    return dicts


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

bible_options = [{"label": v["name"], "value": k} for k, v in BIBLES.items()]

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
