"""Command-line entry point. Run with --help for examples and controls."""
import argparse
import json
from pathlib import Path

from workflow import build_session, build_desktop_session, GENRE_LABELS


def main():
    p = argparse.ArgumentParser(description="Make a generic eight-bar arrangement, MIDI files, and a raw 32-drum kit. No training or downloads.")
    p.add_argument("--genre", choices=list(GENRE_LABELS), default="house")
    p.add_argument("--key", default="A")
    p.add_argument("--mode", choices=["major", "minor"], default="minor")
    p.add_argument("--bpm", type=float)
    p.add_argument("--progression", default="", help="1–32 chord names or scale degrees; used when --auto-chords is off.")
    p.add_argument("--auto-chords", action="store_true", help="Generate fresh shared harmony from the seed.")
    p.add_argument("--chord-creativity", type=float, default=.5, help="0–1: more changes, richer chords and shared-note overlaps (with --auto-chords).")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--density", type=float, default=.5)
    p.add_argument("--swing", type=float, default=None)
    p.add_argument("--variation", type=float, default=.3)
    p.add_argument("--chord-style", choices=["block", "rhythm"], default="block")
    p.add_argument("--lead-style", choices=["lead", "pluck"], default="lead")
    p.add_argument("--base-drums", action="store_true", help="Use only the first sound in each drum family.")
    p.add_argument("--output", type=Path, help="Parent folder; a fresh session subfolder will be created.")
    p.add_argument("--desktop", action="store_true", help="Export a random Desktop MIDI file, or FLP with an opt-in compatible template.")
    p.add_argument("--no-preview", action="store_true")
    p.add_argument("--no-flp", action="store_true")
    p.add_argument("--config", type=Path, help="Load saved generation settings from JSON, overriding the options above.")
    args = p.parse_args()
    config = {k: getattr(args, k) for k in ["genre", "key", "mode", "progression", "auto_chords", "chord_creativity", "seed", "density", "swing", "variation", "chord_style", "lead_style"]}
    config["drum_variants"] = not args.base_drums
    if args.bpm is not None:
        config["bpm"] = args.bpm
    try:
        if args.config:
            source = json.loads(args.config.read_text(encoding="utf-8"))
            if not isinstance(source, dict) or not isinstance(source.get("config", source), dict):
                raise ValueError("The config file must contain a JSON object of settings.")
            config.update(source.get("config", source))
        if args.desktop:
            if args.output or args.no_flp:
                raise ValueError("--desktop cannot be combined with --output or --no-flp.")
            result = build_desktop_session(config, status=lambda s: print(s, flush=True))
        else:
            result = build_session(config, args.output, audio=not args.no_preview, flp=False if args.no_flp else None,
                                   status=lambda s: print(s, flush=True))
    except (ValueError, OSError) as error:
        p.exit(1, f"Could not create the session: {error}\n")
    print(result["folder"])
    if result.get("project"):
        print(result["project"])


if __name__ == "__main__":
    main()
