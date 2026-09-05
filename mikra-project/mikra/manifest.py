"""manifest.json: the single source of truth for pipeline state."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

MANIFEST_NAME = "manifest.json"


def _path(root: Path) -> Path:
    return root / MANIFEST_NAME


def load(root: Path) -> dict:
    p = _path(root)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "chapters": {}, "errors": []}


def save(root: Path, manifest: dict) -> None:
    manifest["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _path(root).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def chapter_entry(manifest: dict, chap_id: str) -> dict:
    return manifest["chapters"].setdefault(chap_id, {})


def add_error(manifest: dict, stage: str, message: str) -> None:
    manifest["errors"].append(
        {"stage": stage, "message": message, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    )
