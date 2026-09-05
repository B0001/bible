# Mikra Sync

Word-synchronized "karaoke" reader for narrated Hebrew Bible audio.
Pipeline + player, desktop only. Full design in [SPEC.md](SPEC.md).

## Quickstart

```bash
# 1. Install (alignment deps are heavy; everything else runs without them)
pip install -r requirements.txt

# 2. Create the project layout at ~/Downloads/bible
python -m mikra scaffold

# 3. Unzip HBRHMTN1DA.zip into ~/Downloads/bible/raw/

# 4. Put matching Hebrew text into ~/Downloads/bible/text/
#    One file per chapter (Gen_01.txt), ONE VERSE PER LINE.
#    ⚠ Must be the SAME edition the narrator reads (HMT = Modern Hebrew).

# 5. Run the pipeline
python -m mikra rename
python -m mikra align --chapters Gen_01 --model medium   # quick sanity chapter
python -m mikra validate
python -m mikra status

# 6. Read
python -m mikra serve        # opens http://127.0.0.1:8737 in your browser
```

Then batch the rest once Gen_01 looks right:

```bash
python -m mikra align --model large-v3    # add --device cuda if you have a GPU
python -m mikra validate
```

## Player controls

| Action            | Effect                        |
|-------------------|-------------------------------|
| click a word      | seek audio to that word       |
| double-click      | loop that word (drill mode)   |
| Space             | play / pause                  |
| ← / →             | back / forward 2 s            |
| `-` / `=`         | playback speed                |
| Esc               | stop looping                  |
| *emphasis* toggle | duration heatmap              |
| *pauses* toggle   | show > 0.8 s gaps (‖)         |

## Tests

```bash
python tests/test_mikra.py     # or: python -m pytest tests/
```

## Layout

```
mikra/      pipeline package (rename, align, validate, site, cli)
web/        static player templates (copied into the root by `build`)
tests/      dependency-free unit tests
SPEC.md     full specification (v0.2)
```
