---
name: derived-dataset-publishing
description: >-
  Publish derived metrics from a corpus you don't own the rights to, with a join
  key that survives structural variation between sources. Use when releasing a
  dataset to HuggingFace or similar, deciding which columns are safe to ship,
  handling copyrighted or licensed source material, joining records across
  sources whose canon/schema/ordering differ, choosing CSV vs Parquet, or
  writing a dataset card. Covers the reconstructability test, the
  observer-dependence test, loud-failing positional mapping, and Hub layout.
---

# Publishing derived data

How to release measurements computed from material you cannot redistribute, in a
form other people can actually join against. Extracted from a live dataset of
~325k rows across 12 translations in 11 languages, including copyrighted ones.

## 1. Publish the measurement, never the measured

Ship **refs and derived metrics; no source text**. A reference (`"John 3:16"`) is
an address in a numbering system — a fact, not an expression. A token count and a
difficulty rank are numbers computed *from* the text, not fragments *of* it.
Together they carry no reconstructable expression, so they are facts about a
work rather than the work.

**The line is reconstructability.** Apply this test to every candidate column:
*could a determined consumer reassemble a meaningful portion of the source from
the published columns alone, possibly by joining several of them?* If yes, drop
one — usually the column carrying identity or sequence rather than magnitude.

Concretely, in the source project all of these existed in the pipeline and are
absent from the export:

- the text itself
- the per-item token list (a bag of stems is lossy but *substantial*
  reproduction)
- **the rank → word mapping.** Ranks ship; the vocabulary that gives them
  meaning does not. That one-way projection is what makes the output
  non-invertible, and it is the detail people miss.

**Make the exclusion structural, not procedural.** Do not `cp` a source file and
trust that you vetted it. Parse it into a named schema and re-emit only listed
fields. Then a new field appearing upstream *cannot* leak — it simply is not in
the writer. The same project applies this to licensed audio: alignment timings
are re-serialized from parsed JSON, never copied, so no audio bytes can ever
land in the published tree.

Three licenses, three separate questions: your **code**'s license, your
**dataset**'s license (CC BY 4.0 here), and the **upstream corpus**'s terms.
State the dataset's own license in the card, and put "no source text is
redistributed" in the card itself — not only in a commit message.

## 2. Which columns may ship: the observer test

**Would this value differ if a different person ran the same pipeline over the
same corpus?** If yes, it describes an observer, not the artifact. Keep it local.

| column | ships | why |
|---|---|---|
| `ref`, `book`, `chapter`, `verse` | yes | citation / address; facts |
| `total_count` | yes | function of (text, tokenizer); identical for every reader |
| `difficulty_rank` | yes | function of (text, corpus), computed with an **empty** known set |
| the text | no | §1 |
| `comprehension_rate` | **no** | intersection with one private vocabulary |
| `known_count` | **no** | same |

The secondary benefit is privacy: a published per-item `known_count` is a
fingerprint of the publisher's own vocabulary. One test disqualifies both the
meaningless-to-others columns and the accidentally-personal ones.

**Compute publishable metrics by calling the same canonical function the app
uses, with the observer-dependent parameter zeroed** —
`verse_difficulty(forms, ranks, known=frozenset())` — rather than writing a
second implementation. Then the published numbers match the product by
construction.

**Document scope in the same breath as the metric.** A vocabulary-independent
column can still be corpus-relative: these ranks are comparable *within* a
translation and meaningless *across* them. Say so, or users will average across
subsets and get nonsense.

**Determinism is a prerequisite.** Break ties explicitly (lexicographic, never
hash or insertion order). Only then can you sha256 a local rebuild against
what's live and *prove* a refactor changed nothing — which is a release gate
worth having.

## 3. The join key is the actual work

Two naive approaches, both wrong:

- **Join on name.** Dead across languages: `Genesis` / `Génesis` / `1. Mose` /
  `Бытие` / `تكوين` are one entity. Worse, the same name means *different*
  entities in different traditions.
- **Join on ordinal position.** The seductive one: it works for most sources and
  then silently corrupts the rest.

