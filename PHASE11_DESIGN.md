# Phase 11 — Cleanup (ponytail audit)

Delete dead weight found by the 2026-07-13 over-engineering audit. Every task
is a deletion or a behavior-preserving shrink. **No new features.** After each
task run `.venv/bin/ruff check .` and `.venv/bin/python -m pytest` from the
repo root; both must pass before moving to the next task.

Implementation order: P11.1 → P11.7 as numbered. Each task is one commit.

## P11.1 Delete unused vendored stemmers

`scripts/export_static.py` only maps stemmers for languages present in
`bibles.toml` (`en es de fr ru pt it nl ar` — plus `he`/`el` which use no
stemmer). Six vendored files are therefore unreachable.

- Delete exactly these files:
  - `site/vendor/danish-stemmer.js`
  - `site/vendor/finnish-stemmer.js`
  - `site/vendor/hungarian-stemmer.js`
  - `site/vendor/norwegian-stemmer.js`
  - `site/vendor/romanian-stemmer.js`
  - `site/vendor/swedish-stemmer.js`
- Do NOT delete `base-stemmer.js` (shared base class) or any other vendor file.
- Verify nothing references them: `grep -rn "danish\|finnish\|hungarian\|norwegian\|romanian\|swedish" --include="*.js" --include="*.py" .` must return no hits outside `.venv`.

## P11.2 Delete Spark-era infra scripts

The Spark/EC2 cluster was removed (SPEC.md §4); these helpers reference it and
nothing else. `minio-start.sh` points at a `minio-deployment.yaml` that does
not exist in the repo.

- Delete: `start.py`, `aws-builder-userdata.sh`, `gcp-builder-userdata.sh`,
  `minio-start.sh`.
- In `CLAUDE.md`: remove the paragraph beginning "`start.py` is unrelated to
  the app logic" (including its ```bash block), and remove the sentence in
  the "Deployment / infra" section that lists `aws-builder-userdata.sh`,
  `gcp-builder-userdata.sh`, `minio-start.sh`, `start.py`.
- Search `SPEC.md` and `README.md` for `start.py`, `minio`, `userdata` and
  delete any sentences mentioning them.

## P11.3 Remove s3:// support

No `s3://` URI appears anywhere in config, scripts, CI, or docs. The pipeline
is single-machine by design.

- `parser.py`: in `_open_write` (≈line 700) delete the
  `if path.startswith("s3://"):` branch and the fsspec import inside it; keep
  the local-path body (expanduser, makedirs, open). Update its docstring to
  "Open a path for binary writing; create parent dirs."
- `dash_app.py`: in `load_graded_raw` (≈line 59) delete the s3 branch the same
  way; update docstring.
- `pyproject.toml`: delete the line `s3 = ["s3fs>=2023.6"]`.
- `SPEC.md`: delete the sentence at ≈line 312 saying paths accept `s3://`
  URIs.
- Check tests: `grep -n "s3\|fsspec" test_*.py` — if any test exercises the
  s3 branch, delete that test.

## P11.4 Replace hand-rolled cosine with spaCy similarity

`SemanticModel` (parser.py ≈line 375) stores raw vectors and computes cosine
with numpy. spaCy ships `Doc.similarity`, which is the same cosine.

- In `SemanticModel.__init__`: store `self._known_docs = [doc for word in
  profile_surface_words for doc in [nlp(word.lower())] if doc.has_vector and
  doc.vector_norm > 0]` (list of Docs, not vectors).
- In `SemanticModel.credit`: build `doc = self._nlp(surface_token.lower())`;
  return 0.0 if `not doc.has_vector or doc.vector_norm == 0 or not
  self._known_docs`; else `max_sim = max(float(doc.similarity(kd)) for kd in
  self._known_docs)`; return `_SIM_WEIGHT * max_sim if max_sim >= _SIM_TAU
  else 0.0`.
- Delete the `import numpy as _np` from the `try` block at the top (keep the
  `_spacy` import; `_SPACY_AVAILABLE` now only gates spacy).
- Guard: the existing tests marked `@pytest.mark.semantic` must still pass
  (`pytest -m semantic` with the `[semantic]` extra installed). If the extra
  is not installed locally, at minimum `pytest` (which skips them) and
  `ruff check .` must pass.

## P11.5 Delete `grade()` and `stem_tokens()`

Neither has a production caller. `main()` computes rates inline;
`stem_tokens(text)` is a one-line alias for `tokenize_and_stem(text, "en")`.

- `parser.py`: delete `def grade(...)` (≈line 493) and `def stem_tokens(...)`
  (≈line 144).
- `test_parser.py`: remove `grade` from the `from parser import (...)` list;
  delete the test using `grade(` (≈line 184, `out = grade(df, vocab)` and its
  enclosing test function). If that test also asserts things about
  `comprehension_rate`, keep those assertions by rewriting them against
  `comprehension_rate` directly.
- `test_static_export.py` line 10: change
  `from parser import stem_tokens, tokenize_and_stem` to
  `from parser import tokenize_and_stem`, and replace every `stem_tokens(x)`
  call in that file with `tokenize_and_stem(x, "en")`.
- `CLAUDE.md`: in the parser.py section, remove `stem_tokens` and `grade` from
  the "Key functions" sentence.

## P11.6 Drop the dead `en_stopwords` manifest key

`site/app.js` reads `manifest.stopwords`; nothing reads `en_stopwords`.

- `scripts/export_static.py`: delete the line `"en_stopwords":
  english_stopwords(),` from the manifest dict, and delete the
  `def english_stopwords():` helper.
- `test_static_export.py` ≈line 54 (`test_manifest_stopwords_match_nltk`):
  replace `mod.english_stopwords()` with
  `mod.stopwords_for_langs(["en"])["en"]`.
- Re-run the export afterwards to confirm it still works:
  `.venv/bin/python scripts/export_static.py` (bible sources may be missing
  locally — "skipping" lines are fine; the command must exit 0).

## P11.7 Drop the dead initial table styles in dash_app

`update_table` returns `_cell_styles(...)` on every callback including the
first, so the `style_cell_conditional=[...]` literal in the layout
(dash_app.py ≈line 404) is overwritten before anyone sees it.

- Delete the `style_cell_conditional=[...]` argument from the
  `dash_table.DataTable(...)` call in the layout. Keep `style_cell`.
- Sanity check: `.venv/bin/python -c "import dash_app"` exits 0 (set
  `BIBLE_GRADED_CSV=out/nasb_graded.csv` or any existing graded CSV first;
  `out/nasb_graded.csv` exists if the sample has been graded — if not, run
  the sample grading command from CLAUDE.md).

## Out of scope

- `scripts/build_english_poset.py` is superseded and deleted in Phase 12 (its
  replacement lands there) — do not touch it here.
- Merging `test_export_static.py` / `test_static_export.py` — skipped; the
  names are a trap but a merge risks losing coverage for no behavior gain.
- No changes to CI, Dockerfile, requirements.txt, or `site/app.js`.

## Definition of done

- All deleted files gone; `git grep` finds no references to them.
- `ruff check .` clean; `pytest` green (81+ tests, minus the ones this phase
  explicitly deletes).
- `python scripts/export_static.py` exits 0 and `site/data/manifest.json` no
  longer contains `en_stopwords`.
