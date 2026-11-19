"""Create a complete, editable eight-bar session without external packages."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile

from generator import generate, write_bundle

APP_DIR = Path(__file__).resolve().parent


def configured_template():
    """FLP export is opt-in; the public release has no personal template."""
    value = os.environ.get("PLAIN_MIDI_TEMPLATE", "").strip()
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("PLAIN_MIDI_TEMPLATE does not point to a file.")
    return path

GENRE_LABELS = {
    "trance": "Trance", "hard_dance": "Hard dance",
    "hard_trance": "Hard trance", "house": "House",
    "hard_house": "Hard house", "hardstyle": "Hardstyle",
    "pop": "Pop", "rap": "Rap",
}
ROLE_LABELS = {
    "DRUMS": "Drums", "PAD": "Pad", "PLUCK": "Pluck",
    "CHORDS": "Chords", "ARPEGGIO": "Arpeggio", "LEAD": "Lead",
    "BASS": "Bass", "SUB_BASS": "Sub-bass", "NOISE": "Noise trigger",
}

_NAME_WORDS = ("Amber", "Blue", "Bright", "Cobalt", "Golden", "Indigo", "Lunar", "Neon",
               "Quiet", "Silver", "Solar", "Velvet", "Violet", "Warm", "Wild", "Winter")
_NAME_OBJECTS = ("Arc", "Bloom", "Cloud", "Current", "Echo", "Field", "Glow", "Harbor",
                 "Horizon", "Motion", "Orbit", "Pulse", "River", "Signal", "Sky", "Wave")


def desktop_directory():
    """Resolve the real Windows Desktop, including redirected/OneDrive folders."""
    if os.name != "nt":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    import ctypes
    from ctypes import wintypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
    folder_id = GUID.from_buffer_copy(uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641").bytes_le)
    value = ctypes.c_wchar_p()
    lookup = ctypes.windll.shell32.SHGetKnownFolderPath
    lookup.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                      ctypes.POINTER(ctypes.c_wchar_p)]
    lookup.restype = ctypes.c_long
    free = ctypes.windll.ole32.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    result = lookup(ctypes.byref(folder_id), 0, None, ctypes.byref(value))
    if result != 0 or not value.value:
        if value:
            free(ctypes.cast(value, ctypes.c_void_p))
        raise OSError("Windows could not resolve your Desktop folder.")
    try:
        return Path(value.value)
    finally:
        free(ctypes.cast(value, ctypes.c_void_p))


def _random_session_name(genre, parent, desktop=None):
    """Choose an unused human-readable name; exclusive publication is still used."""
    genre_label = GENRE_LABELS.get(genre, str(genre)).title()
    for _ in range(100):
        name = f"Plain MIDI - {genre_label} - {secrets.choice(_NAME_WORDS)} {secrets.choice(_NAME_OBJECTS)} - {secrets.token_hex(4)}"
        if (not (parent / name).exists()
                and (desktop is None or not any((desktop / (name + ext)).exists() for ext in (".flp", ".mid")))):
            return name
    raise FileExistsError("Could not choose an unused session name. Please try again.")


def _cached_kit(genre):
    """Accept only a complete cache built from this exact synthesis source/profile."""
    from drum_synth import GENRE_PROFILES, SAMPLE_RATE
    base = APP_DIR / "kits"
    kit = base / genre
    try:
        cache = json.loads((base / "cache.json").read_text(encoding="utf-8"))
        current_hash = hashlib.sha256((APP_DIR / "drum_synth.py").read_bytes()).hexdigest()
        if not isinstance(cache, dict) or cache.get("source_sha256") != current_hash:
            return None
        manifest = json.loads((kit / "kit.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                or manifest.get("genre") != genre or manifest.get("seed") != 42
                or manifest.get("profile") != GENRE_PROFILES.get(genre)
                or manifest.get("sample_rate") != SAMPLE_RATE
                or manifest.get("channels") != 1 or manifest.get("bits_per_sample") != 16):
            return None
        samples = manifest.get("samples")
        if not isinstance(samples, list) or len(samples) != 32:
            return None
        notes, names = set(), set()
        actual = kit.resolve()
        for sample in samples:
            if not isinstance(sample, dict):
                return None
            note, filename = sample.get("midi_note"), sample.get("filename")
            if (type(note) is not int or note not in range(36, 68) or note in notes
                    or not isinstance(filename, str) or not filename
                    or Path(filename).name != filename or "/" in filename or "\\" in filename
                    or Path(filename).suffix.lower() != ".wav" or filename.casefold() in names):
                return None
            source = (actual / filename).resolve()
            if source.parent != actual or not source.is_file() or source.stat().st_size <= 44:
                return None
            notes.add(note)
            names.add(filename.casefold())
        if notes != set(range(36, 68)):
            return None
        return kit
    except (OSError, ValueError, TypeError):
        return None


def _harmony(arrangement):
    return [(p["bar"], p["bars"], tuple(p["pitch_classes"]), p["root"])
            for p in arrangement["progression"]]


def keep_selected_parts(new, previous, roles):
    """Keep absolute notes only when the harmony remains exactly compatible."""
    roles = set(roles)
    unknown = roles - set(ROLE_LABELS)
    if unknown:
        raise ValueError("Unknown parts: " + ", ".join(sorted(unknown)))
    if not roles:
        return new
    if previous is None:
        raise ValueError("Generate a first idea before keeping parts.")
    if roles - {"DRUMS", "NOISE"} and _harmony(new) != _harmony(previous):
        raise ValueError("The key or chords changed. Uncheck the kept melodic parts so they can follow the new harmony.")
    result = copy.deepcopy(new)
    prior = {t["role"]: t for t in previous["tracks"]}
    for index, track in enumerate(result["tracks"]):
        if track["role"] in roles:
            if track["role"] not in prior:
                raise ValueError(f"The previous idea has no {track['role']} part.")
            result["tracks"][index] = copy.deepcopy(prior[track["role"]])
    result["kept_roles"] = sorted(roles)
    # A kept part may originate several generations back; retain the source
    # notes in arrangement.json, not a seed-only promise of reproduction.
    result["parent_seed"] = previous.get("seed")
    return result


def build_session(config, output_root=None, *, previous=None, keep_roles=(),
                  audio=True, flp=None, status=None, reuse_kits=False, _session_name=None):
    """Publish one new folder only after every requested asset succeeds."""
    status = status or (lambda message: None)
    template = configured_template() if flp is not False else None
    flp = template is not None if flp is None else flp
    if flp and template is None:
        raise ValueError("FLP export requires PLAIN_MIDI_TEMPLATE. Omit it to generate MIDI and raw WAVs.")
    status("Writing the shared chord progression and instrument parts…")
    arrangement = keep_selected_parts(generate(config), previous, keep_roles)
    parent = Path(output_root or APP_DIR / "generated").resolve()
    parent.mkdir(parents=True, exist_ok=True)
    name = _session_name or _random_session_name(arrangement["genre"], parent)
    if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError("Session name must be a plain folder name.")
    final = parent / name
    if final.exists():
        raise FileExistsError("That session name already exists; existing files are never overwritten.")
    staging = Path(tempfile.mkdtemp(prefix=".building-", dir=parent))
    try:
        write_bundle(arrangement, staging)
        # A permanent, session-named copy makes external drops identifiable.
        # It stays with the saved session after this window closes.
        midi_name = name + ".mid"
        if midi_name != "arrangement.mid":
            with (staging / midi_name).open("xb") as output:
                with (staging / "arrangement.mid").open("rb") as source:
                    shutil.copyfileobj(source, output)
        from drum_synth import build_kit
        # Kit timbre is fixed per genre. New MIDI variations keep the same kit.
        cached = _cached_kit(arrangement["genre"]) if reuse_kits else None
        if cached is not None:
            status("Copying the 32 raw drum sounds…")
            shutil.copytree(cached, staging / "kit")
        else:
            status("Synthesizing the 32 raw drum sounds…")
            build_kit(arrangement["genre"], staging / "kit", seed=42)
        preview_report = None
        if audio:
            status("Rendering a simple sound preview…")
            from drum_synth import render_preview
            preview_report = render_preview(arrangement, staging / "kit", staging / "preview.wav")
        flp_report = None
        if flp:
            status("Writing a new copy of your automation template…")
            from flp_export import export_template
            flp_report = export_template(arrangement, staging / "Automation Ready.flp", template_path=template,
                                         kit_dir=staging / "kit", kit_reference_dir=final / "kit")
        readme = [
            "PLAIN MIDI — EIGHT-BAR STARTER", "",
            f"Genre: {GENRE_LABELS.get(arrangement['genre'], arrangement['genre'])}",
            f"Key: {arrangement['key']} {arrangement['mode']} | Tempo: {arrangement['bpm']} BPM",
            f"Seed: {arrangement['seed']} | Eight bars of 4/4", "",
            "arrangement.mid: all instrument parts in one Type-1 MIDI file.",
            f"{midi_name}: identical MIDI, named for drag-and-drop from the app.",
            "parts/: separate MIDI files for your instrument channels.",
            "arrangement.json: exact notes, settings, and chord progression; editable.",
            "kit/: 32 synthesized, dry WAV drum sounds and the MIDI note map.",
            "gm/DRUMS.mid: alternative percussion mapping for General MIDI kits.", "",
            "DRUM MAP: MIDI 36–39 kick, 40–43 snare, 44–47 clap,",
            "48–51 closed hat, 52–55 open hat, 56–59 ride, 60–63 shaker, 64–67 tom.",
            "These are note numbers; DAWs label octaves differently.",
            "Load the 32 WAVs on these notes in your sampler to hear the kit.",
            "The MIDI file contains notes, not audio samples or synth presets.", "",
            "The sounds are original procedural drum-machine starters inspired by",
            "basic 808/909 synthesis ideas. They are not Roland samples or exact emulations.",
            "Genre recipes are deliberately simple and editable. Hardstyle kicks are",
            "raw starters; they are not finished distorted hardstyle productions.", "",
        ]
        if audio:
            readme += ["preview.wav: simple synthesized instrument tones plus this drum kit.",
                       "The held NOISE trigger is omitted; your arranger supplies its swells.", ""]
        if flp:
            readme += ["Automation Ready.flp: a new copy of your prepared FL Studio template",
                       "with these notes and tempo. The 32 new WAVs are assigned to Drum Buddy.",
                       "Drum Buddy's gates and automatic tuning are off for raw one-shot playback.",
                       "Other instrument sounds, effects, routing and automation examples remain.",
                       "Required plugins must stay installed. Keep this folder and its kit together;",
                       "the FLP stores absolute sample paths. Generate again after moving the app.",
                       "This optional adapter requires the documented prepared-template layout.",
                       "It cannot build a native project from an arbitrary FLP or a blank template.",
                       "The nine eight-bar parts here are source material, not a finished song.", ""]
        if keep_roles:
            readme += ["Kept parts: " + ", ".join(sorted(keep_roles)),
                       "Their exact notes are saved in arrangement.json.", ""]
        (staging / "START HERE.txt").write_text("\n".join(readme), encoding="utf-8")
        report = {"schema_version": 1, "genre": arrangement["genre"],
                  "seed": arrangement["seed"], "audio": preview_report,
                  "flp": flp_report, "kept_roles": sorted(keep_roles), "reused_kit": cached is not None}
        # Paths in helper reports may point at the staging directory. Normalize
        # them to the published folder before saving the report.
        def published(value):
            if isinstance(value, dict):
                return {k: published(v) for k, v in value.items()}
            if isinstance(value, (tuple, list)):
                return [published(v) for v in value]
            if isinstance(value, Path):
                value = str(value)
            if isinstance(value, str):
                return value.replace(str(staging), str(final))
            return value
        (staging / "session_report.json").write_text(json.dumps(published(report), indent=2), encoding="utf-8")
        staging.rename(final)
    except Exception:
        # Only remove the temporary directory this call created under parent.
        if staging.parent == parent and staging.name.startswith(".building-") and staging.exists():
            shutil.rmtree(staging)
        raise
    status("Ready — MIDI, drum kit, and editable source files saved.")
    return {"folder": str(final), "arrangement": arrangement,
            "midi": str(final / midi_name),
            "preview": str(final / "preview.wav") if audio else None,
            "flp": str(final / "Automation Ready.flp") if flp else None,
            "project": str(final / ("Automation Ready.flp" if flp else "arrangement.mid"))}


def build_desktop_session(config, *, desktop=None, previous=None, keep_roles=(), status=None):
    """Save a random Desktop MIDI file, or opt-in FLP, plus companion assets."""
    from generator import validate_config
    settings = validate_config(config)
    template = configured_template()
    extension = ".flp" if template is not None else ".mid"
    status = status or (lambda message: None)
    actual_desktop = Path(desktop if desktop is not None else desktop_directory()).resolve()
    actual_desktop.mkdir(parents=True, exist_ok=True)
    companions = actual_desktop / "Plain MIDI Files"
    companions.mkdir(exist_ok=True)
    name = _random_session_name(settings["genre"], companions, actual_desktop)
    destination = actual_desktop / (name + extension)
    folder = companions / name
    result = None
    desktop_created = False
    try:
        result = build_session(settings, companions, previous=previous, keep_roles=keep_roles,
                               audio=False, flp=template is not None, reuse_kits=True,
                               _session_name=name, status=status)
        source = Path(result["project"])
        if Path(result["folder"]) != folder:
            raise RuntimeError("The companion folder did not match its Desktop file.")
        status("Saving your eight-bar file to Desktop…")
        with destination.open("xb") as output:
            desktop_created = True
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
        report_path = folder / "session_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["desktop_project"] = str(destination)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except Exception:
        if desktop_created and destination.parent == actual_desktop and destination.name == name + extension:
            destination.unlink(missing_ok=True)
        if result is not None and folder.parent == companions and folder.name == name and folder.is_dir():
            shutil.rmtree(folder)
        raise
    result["companion_project"] = result["project"]
    result["project"] = str(destination)
    result["flp"] = str(destination) if extension == ".flp" else None
    status("Saved — your eight-bar file is on Desktop.")
    return result
