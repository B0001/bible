"""Build the web player into the root, serve it, and report status."""
from __future__ import annotations

import functools
import http.server
import shutil
import webbrowser
from pathlib import Path

from . import manifest

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
WEB_FILES = ("index.html", "app.js", "style.css")


def scaffold(root: Path) -> int:
    for d in ("raw", "audio", "text", "alignments"):
        (root / d).mkdir(parents=True, exist_ok=True)
    manifest.save(root, manifest.load(root))
    print(f"scaffold: ready at {root}")
    print("  1. unzip HBRHMTN1DA.zip into raw/")
    print("  2. put Hebrew text (one verse per line) into text/ as Gen_01.txt ...")
    return 0


def build(root: Path) -> int:
    for name in WEB_FILES:
        src = WEB_DIR / name
        if not src.exists():
            print(f"build: missing template {src}")
            return 1
        shutil.copy2(src, root / name)
    print(f"build: player written to {root} ({', '.join(WEB_FILES)})")
    return 0


class _NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # quieter server
        pass


def serve(root: Path, port: int = 8737, open_browser: bool = True) -> int:
    if not (root / "index.html").exists():
        build(root)
    handler = functools.partial(_NoCacheHandler, directory=str(root))
    url = f"http://127.0.0.1:{port}/"
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"serve: {url}  (Ctrl-C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nserve: stopped")
    return 0


def status(root: Path) -> int:
    m = manifest.load(root)
    by_book: dict[str, dict[str, int]] = {}
    for entry in m["chapters"].values():
        book = entry.get("book", "?")
        st = entry.get("status", "?")
        by_book.setdefault(book, {})[st] = by_book.setdefault(book, {}).get(st, 0) + 1
    if not by_book:
        print("status: manifest is empty — run `python -m mikra rename` first")
        return 0
    for book in sorted(by_book):
        parts = ", ".join(f"{v} {k}" for k, v in sorted(by_book[book].items()))
        print(f"  {book:<5} {parts}")
    if m.get("errors"):
        print(f"  ({len(m['errors'])} recorded errors — see manifest.json)")
    return 0
