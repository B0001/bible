#!/usr/bin/env python3
"""Dash front end for the vocabulary-graded Bible reader.

Loads the graded CSV produced by ``parser.py`` (columns ``ref, verse,
comprehension_rate``) and lets you filter verses by comprehension rate -- so you
can find the ~95% "sweet spot" verses for vocabulary growth -- and search by
reference or text.

Configuration (env vars):
    BIBLE_GRADED_CSV   path to the graded CSV (default: out/graded.csv)
    DASH_HOST          bind host (default: 127.0.0.1)
    DASH_PORT          bind port (default: 8050)
    DASH_DEBUG         "1"/"true" to enable debug mode (default: off)
"""
import os

import polars as pl
from dash import Dash, Input, Output, dash_table, dcc, html

GRADED_CSV = os.environ.get("BIBLE_GRADED_CSV", "out/graded.csv")


def load_graded(path):
    """Load the graded CSV from a local path or s3:// URI."""
    if path.startswith("s3://"):
        try:
            import fsspec
        except ImportError:
            raise ImportError("pip install 'bible-reader[s3]' to read from S3") from None
        with fsspec.open(path, "rb") as f:
            return pl.read_csv(f).sort("comprehension_rate", descending=True)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Graded data not found at {path!r}. Run the pipeline first, e.g.:\n"
            "  python parser.py --bible sample/nasb_sample.txt "
            "--vocab sample/my_vocab.txt --out out/graded.csv\n"
            "or set BIBLE_GRADED_CSV to an existing file."
        )
    return pl.read_csv(path).sort("comprehension_rate", descending=True)


df = load_graded(GRADED_CSV)

app = Dash(__name__)
app.title = "Bible Reader — graded by your vocabulary"


def to_records(frame):
    """polars DataFrame -> list of dicts with comprehension_rate as a percentage."""
    return (
        frame.with_columns(
            (pl.col("comprehension_rate") * 100).round(1).alias("comprehension_%")
        )
        .select("ref", "verse", "comprehension_%")
        .to_dicts()
    )


app.layout = html.Div(
    style={"maxWidth": "900px", "margin": "0 auto", "fontFamily": "sans-serif"},
    children=[
        html.H3("Bible verses graded by your vocabulary"),
        html.P(
            "Filter by comprehension rate — the share of a verse's words you already "
            "know. ~95% is the language-learning sweet spot."
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
        html.Label("Search reference or text"),
        dcc.Input(
            id="search",
            type="text",
            value="",
            placeholder="e.g. Psalm, or 'light'",
            style={"width": "100%", "marginBottom": "1rem"},
        ),
        html.Div(id="count"),
        dash_table.DataTable(
            id="table",
            columns=[
                {"name": "Reference", "id": "ref"},
                {"name": "Verse", "id": "verse"},
                {"name": "Comprehension %", "id": "comprehension_%", "type": "numeric"},
            ],
            data=to_records(df),
            sort_action="native",
            page_size=20,
            style_cell={
                "textAlign": "left",
                "whiteSpace": "normal",
                "height": "auto",
                "padding": "6px",
            },
            style_cell_conditional=[
                {"if": {"column_id": "verse"}, "width": "65%"},
                {"if": {"column_id": "comprehension_%"}, "textAlign": "right"},
            ],
        ),
    ],
)


@app.callback(
    Output("table", "data"),
    Output("count", "children"),
    Input("rate-range", "value"),
    Input("search", "value"),
)
def update_table(rate_range, search):
    low, high = rate_range
    rate = pl.col("comprehension_rate") * 100
    filtered = df.filter((rate >= low) & (rate <= high))

    if search:
        needle = search.lower()
        filtered = filtered.filter(
            pl.col("ref").str.to_lowercase().str.contains(needle, literal=True)
            | pl.col("verse").str.to_lowercase().str.contains(needle, literal=True)
        )

    count = f"{filtered.height} of {df.height} verses match."
    return to_records(filtered), count


def _env_bool(name):
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


if __name__ == "__main__":
    app.run(
        host=os.environ.get("DASH_HOST", "127.0.0.1"),
        port=int(os.environ.get("DASH_PORT", "8050")),
        debug=_env_bool("DASH_DEBUG"),
    )
