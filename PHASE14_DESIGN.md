# Phase 14 — Mobile-first UI: simple, attractive, Android-friendly

Restyle the static reader (`site/` only; `dash_app.py` untouched) into a
clean, phone-first app. Depends on Phase 13 (the DOM it styles: level slider,
learn-next chips, advanced `<details>`). No behavior changes — this phase is
HTML structure, CSS, and PWA metadata only. Any JS edits are limited to
class-name hooks explicitly listed here.

## D1 Design language (locked)

- System font stack, generous whitespace, one accent color, cards instead of
  a data grid on phones. No CSS framework, no icon font, no external assets
  (GitHub Pages + offline-cacheable later).
- Colors as CSS custom properties on `:root`, with a
  `@media (prefers-color-scheme: dark)` override:

```css
:root {
  --bg: #ffffff;      --fg: #1b1f27;    --muted: #6b7280;
  --card: #f7f8fa;    --border: #e5e7eb;
  --accent: #4f46e5;  --accent-weak: #eef2ff;
  --readable-bg: #f0fdf6; --readable-edge: #10b981;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1115;    --fg: #e5e7eb;    --muted: #9aa1ad;
    --card: #171a21;  --border: #262a33;
    --accent: #818cf8; --accent-weak: #23264d;
    --readable-bg: #10231a; --readable-edge: #34d399;
  }
}
```

- Type scale: base `16px`; verse text `1.05rem/1.65`; headings `1.25rem`
  (h1) and `1rem` bold (section labels); metadata `0.8rem` `--muted`.
