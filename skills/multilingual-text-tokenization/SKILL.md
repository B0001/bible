---
name: multilingual-text-tokenization
description: >-
  Tokenize and normalize text across scripts so that word matching, vocabulary
  lookup, and search actually work. Use when handling Hebrew niqqud, Greek
  polytonic diacritics, Arabic harakat, Cyrillic, or any mixed-script corpus;
  when stemming decisions matter (Snowball vs bare forms); when diacritic-
  insensitive search returns nothing; when a Python pipeline and a JavaScript
  browser implementation must produce byte-identical tokens; or when setting up
  RTL rendering driven by content language.
---

# Multilingual tokenization for word matching

How to turn text in an arbitrary script into comparable word forms — the
normalization layer under any vocabulary matching, frequency ranking, or search
feature. Extracted from a reader covering 12 translations across 11 languages
(Latin, Cyrillic, Hebrew, Greek, Arabic scripts).

## The rule

**One function, both sides, always.** The corpus and the thing you compare it
against — a vocabulary list, a search needle, a review log key — must pass
through the *same* normalizer. Every bug in this area is a violation of that
sentence.

```python
vocab = set(tokenize_and_stem(open(path).read(), lang))
forms = tokenize_and_stem(text, lang)
```

Dispatch on the language of the *content*, not the UI locale.

## Per-language rules

| lang | strip | extract | stem |
|---|---|---|---|
| `he` Hebrew | niqqud + cantillation `[֑-ׇ]` | consonant runs `[א-ת]+` | none |
| `el` Greek | NFD, then combining marks `[̀-ͯ]` | letter runs `[α-ω]+` after lowercasing | none |
| `ar` Arabic | harakat `[ً-ْ]`, superscript alef `ٰ`, tatweel `ـ` | `\w+` on lowercased | Snowball `arabic` |
| Latin/Cyrillic | — | `\w+` on lowercased | Snowball where covered |
| everything else | — | `\w+` on lowercased | none |

```python
_HE_STRIP     = re.compile(r"[֑-ׇ]")
_HE_TOKEN     = re.compile(r"[א-ת]+")
_EL_DIACRITIC = re.compile(r"[̀-ͯ]")
_EL_TOKEN     = re.compile(r"[α-ω]+")
_AR_STRIP     = re.compile(r"[ً-ْٰـ]")

def tokenize(text, lang="en"):
    if lang == "he":
        return _HE_TOKEN.findall(_HE_STRIP.sub("", text))
    if lang == "el":
        norm = unicodedata.normalize("NFD", text)
        return _EL_TOKEN.findall(_EL_DIACRITIC.sub("", norm).lower())
    if lang == "ar":
        return TOKEN_RE.findall(_AR_STRIP.sub("", text).lower())
    return TOKEN_RE.findall(text.lower())
```

Note the Hebrew path does **not** lowercase (no case in the script) and does not
need NFD — the marks are already separate codepoints in the source encoding.
The Greek path needs NFD first, because precomposed ά and decomposed α+΄ are
different strings that must collapse to the same token. **Extracting by
character class instead of splitting on whitespace** is what makes both robust:
punctuation, maqqef, and stray marks fall out for free rather than needing a
strip list.

Snowball coverage is per-language and worth checking against your actual NLTK
version rather than a doc — the source repo's `SNOWBALL_LANGS` maps 9 codes
(`ar de en es fr it nl pt ru`) while its own docs claim 15. Select the stemmer
through a cached lookup that falls back to no stemming for uncovered languages:

```python
def _stemmer_for(lang):
    name = SNOWBALL_LANGS.get(lang)
    if name is None:
        return None
    try:
        return SnowballStemmer(name, ignore_stopwords=True)
    except (ValueError, LookupError):
        # ValueError: Snowball covers it but NLTK ships no stopword list.
        # LookupError: the stopwords corpus was never downloaded.
        return SnowballStemmer(name)
```

Catch those two exceptions specifically. A bare `except Exception` here hides a
missing-corpus install problem behind what looks like a language-support gap.

## Why strip rather than stem, for Hebrew and Greek

Consonantal Hebrew already conflates most morphological variants: removing the
pointing collapses inflections that differ only in vowels, so a consonant run is
a de-facto lemma class. Stripping Greek diacritics does less — it collapses
accent placement and breathing, not inflection — but there is no maintained
Koine stemmer, and a modern Greek stemmer trained on demotic will mangle Koine
morphology worse than doing nothing.

**Where this breaks down:** Hebrew prefixed particles (ו־, ה־, ב־, כ־, ל־, מ־)
and pronominal suffixes still produce distinct consonant runs, so דבר and ודבר
are separate forms. That over-counts vocabulary for a learner. If you need to
fix it, the right tool is a morphological analyzer with a lexicon, not a
rule-based stemmer — and it is a much bigger project than the 3-line strip.
Ship the strip, document the limit.

## Cross-runtime parity (Python ↔ JavaScript)

If you score server-side *and* in the browser, the two implementations must
agree token-for-token or users see different numbers in different places.

- Vendor the stemmer rather than picking whatever the JS ecosystem offers —
  a different Snowball port is a different stemmer.
- Put the per-language stopword lists in the exported data manifest, so the
  browser uses the exact list the pipeline used rather than its own copy.
- **Test one shared fixture on both sides**, asserting equality of the whole
  token list and of the derived ranks — not just spot checks.
- Head every mirrored file with a comment naming its counterpart and saying
  *change both sides together*. It is the only thing standing between you and a
  silent six-month drift.
- Sort ties by raw string comparison. `x < y` compares UTF-16 code units in JS
  and code points in Python — identical for every BMP script, which is all of
  these. **Do not "fix" this with `localeCompare`**: it is locale-dependent and
  will break parity on someone else's machine.

## RTL rendering

Drive direction from the content's language, declared once in config, and set it
on the element that holds text (`direction: rtl`), not globally. Hebrew and
Arabic get it; Greek does not. Mixed-direction UI (LTR chrome, RTL text) is the
normal case — scoping it to the text cell is what keeps the surrounding layout
sane.

## Traps

1. **Diacritic-insensitive search must strip the needle too.** Build a
   `text_plain` column at load time *and* normalize the query. Stripping only
   one side returns zero results for every accented query, and it looks like the
   search is broken rather than the normalization.
2. **NFC vs NFD equality.** Two visually identical Greek or Hebrew strings can
   compare unequal. Normalize before comparing, and pick one form for stored
   data; NFD then strip is the safest order.
3. **Source-format artifacts.** Morphologically tagged texts carry markup inside
   the words — morphhb's OSIS uses `/` as a morpheme separator, so `בְּ/רֵאשִׁית`
   tokenizes as two words if you don't strip it at conversion time. Inspect real
   token output from a real file before trusting a converter.
4. **Latin-confusable Unicode linters.** Ruff's RUF001/002/003 (and equivalents)
   flag Cyrillic, Greek and Hebrew characters as suspicious lookalikes. In a
   multilingual corpus those characters are the subject matter — ignore the rule
   permanently and say why in the config, rather than leaving 80+ standing false
   positives that train everyone to ignore the linter.
5. **No word segmentation.** Chinese, Japanese and Thai have no whitespace word
   boundaries, and `\w+` treats a whole run as one token. The fallback path
   silently produces garbage for them. Either integrate a segmenter or refuse
   those languages explicitly — do not let them fall through quietly.
6. **Empty tokens after stripping.** A "word" consisting entirely of marks
   normalizes to the empty string. Drop those, and know that dropping them means
   your token count can disagree with the source's word count — which matters if
   anything downstream maps tokens back to display words 1:1.
