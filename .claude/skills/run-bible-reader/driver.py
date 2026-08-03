#!/usr/bin/env python3
"""Launch and drive the bible reader's two front ends from a terminal.

Both surfaces are browser apps with no server-rendered content worth reading:
the static reader (site/) scores verses in client-side JS, and the Dash app
paints its table from a callback. curl gets you an empty shell from either, so
this drives real headless Chromium via Playwright.

    ./driver.py site                    build nothing, serve site/, drive the reader
    ./driver.py dash                    launch dash_app.py, drive the table
    ./driver.py shot URL OUT.png        ad-hoc screenshot of anything
    ./driver.py eval URL 'JS'           ad-hoc JS eval, prints the JSON result

`site` and `dash` start their own server on a free port, drive it, write a
screenshot under out/run/, print a PASS/FAIL line per check, and shut down.
Pass --keep to leave the server running (it prints the URL and waits for
Ctrl-C) when you want to poke at it yourself with `shot`/`eval`.

Requires: pip install -e '.[e2e]' && playwright install chromium
"""

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SITE = os.path.join(ROOT, "site")
SHOTS = os.path.join(ROOT, "out", "run")

_failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    if not ok:
        _failures.append(label)
    return ok


def free_port():
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url, timeout=60):
    """Poll until the URL answers, so we never race a cold server."""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True  # answering at all is enough
        except OSError:
            time.sleep(0.5)
    return False


@contextlib.contextmanager
def browser(width=1280, height=900):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            yield page, errors
        finally:
            br.close()


def shoot(page, name):
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, name)
    page.screenshot(path=path, full_page=False)
    print(f"  shot  {os.path.relpath(path, ROOT)}")
    return path


# ---------------------------------------------------------------------------
# site — the static reader
# ---------------------------------------------------------------------------

def cmd_site(args):
    site = os.path.abspath(args.site_dir or SITE)
    manifest = os.path.join(site, "data", "manifest.json")
    if not os.path.exists(manifest):
        sys.exit(f"{manifest} missing — run: python scripts/export_static.py")

    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}/"
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=site, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_http(url):
            sys.exit("static server never came up")
        print(f"serving {url}")
        if args.keep:
            print("--keep: Ctrl-C to stop")
            server.wait()
            return

        with browser(args.width, args.height) as (page, errors):
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # The reader paints asynchronously after scoring the whole corpus.
            page.wait_for_function(
                "document.querySelectorAll('#reader .reader-text, #reader .reader-empty')"
                ".length > 0",
                timeout=60000,
            )
            bibles = page.eval_on_selector("#bible-select", "el => el.options.length")
            check("bible dropdown populated", bibles > 0, f"{bibles} translations")

            head = page.eval_on_selector("#reader .reader-head", "el => el.textContent")
            count = page.eval_on_selector("#reader-count", "el => el.textContent")
            check("read route auto-renders a passage", bool(head), f"{head!r} / {count!r}")

            chips = page.eval_on_selector_all("#learn-next .chip", "els => els.length")
            check("learn-next chips render", chips > 0, f"{chips} chips")
            shoot(page, "site-read.png")

            # Done marks the passage read and advances the queue — the core loop.
            page.click("#reader-done")
            advanced = True
            try:
                page.wait_for_function(
                    "document.getElementById('reader-count').textContent"
                    ".startsWith('Passage 2 of')",
                    timeout=15000,
                )
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                advanced = False
                print(f"        {type(exc).__name__}")
            progress = page.eval_on_selector("#progress", "el => el.textContent")
            check("Done advances the queue", advanced, progress)

            # The level slider re-scores everything and rebuilds the queue.
            page.fill("#level", "100")
            page.dispatch_event("#level", "input")
            page.wait_for_function(
                "document.getElementById('reader-count').textContent"
                ".startsWith('Passage 1 of')",
                timeout=15000,
            )
            meta = page.eval_on_selector("#reader .reader-meta", "el => el.textContent")
            check("level slider rebuilds the queue", "verses" in meta, meta)

            page.evaluate("location.hash = '#browse'")
            page.wait_for_function(
                "!document.getElementById('route-browse').hidden", timeout=5000)
            rows = page.eval_on_selector_all("#verse-body tr", "els => els.length")
            check("browse route lists verses", rows > 0, f"{rows} rows")
            shoot(page, "site-browse.png")

            page.evaluate("location.hash = '#read'")
            check("no JS errors", errors == [], "; ".join(errors[:3]))
    finally:
        server.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            server.wait(timeout=5)


# ---------------------------------------------------------------------------
# dash — the local power tool
# ---------------------------------------------------------------------------

