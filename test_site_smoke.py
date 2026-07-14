"""Browser smoke test for the static reader (site/).

Drives the real site in headless Chromium and asserts the things a unit test
can't see: the module actually initializes (a single undeclared variable once
killed init() silently — see d33c792), the Bible dropdown populates, verses
render, learn-next chips work, and the P14 mobile acceptance checks hold
(no horizontal scroll at 360px, dark-mode background).

Requires `pip install '.[e2e]'` + `playwright install chromium`, and exported
site data (`python scripts/export_static.py`). Skips itself when either is
missing, so plain `pytest` stays green everywhere.
"""
import contextlib
import json
import os
import socket
import subprocess
import sys
import time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SITE = os.path.join(_HERE, "site")
_MANIFEST = os.path.join(_SITE, "data", "manifest.json")

pytestmark = pytest.mark.e2e

playwright_sync = pytest.importorskip(
    "playwright.sync_api", reason="playwright not installed (pip install '.[e2e]')"
)


def _free_port():
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site_url():
    if not os.path.exists(_MANIFEST):
        pytest.skip("site/data/manifest.json missing — run scripts/export_static.py first")
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=_SITE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.terminate()
        server.wait(timeout=5)


@pytest.fixture(scope="module")
def loaded_page(site_url):
    """A 360x800 page with the site fully initialized, plus collected JS errors."""
    with playwright_sync.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("chromium not installed — run: playwright install chromium")
        page = browser.new_page(viewport={"width": 360, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(site_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_function(
            "document.querySelectorAll('#verse-body tr').length > 0", timeout=60000
        )
        yield page, errors
        browser.close()


def test_no_js_errors(loaded_page):
    _, errors = loaded_page
    assert errors == []


def test_bible_dropdown_populates(loaded_page):
    page, _ = loaded_page
    with open(_MANIFEST, encoding="utf-8") as f:
        expected = len(json.load(f)["bibles"])
    options = page.eval_on_selector("#bible-select", "el => el.options.length")
    assert options == expected


def test_verses_and_learn_next_render(loaded_page):
    page, _ = loaded_page
    assert page.eval_on_selector_all("#verse-body tr", "els => els.length") > 0
    assert page.eval_on_selector_all("#learn-next .chip", "els => els.length") > 0


def test_no_horizontal_scroll_at_360(loaded_page):
    page, _ = loaded_page
    assert page.evaluate("document.documentElement.scrollWidth") <= 360


def test_learn_next_chip_tap_rescores(loaded_page):
    page, errors = loaded_page
    first = page.eval_on_selector("#learn-next .chip", "el => el.textContent")
    page.click("#learn-next .chip")
    page.wait_for_function(
        f"document.querySelector('#learn-next .chip')?.textContent !== {json.dumps(first)}",
        timeout=15000,
    )
    assert errors == []


def test_dark_mode_background(loaded_page):
    page, _ = loaded_page
    page.emulate_media(color_scheme="dark")
    bg = page.eval_on_selector("body", "el => getComputedStyle(el).backgroundColor")
    assert bg == "rgb(15, 17, 21)"
    page.emulate_media(color_scheme="light")
