#!/usr/bin/env python3
"""Download a public-domain Bible from eBible.org and convert it to
verse-text -- reference format.

Usage:
    python scripts/convert_ebible.py --translation spaRV1909   # -> data/valera.txt
    python scripts/convert_ebible.py --translation fraLSG      # -> data/ls1910.txt
    python scripts/convert_ebible.py --translation arb-vd      # -> data/arabicsv.txt

Why this exists: getbible.net serves these three translations from CrossWire
modules whose terms don't clearly cover a public site (README, "Text and audio
credits"). eBible.org states each is public domain, and all three were
published before 1931. The output keeps the getbible file names and book
names, so refs (and the read verses the static reader keeps per ref in
localStorage) carry over. Requires only stdlib.
"""
import argparse
import io
import os
import re
import unicodedata
import urllib.request
import zipfile

URL = "https://ebible.org/Scriptures/{}_vpl.zip"

# eBible's VPL book codes in canonical order. Not USFM: SOL, EZE, JOE, NAH, MAR,
# JOH, PHI, JAM and 1JO-3JO where USFM has SNG, EZK, JOL, NAM, MRK, JHN, ...
CODES = [
    "GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA", "1KI", "2KI", "1CH",
    "2CH", "EZR", "NEH", "EST", "JOB", "PSA", "PRO", "ECC", "SOL", "ISA", "JER", "LAM", "EZE",
    "DAN", "HOS", "JOE", "AMO", "OBA", "JON", "MIC", "NAH", "HAB", "ZEP", "HAG", "ZEC", "MAL",
    "MAT", "MAR", "LUK", "JOH", "ACT", "ROM", "1CO", "2CO", "GAL", "EPH", "PHI", "COL", "1TH",
    "2TH", "1TI", "2TI", "TIT", "PHM", "HEB", "JAM", "1PE", "2PE", "1JO", "2JO", "3JO", "JUD",
    "REV",
]
MAX_EMPTY = 100  # RV1909 has 18 verses merged into a neighbour

# Book names exactly as getbible.net spelled them in the files these replace.
ES = [
    "Génesis", "Éxodo", "Levítico", "Números", "Deuteronomio", "Josué", "Jueces", "Rut",
    "1 Samuel", "2 Samuel", "1 Reyes", "2 Reyes", "1 Crónicas", "2 Crónicas", "Esdras", "Nehemías",
    "Ester", "Job", "Salmos", "Proverbios", "Eclesiastés", "Cantares", "Isaías", "Jeremías",
    "Lamentaciones", "Ezequiel", "Daniel", "Oseas", "Joel", "Amós", "Abdías", "Jonás", "Miqueas",
    "Nahúm", "Habacuc", "Sofonías", "Hageo", "Zacarías", "Malaquías", "Mateo", "Marcos", "Lucas",
    "Juan", "Hechos", "Romanos", "1 Corintios", "2 Corintios", "Gálatas", "Efesios", "Filipenses",
    "Colosenses", "1 Tesalonicenses", "2 Tesalonicenses", "1 Timoteo", "2 Timoteo", "Tito",
    "Filemón", "Hebreos", "Santiago", "1 Pedro", "2 Pedro", "1 Juan", "2 Juan", "3 Juan", "Judas",
    "Apocalipsis",
]
FR = [
    "Genèse", "Exode", "Lévitique", "Nombres", "Deutéronome", "Josué", "Juges", "Ruth", "1 Samuel",
    "2 Samuel", "1 Rois", "2 Rois", "1 Chroniques", "2 Chroniques", "Esdras", "Néhémie", "Esther",
    "Job", "Psaumes", "Proverbes", "Ecclésiaste", "Cantique des Cantiques", "Ésaïe", "Jérémie",
    "Lamentations", "Ézéchiel", "Daniel", "Osée", "Joël", "Amos", "Abdias", "Jonas", "Michée",
    "Nahum", "Habacuc", "Sophonie", "Aggée", "Zacharie", "Malachie", "Matthieu", "Marc", "Luc",
    "Jean", "Actes des Apôtres", "Romains", "1 Corinthiens", "2 Corinthiens", "Galates",
    "Éphésiens", "Philippiens", "Colossiens", "1 Thessaloniciens", "2 Thessaloniciens",
    "1 Timothée", "2 Timothée", "Tite", "Philémon", "Hébreux", "Jacques", "1 Pierre", "2 Pierre",
    "1 Jean", "2 Jean", "3 Jean", "Jude", "Apocalypse",
]
AR = [
    "تكوين", "خروج", "لاويين", "عدد", "تثنية", "يشوع", "القضاة", "راعوث", "1 صموئيل", "2 صموئيل",
    "1 الملوك", "2 الملوك", "1 أخبار الأيام", "2 أخبار الأيام", "عزرا", "نحميا", "أستير", "أيوب",
    "المزامير", "الأمثال", "الجامعة", "نشيد الأنشاد", "إشعياء", "إرميا", "مراثي إرميا", "حزقيال",
    "دانيال", "هوشع", "يوئيل", "عاموس", "عوبديا", "يونان", "ميخا", "ناحوم", "حبقوق", "صفنيا",
    "حجي", "زكريا", "ملاخي", "متى", "مرقس", "لوقا", "يوحنا", "أعمال الرسل", "رومية", "1 كورنثوس",
    "2 كورنثوس", "غلاطية", "أفسس", "فيليبي", "كولوسي", "1 تسالونيكي", "2 تسالونيكي", "1 تيموثاوس",
    "2 تيموثاوس", "تيطس", "فليمون", "عبرانيين", "يعقوب", "1 بطرس", "2 بطرس", "1 يوحنا", "2 يوحنا",
    "3 يوحنا", "يهوذا", "الرؤيا",
]

