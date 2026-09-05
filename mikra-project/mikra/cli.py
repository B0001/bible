"""Mikra Sync command-line interface. See SPEC.md §3."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import align, rename, site, validate

DEFAULT_ROOT = Path.home() / "Downloads" / "bible"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mikra", description="Hebrew Bible karaoke reader pipeline + player"
    )
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                   help=f"project root (default: {DEFAULT_ROOT})")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("scaffold", help="create the directory layout")
    sub.add_parser("rename", help="raw/ vendor files -> audio/Gen_01.mp3 ...")

    pa = sub.add_parser("align", help="forced-align text/ against audio/")
    pa.add_argument("--model", default="large-v3",
                    help="whisper model (large-v3, medium, small)")
    pa.add_argument("--device", default=None, help="cuda | cpu (auto if omitted)")
    pa.add_argument("--chapters", default=None,
                    help="prefix filter, e.g. 'Gen' or 'Gen_01'")
    pa.add_argument("--keep-niqqud", action="store_true",
                    help="keep vowel points in the alignment text")
    pa.add_argument("--force", action="store_true", help="re-align even if done")

    sub.add_parser("validate", help="sanity-check all alignments")
    sub.add_parser("build", help="write the web player into the root")

    ps = sub.add_parser("serve", help="serve the player locally")
    ps.add_argument("--port", type=int, default=8737)
    ps.add_argument("--no-browser", action="store_true")

    sub.add_parser("status", help="pipeline progress per book")

    args = p.parse_args(argv)
    root: Path = args.root.expanduser()

    if args.command == "scaffold":
        return site.scaffold(root)
    if not root.exists():
        print(f"error: {root} does not exist — run `python -m mikra scaffold` first")
        return 2

    if args.command == "rename":
        return rename.run(root)
    if args.command == "align":
        return align.run(root, model_name=args.model, device=args.device,
                         chapters=args.chapters, keep_niqqud=args.keep_niqqud,
                         force=args.force)
    if args.command == "validate":
        return validate.run(root)
    if args.command == "build":
        return site.build(root)
    if args.command == "serve":
        return site.serve(root, port=args.port, open_browser=not args.no_browser)
    if args.command == "status":
        return site.status(root)
    return 2


if __name__ == "__main__":
    sys.exit(main())