Position breaks because **membership and order are both variable**. In this
corpus: 27-, 39-, 66-, 67- and 78-entry canons; one source inserts Baruch
*mid-sequence* between Malachi and Matthew (so every one of the 27 following
books shifts by one — twenty-seven silently wrong labels, no error raised);
another interleaves deuterocanonical books throughout *and* orders the Catholic
epistles before the Pauline ones.

**The fix: an external abstract identifier, an explicit recorded sequence per
source, and loud validation.**

1. Adopt an abstract ID for the joined entity (here: USFM book codes). Not a
   name (varies by language), not an ordinal (varies by edition).
2. Enumerate every structural variant your sources exhibit *before* writing the
   mapper, and write them down as explicit ordered lists.
3. Verify the assumption once, manually, at the point most likely to break — a
   seam or boundary index — and record the check in a comment. ("Verified all
   seven 66-book translations really are in standard order: index 38 Malachi,
   39 Matthew.")
4. Guard with failures that **exit**, each carrying its remediation in the
   message:
   - **source not in the mapping table** → refuse to export. A new source cannot
     be published by accident; it must be curated in.
   - **count mismatch** → refuse: *"found N entries, table has M. The source's
     canon changed — update the table."*
   - **non-contiguous runs** → refuse: *"a name appears in more than one run, so
     records are not grouped. Positional mapping is unsafe here."* This is the
     precondition check for positionality itself.
5. Assert wide variants are strict **supersets** of narrow ones, so absence
   yields blanks rather than dropped rows — and say in the card that **a blank
   means absent, not missing**.
6. Assert no duplicate IDs. A repeat silently merges two entities on join.
7. Add a test that your config file and your mapping table cannot drift apart.
8. Parse refs **from the right** when the variable-length part is on the left
   (`1 Samuel 3:16` — `rpartition` on `:` then on the last space). Test the
   degenerate no-separator case; that found a real bug here, where a ref with no
   colon lost the book name entirely.

## 4. When the source's labels lie

The worked example, and the best argument for the whole design.

The Russian Synodal canon has three books named *Ездры*. The source labelled
them **one number lower** than the canon does:

| Synodal name | source label | actually is | code |
|---|---|---|---|
| 1 Ездры | `Ездры` | Ezra | `EZR` |
| — | `Неемии` | Nehemiah | `NEH` |
| 2 Ездры | `1-я Ездры` | 1 Esdras (Greek) | `1ES` |
| 3 Ездры | `2-я Ездры` | 2 Esdras (4 Ezra) | `2ES` |

It drops the "1" from canonical Ezra, then restarts numbering from 1 for the
deuterocanonical pair — so its `N-я Ездры` is the canon's `N+1`. Take the labels
at face value and both land one book too early.

**Three kinds of evidence about identity, ascending in trustworthiness:**

1. **Labels** — what the source *calls* a thing. Cheapest to read, and the only
   channel that can be *confidently, self-consistently wrong*. A source can
   renumber, abbreviate, translate, or inherit another tradition's convention,
   and every label still looks plausible.
2. **Position / structure** — where it sits among its neighbours. Harder to
   fake, because it must be consistent with everything around it. But brittle:
   needs a recorded expected order and the guards in §3.
3. **Independent corroboration** — a *differently sourced* attribute implying
   the same identity. Here, curated Greek LXX titles (added for an unrelated
   reason) voted with position against labels: `1ES` is Ἔσδρας Α΄, `EZR` is
   Ἔσδρας Β΄, and `2ES` is blank because 4 Ezra survives in Latin and Syriac,
   not Greek. The Greek naming tradition has no causal link to the source's
   Russian numbering, so agreement is real evidence rather than a shared error.

**Operationally: when labels and structure disagree, do not pick.** Encode the
structural answer, ship the uncertainty as a documented caveat in the card, and
go find a third signal. That is literally what happened here — two commits apart,
with a period of published uncertainty in between.

Then: pin it with a test whose docstring explains what the trap was ("pins the
positions so the labels cannot talk a future editor into shifting them"), replace
the caveat with the finding, and **publish the mapping table in the card** so
consumers never rediscover it.

**A bare number in a label is a smell.** Any "N-th X" scheme is a candidate
off-by-one, because traditions start counting in different places.

## 5. Format follows failure mode

Not size, not speed — **who finds the errors**.

- **Data whose errors are found by humans → CSV / plain text.** The 78-row book
  table is hand-curated across languages the author doesn't all read. Its
  correctness depends on strangers spotting mistakes, so a format that renders
  as a diff in a pull request is a *correctness mechanism*. Parquet would have
  been technically fine and socially useless.
- **Data whose errors are found by machines → Parquet, zstd, explicit int
  dtypes.** ~325k rows: **1.5 MB vs ~40 MB as CSV**, ~27×. It's the Hub's native
  storage (the viewer converts everything anyway), it keeps int dtypes CSV would
  flatten to text, and — the critical one here — **it is UTF-8 by spec**. With
  Hebrew niqqud, Arabic and polytonic Greek in a `ref` column, CSV's BOM-or-not,
  Excel cp1252 mangling, and dialect-dependent quoting are real failure modes
  that simply don't exist in Parquet. For non-Latin scripts this is a
  correctness requirement, not a preference.

Mixed formats across subsets of one dataset are fine.

## 6. Curated vs source-faithful columns

Keep two families with **explicitly different contracts**:

| family | contract | property of |
|---|---|---|
| curated (`english`, `hebrew`, `greek`) | what this entity is actually called in that language | the **language** |
| source-faithful (`nasb`, `wlc`, `gnt`, …) | the string exactly as it appears in that source's refs | the **source** |

The Hebrew and Greek columns of the table originally held Latin abbreviations
(`Gen`, `Matt`) because that is what those source files ship. The obvious fix —
rewrite them with real Hebrew and Greek names — was **rejected**: a
source-faithful column's contract is not "the name of this thing", it is "the
exact substring that appears at the start of every ref in the corresponding
Parquet file". Substituting `בראשית` for `Gen` makes the table prettier and makes
the join return zero rows.

**Never "improve" a source-faithful column. Add a new one.** A cosmetic
improvement that severs a structural link is a regression.

**Prefer omitting to inventing.** The Hebrew names are deliberately unvocalized:
unvocalized is the standard citation form, hand-transcribed diacritics are a
steady source of errors that are *silent* to anyone who can't read pointed
Hebrew, and nothing downstream depends on the pointing anyway. **Wrong data is
worse than absent data, because absent data announces itself.** Document what
each blank means.

Validate curated reference tables with cheap oracles that don't require knowing
the right answer: is every character in the expected Unicode block (this catches
the exact Latin-abbreviation regression above); are there duplicates (two
entities sharing a name means one is mislabelled); are the blanks *exactly* the
intended set. And pin counterintuitive-but-correct entries with a comment saying
why they look wrong, or a well-meaning editor will "fix" them.

## 7. HuggingFace mechanics

```
out/dataset/
├── README.md                      # YAML front-matter = dataset card + config
├── books.csv
└── data/<config_id>/train-00000.parquet
```

```yaml
configs:
  - config_name: nasb
    data_files: data/nasb/train-*.parquet
  - config_name: books
    data_files: books.csv
license: cc-by-4.0
language: [ar, de, el, en, es, fr, he, it, nl, pt, ru]
```

Three conventions doing real work in that path:

- **`data/<id>/`** — the directory is the config; `data_files` globs it.
- **`train-` prefix** — the Hub infers the *split name* from the filename. Even a
  single-split dataset must say `train`.
- **`-00000` suffix** — the shard-index convention. The `train-*` glob means
  growing past one shard needs no card change. Costless future-proofing.

**One config per subset that shouldn't be naively concatenated.** These aren't
union-compatible in any meaningful sense — the ranks are corpus-relative —
so separate configs make that structurally obvious and give the viewer a
dropdown.

**Generate the `configs:` block and the `language:` list from your manifest**, so
metadata derived from data cannot drift.

Before pushing, check: front-matter is valid YAML; every `data_files` glob
matches at least one committed file (a config pointing at nothing fails the whole
card); schema is uniform within a config; config names are unique; split names
resolve.

Keep the Hub CLI an **optional extra**, not a core dependency — your serving
image has no use for it — and keep the actual push a deliberate human step
rather than a CI job. The export should be reproducible and hash-checkable; the
publish should be a decision.