# translation -> (default output, book names, strip [ ])
TEXTS = {
    # RV1909's brackets mark supplied words (italics in print); getbible's copy
    # had none. LS1910's two brackets are the edition's own and stay.
    "spaRV1909": ("data/valera.txt", ES, True),
    "fraLSG": ("data/ls1910.txt", FR, False),
    "arb-vd": ("data/arabicsv.txt", AR, False),
}


def convert_lines(lines, names, strip_brackets=False):
    """eBible VPL lines (``GEN 1:1 text``) -> ``[(ref, text)]`` using ``names``.

    Text is NFC-normalized and empty verses are dropped, as in the getbible
    copies (eBible's Arabic orders its vowel marks differently, so 90% of its
    verses would otherwise differ byte for byte from the same words). Raises
    ValueError on a line that doesn't parse, books other than CODES in order
    (another canon would misname every later book), more than MAX_EMPTY empty
    verses, or a verse containing the `` -- `` separator.
    """
    rows = []
    for ln in lines:
        m = re.fullmatch(r"(\S+) (\d+):(\d+) ?(.*)", ln)
        if not m:
            raise ValueError(f"unparsed line: {ln!r}")
        rows.append(m.groups())
    order = list(dict.fromkeys(r[0] for r in rows))
    if order != CODES:
        raise ValueError(f"expected eBible's 66 books in order, got {len(order)}: {order[:3]}")
    empty = sum(1 for r in rows if not r[3].strip())
    if empty > MAX_EMPTY:
        raise ValueError(f"{empty} empty verses: not a complete translation")
    name = dict(zip(CODES, names, strict=True))
    out = []
    for code, chapter, verse, text in rows:
        if strip_brackets:
            text = text.replace("[", "").replace("]", "")
        text = " ".join(unicodedata.normalize("NFC", text).split())
        if not text:
            continue
        if " -- " in f" {text} ":
            raise ValueError(f"separator inside verse text: {text!r}")
        out.append((f"{name[code]} {int(chapter)}:{int(verse)}", text))
    return out


def fetch_lines(translation):
    req = urllib.request.Request(URL.format(translation),
                                 headers={"User-Agent": "bible-reader/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        archive = zipfile.ZipFile(io.BytesIO(r.read()))
    return archive.read(f"{translation}_vpl.txt").decode("utf-8-sig").splitlines()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--translation", required=True, choices=sorted(TEXTS))
    ap.add_argument("--out", help="output path (default: the getbible-era file it replaces)")
    args = ap.parse_args()
    default_out, names, strip_brackets = TEXTS[args.translation]
    out = args.out or default_out
    verses = convert_lines(fetch_lines(args.translation), names, strip_brackets)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.writelines(f"{text} -- {ref}\n" for ref, text in verses)
    print(f"Wrote {len(verses)} verses -> {out}")


if __name__ == "__main__":
    main()
