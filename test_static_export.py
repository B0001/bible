"""Tests for the static-site export (P9.1) and JS stemmer fidelity (P9.2)."""
import importlib.util
import json
import os
import shutil
import subprocess

import pytest

from parser import tokenize_and_stem

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

    assert set(mod.english_stopwords()) == set(stopwords.words("english"))


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
