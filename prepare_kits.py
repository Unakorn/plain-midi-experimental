"""Prepare missing raw kits for faster GUI exports. No downloads required."""
from pathlib import Path
import hashlib
import json

from drum_synth import GENRE_PROFILES, build_kit


def main():
    root = Path(__file__).resolve().parent
    kits = root / "kits"
    kits.mkdir(exist_ok=True)
    digest = hashlib.sha256((root / "drum_synth.py").read_bytes()).hexdigest()
    cache = kits / "cache.json"
    previous = json.loads(cache.read_text()) if cache.is_file() else {}
    if previous and previous.get("source_sha256") != digest:
        raise SystemExit("Synthesis code changed. Move the old kits folder elsewhere, then run this again.")
    for genre in GENRE_PROFILES:
        target = kits / genre
        if target.exists():
            print(f"Keeping existing {genre}; export validates its manifest before reuse.")
            continue
        print(f"Building {genre}…", flush=True)
        build_kit(genre, target)
    cache.write_text(json.dumps({"source_sha256": digest}, indent=2), encoding="utf-8")
    print("Kits prepared.")


if __name__ == "__main__":
    main()
