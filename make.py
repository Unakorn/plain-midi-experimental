"""Command-line entry point. Run with --help for examples and controls."""
import argparse
import json
import math
from pathlib import Path

from generator import generate, validate_arrangement
from evolution import evolve_song, reorder_song, shuffle_song, validate_song
from workflow import build_session, build_desktop_session, GENRE_LABELS


def _amount(value):
    amount = float(value)
    if not math.isfinite(amount) or not 0 <= amount <= 1:
        raise argparse.ArgumentTypeError("Evolution must be a number from 0 to 1.")
    return amount


def _order(value):
    order = list(value.strip().upper())
    if sorted(order) != list("ABCD"):
        raise argparse.ArgumentTypeError("Order must contain A, B, C and D once, for example ACBD.")
    return order


def _prepare_idea(args, config):
    """Load exact notes, or explicitly develop an eight-bar source once."""
    prepared = None
    if args.idea:
        if args.config:
            raise ValueError("--idea keeps its saved settings; --config cannot be combined with it.")
        prepared = json.loads(args.idea.read_text(encoding="utf-8"))
        if not isinstance(prepared, dict):
            raise ValueError("The idea file must contain a saved arrangement JSON object.")
        try:
            validate_arrangement(prepared)
            if prepared["bars"] == 32:
                validate_song(prepared)
            config = prepared["config"]
            if not isinstance(config, dict):
                raise ValueError("The saved idea needs its generation settings.")
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("The idea file is missing valid saved arrangement data.") from error
    if args.song and (prepared is None or prepared["bars"] == 8):
        source = prepared if prepared is not None else generate(config)
        seed = args.seed if args.seed is not None else source["seed"]
        prepared = evolve_song(source, seed=seed, amount=args.evolution)
    if args.order or args.shuffle:
        if prepared is None or prepared["bars"] != 32:
            raise ValueError("--order and --shuffle need a 32-bar saved idea or --song.")
        if args.order:
            prepared = reorder_song(prepared, args.order)
        else:
            seed = args.seed if args.seed is not None else prepared["seed"]
            prepared = shuffle_song(prepared, seed)
    return config, prepared


def main():
    p = argparse.ArgumentParser(description="Make an eight-bar idea or develop a 32-bar song, with MIDI files and a raw 32-drum kit. No training or downloads.")
    p.add_argument("--genre", choices=list(GENRE_LABELS), default="house")
    p.add_argument("--key", default="A")
    p.add_argument("--mode", choices=["major", "minor"], default="minor")
    p.add_argument("--bpm", type=float)
    p.add_argument("--progression", default="", help="1–32 chord names or scale degrees; used when --auto-chords is off.")
    p.add_argument("--auto-chords", action="store_true", help="Generate fresh shared harmony from the seed.")
    p.add_argument("--chord-creativity", type=float, default=.5, help="0–1: more changes, richer chords and shared-note overlaps (with --auto-chords).")
    p.add_argument("--seed", type=int, help="Generation, evolution or shuffle seed; defaults to 42 for new ideas and the saved seed for loaded ideas.")
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
    p.add_argument("--idea", type=Path, help="Load exact saved 8/32-bar arrangement JSON, including its notes and settings; generation options do not reroll it.")
    p.add_argument("--song", action="store_true", help="Develop an eight-bar idea into four related sections; an already saved 32-bar song stays intact.")
    p.add_argument("--evolution", type=_amount, default=.5, metavar="0..1", help="Amount of development when --song expands eight bars (default: 0.5).")
    order = p.add_mutually_exclusive_group()
    order.add_argument("--order", type=_order, metavar="ABCD", help="Reorder the four saved sections, for example ACBD; notes are retained.")
    order.add_argument("--shuffle", action="store_true", help="Shuffle saved sections to a different order using --seed; notes are retained.")
    args = p.parse_args()
    config = {k: getattr(args, k) for k in ["genre", "key", "mode", "progression", "auto_chords", "chord_creativity", "seed", "density", "swing", "variation", "chord_style", "lead_style"]}
    if args.seed is None:
        config["seed"] = 42
    config["drum_variants"] = not args.base_drums
    if args.bpm is not None:
        config["bpm"] = args.bpm
    try:
        if args.config:
            source = json.loads(args.config.read_text(encoding="utf-8"))
            if not isinstance(source, dict) or not isinstance(source.get("config", source), dict):
                raise ValueError("The config file must contain a JSON object of settings.")
            config.update(source.get("config", source))
        config, prepared = _prepare_idea(args, config)
        if args.desktop:
            if args.output or args.no_flp:
                raise ValueError("--desktop cannot be combined with --output or --no-flp.")
            result = build_desktop_session(config, status=lambda s: print(s, flush=True),
                                           prepared_arrangement=prepared)
        else:
            result = build_session(config, args.output, audio=not args.no_preview, flp=False if args.no_flp else None,
                                   status=lambda s: print(s, flush=True), prepared_arrangement=prepared)
    except (ValueError, OSError) as error:
        p.exit(1, f"Could not create the session: {error}\n")
    print(result["folder"])
    if result.get("project"):
        print(result["project"])


if __name__ == "__main__":
    main()
