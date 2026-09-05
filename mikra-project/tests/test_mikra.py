"""Fast, dependency-free tests. Run: python -m pytest tests/  (or python tests/test_mikra.py)"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mikra import books, hebrew, manifest, rename, validate  # noqa: E402

GEN_1_1 = "בְּרֵאשִׁ֖ית בָּרָ֣א אֱלֹהִ֑ים אֵ֥ת הַשָּׁמַ֖יִם וְאֵ֥ת הָאָֽרֶץ׃"


def test_tokenize_and_normalize():
    toks = hebrew.tokenize_line(GEN_1_1, verse=1)
    assert len(toks) == 7
    assert toks[0]["display"].startswith("בְּ")  # display keeps points
    bare = hebrew.normalize(toks[0]["display"])
    assert bare == "בראשית"  # normalized has no cantillation/niqqud
    with_niqqud = hebrew.normalize(toks[0]["display"], keep_niqqud=True)
    assert "\u05b0" <= min(c for c in with_niqqud if c > "\u05af")  # points survive


def test_maqqef_stays_one_token():
    toks = hebrew.tokenize_line("עַל־פְּנֵ֣י הַמָּֽיִם׃", verse=2)
    assert len(toks) == 2
    assert hebrew.MAQAF in toks[0]["display"]


def test_chapter_tokenizer_tracks_verses():
    toks = hebrew.tokenize_chapter(GEN_1_1 + "\n\n" + GEN_1_1)
    assert toks[0]["verse"] == 1 and toks[-1]["verse"] == 2


def test_book_lookup_variants():
    assert books.abbrev_for("Genesis") == "Gen"
    assert books.abbrev_for("Song_of_Songs") == "Song"
    assert books.abbrev_for("1Samuel") == "1Sam"
    assert books.abbrev_for("PSALMS") == "Ps"
    assert books.abbrev_for("NotABook") is None


def test_chapter_id_padding():
    assert books.chapter_id("Gen", 1) == "Gen_01"
    assert books.chapter_id("Ps", 7) == "Ps_007"
    assert books.chapter_id("Ps", 119) == "Ps_119"


def test_vendor_filename_parsing():
    assert rename.parse_vendor_name("B01___01_Genesis_____HBRHMTN1DA.mp3") == ("Gen", 1)
    assert rename.parse_vendor_name("B19__119_Psalms_____HBRHMTN1DA.mp3") == ("Ps", 119)
    assert rename.parse_vendor_name("B09___05_1Samuel____HBRHMTN1DA.mp3") == ("1Sam", 5)
    assert rename.parse_vendor_name("README.txt") is None
    assert rename.parse_vendor_name("B99___01_Atlantis_____X.mp3") is None


def _fake_root(tmp: Path, word_end_overrun=False):
    (tmp / "text").mkdir()
    (tmp / "alignments").mkdir()
    (tmp / "text" / "Gen_01.txt").write_text(GEN_1_1, encoding="utf-8")
    toks = hebrew.tokenize_line(GEN_1_1, 1)
    t, wlist = 0.0, []
    for i, tok in enumerate(toks):
        wlist.append({"i": i, "display": tok["display"], "start": round(t, 2),
                      "end": round(t + 0.4, 2), "conf": 0.95, "verse": 1})
        t += 0.5
    data = {"book": "Gen", "chapter": 1, "audio": "audio/Gen_01.mp3",
            "duration": (2.0 if word_end_overrun else 10.0), "words": wlist}
    (tmp / "alignments" / "Gen_01.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"book": "Gen", "chapter": 1, "audio": "audio/Gen_01.mp3",
            "alignment": "alignments/Gen_01.json", "status": "aligned"}


def test_validate_passes_good_chapter():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        entry = _fake_root(tmp)
        assert validate.check_chapter(tmp, "Gen_01", entry) == []


def test_validate_flags_overrun():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        entry = _fake_root(tmp, word_end_overrun=True)
        problems = validate.check_chapter(tmp, "Gen_01", entry)
        assert any("past audio" in p for p in problems)


def test_manifest_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        m = manifest.load(tmp)
        manifest.chapter_entry(m, "Gen_01")["status"] = "renamed"
        manifest.save(tmp, m)
        assert manifest.load(tmp)["chapters"]["Gen_01"]["status"] == "renamed"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