def cmd_dash(args):
    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}/"
    env = {**os.environ, "DASH_PORT": str(port), "DASH_HOST": "127.0.0.1"}
    os.makedirs(SHOTS, exist_ok=True)
    log_path = os.path.join(SHOTS, "dash.log")

    with open(log_path, "w") as log:
        server = subprocess.Popen(
            [sys.executable, "dash_app.py"], cwd=ROOT, env=env, stdout=log, stderr=log)
    try:
        if not wait_http(url + "health"):
            sys.exit(f"dash never became healthy — see {log_path}")
        print(f"serving {url}")
        if args.keep:
            print("--keep: Ctrl-C to stop")
            server.wait()
            return

        with browser(args.width, args.height) as (page, errors):
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Dash paints the table from a callback, so the shell arrives empty.
            page.wait_for_function(
                "document.querySelectorAll('#table td').length > 0", timeout=60000)
            cells = page.eval_on_selector_all("#table td", "els => els.length")
            count = page.eval_on_selector("#count", "el => el.textContent")
            check("verse table renders", cells > 0, f"{cells} cells / {count!r}")

            # dcc.Dropdown is not a <select> — select_option() does nothing.
            # Click it open, then click the option by label. Dash 3.x renders
            # options as .dash-options-list-option-text OUTSIDE #bible-select;
            # older React-Select builds used .Select-option inside it.
            if args.bible:
                page.click("#bible-select")
                page.click(
                    f".dash-options-list-option-text:has-text('{args.bible}'), "
                    f".Select-option:has-text('{args.bible}')",
                    timeout=10000,
                )
                page.wait_for_function(
                    "document.querySelectorAll('#table td').length > 0", timeout=60000)
            label = page.eval_on_selector("#bible-select", "el => el.textContent")
            check("bible selector usable", bool(label), label.strip()[:60])

            before = page.eval_on_selector("#passage-panel", "el => el.textContent")
            page.click("#find-passage")
            filled = True
            try:
                page.wait_for_function(
                    "document.getElementById('passage-panel').textContent.length > "
                    f"{len(before)}",
                    timeout=30000,
                )
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                filled = False
                print(f"        {type(exc).__name__}")
            panel = page.eval_on_selector("#passage-panel", "el => el.textContent")
            check("find-passage returns a span", filled, panel[:90])
            if "No passage" in panel:
                print("        note: that is the app working — the graded CSV for this")
                print("        Bible has no span at >=95%. See Gotchas in SKILL.md.")

            # Clicking scrolled us to the bottom; shoot the top deliberately,
            # then the panel, rather than whatever happened to be in frame.
            page.evaluate("window.scrollTo(0, 0)")
            shoot(page, "dash.png")
            # Only shoot the panel when it holds a real span — "No passage"
            # also grows the text, and a screenshot of that is just noise.
            if filled and "Longest passage" in panel:
                page.eval_on_selector(
                    "#passage-panel", "el => el.scrollIntoView({block: 'center'})")
                shoot(page, "dash-passage.png")
            check("no JS errors", errors == [], "; ".join(errors[:3]))
    finally:
        server.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            server.wait(timeout=5)


# ---------------------------------------------------------------------------
# ad-hoc
# ---------------------------------------------------------------------------

def cmd_shot(args):
    with browser(args.width, args.height) as (page, _):
        page.goto(args.url, wait_until="networkidle", timeout=60000)
        if args.wait:
            page.wait_for_function(args.wait, timeout=60000)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        page.screenshot(path=args.out, full_page=args.full)
        print(f"shot  {args.out}")


def cmd_eval(args):
    with browser(args.width, args.height) as (page, _):
        page.goto(args.url, wait_until="networkidle", timeout=60000)
        if args.wait:
            page.wait_for_function(args.wait, timeout=60000)
        print(json.dumps(page.evaluate(args.expr), ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=900)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn in (("site", cmd_site), ("dash", cmd_dash)):
        p = sub.add_parser(name)
        p.add_argument("--port", type=int)
        p.add_argument("--keep", action="store_true")
        if name == "dash":
            p.add_argument("--bible", help="switch to this Bible by label substring")
        else:
            p.add_argument("--site-dir", help="serve this dir instead of site/")
        p.set_defaults(fn=fn)

    p = sub.add_parser("shot")
    p.add_argument("url")
    p.add_argument("out")
    p.add_argument("--wait", help="JS predicate to wait for before shooting")
    p.add_argument("--full", action="store_true")
    p.set_defaults(fn=cmd_shot)

    p = sub.add_parser("eval")
    p.add_argument("url")
    p.add_argument("expr")
    p.add_argument("--wait")
    p.set_defaults(fn=cmd_eval)

    args = ap.parse_args()
    args.fn(args)
    if _failures:
        sys.exit(f"\n{len(_failures)} check(s) failed: {', '.join(_failures)}")
    if args.cmd in ("site", "dash"):
        print("\nall checks passed")


if __name__ == "__main__":
    main()