- Every tappable control (buttons, chips, select, slider thumb, checkboxes'
  hit area, table rows' play button) ≥ **48×48 px** effective target:
  `min-height: 48px` on buttons/chips/selects; `padding: 12px` minimum.
- The page must never scroll horizontally at 360 px width. Wide content
  wraps; nothing gets `white-space: nowrap` except the difficulty badge.

## P14.1 index.html: structure + Android metadata

Head additions (keep the existing viewport meta):

```html
<meta name="theme-color" content="#4f46e5">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="icon.svg">
```

Body structure (reorder existing elements — do not rename ids):

1. `<header class="topbar">`: app title (shortened to **“Graded Reader”**) on
   one line with the Bible `<select>` beside it; the long explainer sentence
   moves into a `title=` tooltip / small muted line under the level label.
2. `<main>`: level slider panel → learn-next chips → search input → verse
   list → pagination → advanced `<details>`.
3. The topbar is `position: sticky; top: 0` with `background: var(--bg)` and
   a bottom border, so Bible switching and the slider stay reachable while
   scrolling. Only the topbar is sticky — the slider panel scrolls away (on
   a 640-px-tall phone screen real estate wins).

Create `site/manifest.webmanifest` exactly:

```json
{
  "name": "Graded Bible Reader",
  "short_name": "Reader",
  "start_url": ".",
  "display": "standalone",
  "background_color": "#0f1115",
  "theme_color": "#4f46e5",
  "icons": [{ "src": "icon.svg", "sizes": "any", "type": "image/svg+xml",
              "purpose": "any" }]
}
```

Create `site/icon.svg` exactly (indigo rounded square, open-book glyph):

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" rx="22" fill="#4f46e5"/>
  <path d="M50 30c-6-5-16-7-24-6v42c8-1 18 1 24 6 6-5 16-7 24-6V24c-8-1-18 1-24 6z"
        fill="none" stroke="#fff" stroke-width="5" stroke-linejoin="round"/>
  <line x1="50" y1="30" x2="50" y2="72" stroke="#fff" stroke-width="5"/>
</svg>
```

No service worker in this phase (offline caching of multi-MB bible JSONs
needs its own design; installable-without-offline is fine for Android).

## P14.2 style.css: rewrite

Replace `site/style.css` wholesale with the design above. Required rules
beyond D1's tokens:

- Layout: `body { max-width: 720px; margin: 0 auto; padding: 0 16px 48px;
  background: var(--bg); color: var(--fg); }`.
- **Verse list as cards on phones.** At `max-width: 700px`: hide the table
  header (`thead { display: none; }`); make `tr` a card — `display: block;
  background: var(--card); border: 1px solid var(--border); border-radius:
  12px; padding: 12px 14px; margin: 10px 0;`; `td { display: block; border:
  none; padding: 2px 0; }`; ref + difficulty badge on the first line
  (`0.8rem`, `--muted`), verse text full-width beneath, the rate/unknown
  numbers and read-checkbox collapsed onto one muted footer line (`td.rate {
  display: inline-block; margin-right: 12px; }`). Above 700 px keep the
  table but restyle: borderless rows, `border-bottom: 1px solid
  var(--border)`, same badge styling.
- Readable vs beyond (classes from P13.2): `tr.readable { border-left: 4px
  solid var(--readable-edge); background: var(--readable-bg); }` (on desktop
  rows: `background` only); `tr.beyond { opacity: 0.5; }`.
- Level slider: full-width; `input[type=range]` restyled with an
  `accent-color: var(--accent)` (sufficient — no vendor pseudo-element
  styling); label line bold with the count line beneath in `--muted`.
- Learn-next chips: `display: flex; flex-wrap: wrap; gap: 8px;`;
  `.chip { background: var(--accent-weak); color: var(--accent); border:
  none; border-radius: 999px; padding: 12px 18px; font-size: 1rem;
  min-height: 48px; }`; active/hover darkens. RTL bibles: the chips row gets
  `dir="rtl"` (set in app.js `renderLearnNext` from the existing `rtlLangs`
  check — the one permitted JS edit besides class hooks).
- Buttons/selects/inputs: `min-height: 48px; border-radius: 10px; border:
  1px solid var(--border); background: var(--card); color: var(--fg);
  font-size: 1rem; padding: 0 14px;`. Primary buttons (`find-passage`,
  chips) use accent colors.
- `details#advanced summary { min-height: 48px; display: flex; align-items:
  center; color: var(--muted); cursor: pointer; }`.
- Keep/port the existing karaoke styles (`span.w`, `.wplay`, `.pause`,
  `tr.playing`, audio panel) — restyle to tokens (`.wplay { background:
  var(--accent-weak); }` etc.) but do not drop them.
- Focus visibility: `:focus-visible { outline: 2px solid var(--accent);
  outline-offset: 2px; }`.

## P14.3 Copy pass (simple)

- Title: “Graded Bible Reader” → topbar shows “Graded Reader”.
- Subtitle (single muted line under the level label): “Drag the slider from
  simple to hard — verses unlock as your vocabulary grows.”
- Vocab textarea label: “Words you already know (optional)”.
- Learn-next heading: “Learn next” with muted suffix “tap a word once you
  know it”.
- Remove all other explanatory paragraphs from the page.

## P14.4 Acceptance checks

Automated (extend the Playwright smoke from P13.5 or create
`scratchpad/mobile_check.py` following the same serve-and-drive pattern):

- Viewport 360×800: `document.documentElement.scrollWidth <= 360` (no
  horizontal scroll) after loading the largest bible in `bibles.toml`.
- `getBoundingClientRect().height >= 48` for: a learn-next chip, the bible
  select, one pagination button.
- Dark mode: emulate `prefers-color-scheme: dark`
  (`page.emulate_media(color_scheme="dark")`) and assert `body`'s computed
  background-color equals `rgb(15, 17, 21)`.
- `manifest.webmanifest` and `icon.svg` return 200 from the local server and
  the manifest link tag resolves.

Manual (document results in the PR): open on an Android phone (or Chrome
device emulation), confirm the page is installable (“Add to Home screen”
shows the icon and name), the slider is draggable with a thumb, chips are
comfortably tappable, and Hebrew (wlc) renders RTL correctly in cards and
chips.

## Definition of done

- All P14.4 automated checks pass; site loads with zero JS console errors in
  both color schemes.
- `python scripts/export_static.py` output unchanged (this phase ships no
  data-format changes) and all Phase 12/13 tests stay green.
- CI deploy publishes the new look; verify https://b0001.github.io/bible/
  after merge.
