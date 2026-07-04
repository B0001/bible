"""Tests for scripts/export_static.py's audio export (P10.3) — pure parts only.

The licensing-critical property: export_audio copies alignment *timings* into
site/data/audio/, never audio bytes.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))

from export_static import export_audio  # noqa: E402


def _fixture(tmp_path):
    """A D5 manifest + sidecar (+ a decoy .opus) under tmp_path; returns entry."""
    out = tmp_path / "out" / "audio" / "wlc"
    out.mkdir(parents=True)
    sidecar = out / "Gen_001.json"
    sidecar.write_text(json.dumps({
        "bible_id": "wlc", "book": "Gen", "chapter": 1,
        "audio": "data/audio/serve/Gen_001.opus", "duration": 10.0,
        "verses": [{"ref": "Gen 1:1", "start": 0.5, "end": 9.5, "confidence": 0.9}],
    }))
    # decoy audio file next to the sidecars — must never be copied
    (out / "Gen_001.opus").write_bytes(b"OpusHead-fake")
    manifest = tmp_path / "out" / "audio" / "wlc_manifest.json"
    manifest.write_text(json.dumps({
        "bible_id": "wlc", "audio_dir": "data/audio/serve",
        "chapters": [{"book": "Gen", "chapter": 1, "sidecar": str(sidecar)}],
    }))
    return {"id": "wlc", "audio_manifest": str(manifest)}


def test_export_audio(tmp_path):
    entry = _fixture(tmp_path)
    site_data = tmp_path / "site" / "data"
    site_data.mkdir(parents=True)
    rel = export_audio(entry, str(site_data), "../data/audio/serve")
    assert rel == "data/audio/wlc/index.json"

    index = json.loads((site_data / "audio" / "wlc" / "index.json").read_text())
    assert index["audio_base"] == "../data/audio/serve"
    assert index["chapters"] == {"Gen 1": "Gen_001.json"}

    copied = json.loads((site_data / "audio" / "wlc" / "Gen_001.json").read_text())
    assert copied["verses"][0]["ref"] == "Gen 1:1"


def test_export_audio_never_ships_audio_bytes(tmp_path):
    entry = _fixture(tmp_path)
    site_data = tmp_path / "site" / "data"
    site_data.mkdir(parents=True)
    export_audio(entry, str(site_data), "../data/audio/serve")
    exported = [
        name
        for _, _, files in os.walk(site_data)
        for name in files
    ]
    assert exported and all(name.endswith(".json") for name in exported)


def test_export_audio_absent_manifest(tmp_path):
    site_data = tmp_path / "site" / "data"
    site_data.mkdir(parents=True)
    assert export_audio({"id": "nasb"}, str(site_data), "x") is None
    assert export_audio(
        {"id": "wlc", "audio_manifest": str(tmp_path / "nope.json")}, str(site_data), "x"
    ) is None
    # a manifest whose sidecars are all unreadable exports nothing
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(
        {"chapters": [{"book": "Gen", "chapter": 1, "sidecar": str(tmp_path / "gone.json")}]}
    ))
    assert export_audio({"id": "wlc", "audio_manifest": str(manifest)}, str(site_data), "x") is None
    assert not (site_data / "audio" / "nasb").exists()
