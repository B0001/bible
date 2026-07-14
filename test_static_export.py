"""Tests for the static-site export (P9.1) and JS stemmer fidelity (P9.2)."""
import importlib.util
import json
import os
import shutil
import subprocess

import pytest

from parser import corpus_ranks, tokenize_and_stem, verse_difficulty

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_export_module():
    spec = importlib.util.spec_from_file_location(
        "export_static", os.path.join(_HERE, "scripts", "export_static.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_export_bible_tokens_match_pipeline(tmp_path):
    """Exported tokens are exactly tokenize_and_stem's output per verse."""
    src = tmp_path / "mini.txt"
    src.write_text(
        "The running waters were blessed -- Gen 1:1\n"
        "שָׁלוֹם עַל יִשְׂרָאֵל -- Ps 125:5\n"
    )
    mod = _load_export_module()

    for lang, expected_verse in [("en", "The running waters were blessed")]:
        entry = {"id": "t", "name": "T", "lang": lang, "source": str(src)}
        m = mod.export_bible(entry, str(tmp_path))
        assert m is not None
        data = json.loads((tmp_path / "t.json").read_text())
        assert data["tokens"][0] == tokenize_and_stem(data["verses"][0], lang)
        assert data["refs"][0] == "Gen 1:1"

    he_entry = {"id": "th", "name": "TH", "lang": "he", "source": str(src)}
    mod.export_bible(he_entry, str(tmp_path))
    data = json.loads((tmp_path / "th.json").read_text())
    assert data["tokens"][1] == tokenize_and_stem(data["verses"][1], "he")
    assert data["tokens"][1] == ["שלום", "על", "ישראל"]


def test_export_skips_missing_source(tmp_path):
    mod = _load_export_module()
    entry = {"id": "x", "name": "X", "lang": "en", "source": str(tmp_path / "nope.txt")}
    assert mod.export_bible(entry, str(tmp_path)) is None


def test_manifest_stopwords_match_nltk():
    mod = _load_export_module()
    from nltk.corpus import stopwords

    assert set(mod.stopwords_for_langs(["en"])["en"]) == set(stopwords.words("english"))


# --------------------------------------------------------------------------- #
# JS stemmer fidelity: the vendored Snowball JS must produce the same stems as
# NLTK's SnowballStemmer for non-stopword vocabulary. Runs where node exists
# (GitHub Actions runners include it); skipped otherwise.
# --------------------------------------------------------------------------- #

_node = shutil.which("node")

# Representative vocabulary: regular inflections, Snowball special cases,
# y/i endings, doubled consonants, and words from the sample corpus.
_FIDELITY_WORDS = [
    "running", "ran", "runs", "blessed", "blessing", "waters", "created",
    "generously", "happiness", "dying", "lying", "tying", "agreed", "skis",
    "beautiful", "multiply", "conspicuous", "abilities", "national",
    "righteousness", "everlasting", "wickedness", "trembling", "delivered",
]


@pytest.mark.skipif(not _node, reason="node not installed")
def test_vendored_js_stemmer_matches_nltk(tmp_path):
    from nltk.corpus import stopwords
    from nltk.stem.snowball import SnowballStemmer

    stemmer = SnowballStemmer("english")  # no stopword skip: raw algorithm vs raw algorithm
    words = [w for w in _FIDELITY_WORDS if w not in stopwords.words("english")]
    expected = [stemmer.stem(w) for w in words]

    runner = tmp_path / "runner.mjs"
    vendor = os.path.join(_HERE, "site", "vendor")
    runner.write_text(
        f"import EnglishStemmer from '{vendor}/english-stemmer.js';\n"
        "const s = new EnglishStemmer();\n"
        f"const words = {json.dumps(words)};\n"
        "for (const w of words) console.log(s.stem(w));\n"
    )
    out = subprocess.run(
        [_node, str(runner)], capture_output=True, text=True, check=True
    )
    got = out.stdout.strip().splitlines()
    assert got == expected


def test_stopword_skip_reproduces_ignore_stopwords():
    """Skipping manifest stopwords + raw Snowball == SnowballStemmer(ignore_stopwords=True).

    This is the contract app.js relies on: stopword -> unchanged, else stem.
    """
    from nltk.corpus import stopwords

    sw = set(stopwords.words("english"))
    # "was" is a stopword: ignore_stopwords keeps it verbatim
    assert "was" in sw
    assert tokenize_and_stem("was", "en")[0] == "was"
    # "running" is not: it must be stemmed
    assert "running" not in sw
    assert tokenize_and_stem("running", "en")[0] == "run"


# --------------------------------------------------------------------------- #
# JS rank functions parity: the JS corpus_ranks and verse_difficulty must
# produce the same results as Python. Runs where node exists; skipped otherwise.
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not _node, reason="node not installed")
def test_rank_js_matches_python(tmp_path):
    """JS corpusRanks/verseDifficulty/nextWords match Python equivalents.

    Uses fixtures from PHASE12_DESIGN.md and PHASE13_DESIGN.md to verify parity.
    """
    # D2 fixture
    tokens = [["a", "b", "a"], ["b", "c"], ["a", "c", "d", "a"]]
    ranks = corpus_ranks(tokens)

    # Python expectations
    assert ranks == {"a": 1, "b": 2, "c": 3, "d": 4}
    py_diffs = [verse_difficulty(t, ranks, known=frozenset()) for t in tokens]
    assert py_diffs == [2, 3, 4]
    py_v2_with_known = verse_difficulty(tokens[1], ranks, known=frozenset(["c"]))
    assert py_v2_with_known == 2
    py_v3_with_known = verse_difficulty(tokens[2], ranks, known=frozenset(["c"]))
    assert py_v3_with_known == 4

    # Run JS to compute the same
    rank_module = os.path.join(_HERE, "site", "rank.js")
    runner = tmp_path / "rank_runner.mjs"
    runner.write_text(
        f"import {{ corpusRanks, verseDifficulty, nextWords }} from 'file://{rank_module}';\n"
        f"const tokens = {json.dumps(tokens)};\n"
        "const ranks = corpusRanks(tokens);\n"
        "const ranksObj = Object.fromEntries(ranks);\n"
        "const diffs = tokens.map(t => verseDifficulty(t, ranks));\n"
        "const v2WithC = verseDifficulty(tokens[1], ranks, 0.95, new Set(['c']));\n"
        "const v3WithC = verseDifficulty(tokens[2], ranks, 0.95, new Set(['c']));\n"
        "const nextW = nextWords(tokens, ranks, 1, new Set());\n"
        "console.log(JSON.stringify({ ranks: ranksObj, diffs, v2WithC, v3WithC, nextW }));\n"
    )
    out = subprocess.run(
        [_node, str(runner)], capture_output=True, text=True, check=True
    )
    result = json.loads(out.stdout.strip())

    # Compare rank/difficulty
    assert result["ranks"] == ranks
    assert result["diffs"] == py_diffs
    assert result["v2WithC"] == py_v2_with_known
    assert result["v3WithC"] == py_v3_with_known

    # Compare nextWords: at level N=1, only "b" unlocks v1 (v2 needs both b+c, v3 needs c+d or b+d)
    assert result["nextW"] == [{"stem": "b", "count": 1, "rank": 2}]


# --------------------------------------------------------------------------- #
# P14.4 Mobile UI acceptance checks: manifest, icon, HTML structure, CSS tokens
# --------------------------------------------------------------------------- #


def test_mobile_ui_structure():
    """Verify P14.4 acceptance requirements: manifest, icon, HTML/CSS structure."""
    site_dir = os.path.join(_HERE, "site")

    # 1. Manifest and icon files exist
    manifest_path = os.path.join(site_dir, "manifest.webmanifest")
    icon_path = os.path.join(site_dir, "icon.svg")
    assert os.path.exists(manifest_path), f"Missing {manifest_path}"
    assert os.path.exists(icon_path), f"Missing {icon_path}"

    # 2. Parse manifest JSON and verify required fields
    with open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["name"] == "Graded Bible Reader"
    assert manifest["short_name"] == "Reader"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "."
    assert manifest["theme_color"] == "#4f46e5"
    assert manifest["background_color"] == "#0f1115"
    assert len(manifest["icons"]) > 0
    assert manifest["icons"][0]["src"] == "icon.svg"
    assert manifest["icons"][0]["type"] == "image/svg+xml"

    # 3. Verify icon SVG content (check for key elements)
    with open(icon_path) as f:
        icon_svg = f.read()
    assert '<svg' in icon_svg
    assert 'viewBox="0 0 100 100"' in icon_svg
    assert 'fill="#4f46e5"' in icon_svg  # Indigo background
    assert '<path' in icon_svg  # Book glyph

    # 4. Parse HTML and verify required structure
    html_path = os.path.join(site_dir, "index.html")
    with open(html_path) as f:
        html = f.read()

    # Check for manifest and icon links in head
    assert 'href="manifest.webmanifest"' in html, "Manifest link missing"
    assert 'href="icon.svg"' in html, "Icon link missing"
    assert 'rel="manifest"' in html, "Manifest rel attribute missing"

    # Check for required semantic IDs
    assert 'id="level"' in html, "Level slider (#level) missing"
    assert 'id="learn-next"' in html, "Learn-next section (#learn-next) missing"
    assert 'id="bible-select"' in html, "Bible select (#bible-select) missing"
    assert 'id="prev-page"' in html, "Prev pagination button missing"
    assert 'id="next-page"' in html, "Next pagination button missing"

    # Check for topbar styling class
    assert 'class="topbar"' in html, "Topbar header missing"

    # 5. Parse CSS and verify design tokens
    css_path = os.path.join(site_dir, "style.css")
    with open(css_path) as f:
        css = f.read()

    # Light mode colors
    assert "--bg:" in css or "--bg :" in css, "CSS var --bg missing"
    assert "--fg:" in css or "--fg :" in css, "CSS var --fg missing"
    assert "--accent:" in css or "--accent :" in css, "CSS var --accent missing"
    assert "--card:" in css or "--card :" in css, "CSS var --card missing"
    assert "--border:" in css or "--border :" in css, "CSS var --border missing"
    assert "--muted:" in css or "--muted :" in css, "CSS var --muted missing"
    assert "--readable-bg:" in css or "--readable-bg :" in css, "CSS var --readable-bg missing"
    assert "--readable-edge:" in css or "--readable-edge :" in css, "CSS var --readable-edge missing"

    # Dark mode override
    assert "@media (prefers-color-scheme: dark)" in css, "Dark mode media query missing"

    # Specific color values for dark mode (verify the dark override is present)
    assert "#0f1115" in css, "Dark mode background color missing"
    assert "#e5e7eb" in css, "Dark mode foreground color missing"

    # Mobile-first responsive design
    assert "max-width: 720px" in css or "max-width:720px" in css, "Body max-width missing"
    assert "@media" in css, "Media queries missing (responsive design)"

    # Topbar sticky positioning
    assert "sticky" in css, "Sticky positioning missing"

    # Button/control sizing for touch targets (48px minimum)
    assert "min-height: 48px" in css or "min-height:48px" in css, "Touch target size (48px) missing"

    # Card styling for mobile
    assert "border-radius" in css, "Border radius styling missing"
    assert "display: flex" in css or "display:flex" in css, "Flexbox layout missing"

    # RTL support for Hebrew/Greek
    assert 'dir="rtl"' in html or "direction:" in css, "RTL support missing"
