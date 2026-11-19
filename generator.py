"""Plain MIDI: editable, rule-based eight-bar musical building blocks.

No model, internet connection or third-party package is required. Pitches use
MIDI numbers (middle C=60); the FL Studio octave display is one octave higher
than many other DAWs. The drum map is the custom 32-pad map, not General MIDI.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import re
import shutil
import struct
import tempfile

BASE = Path(__file__).resolve().parent
PPQ = 480
BARS = 8
BEATS = 32
ROLES = ("DRUMS", "PAD", "PLUCK", "CHORDS", "ARPEGGIO", "LEAD", "BASS", "SUB_BASS", "NOISE")
DRUM_MAP = {"kick": 36, "snare": 40, "clap": 44, "closed_hat": 48,
            "open_hat": 52, "ride": 56, "shaker": 60, "tom": 64}
GM_DRUM_MAP = {"kick": 36, "snare": 38, "clap": 39, "closed_hat": 42,
               "open_hat": 46, "ride": 51, "shaker": 70, "tom": 45}
KEYS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
        "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
        "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
SCALES = {"major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10),
          "dorian": (0, 2, 3, 5, 7, 9, 10), "mixolydian": (0, 2, 4, 5, 7, 9, 10),
          "harmonic_minor": (0, 2, 3, 5, 7, 8, 11)}
CHORDS = {"": (0, 4, 7), "m": (0, 3, 7), "dim": (0, 3, 6),
          "aug": (0, 4, 8), "sus2": (0, 2, 7), "sus4": (0, 5, 7),
          "5": (0, 7), "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9),
          "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10),
          "m7b5": (0, 3, 6, 10), "dim7": (0, 3, 6, 9),
          "add9": (0, 4, 7, 14), "madd9": (0, 3, 7, 14),
          "9": (0, 4, 7, 10, 14), "maj9": (0, 4, 7, 11, 14),
          "m9": (0, 3, 7, 10, 14), "11": (0, 4, 7, 10, 14, 17),
          "m11": (0, 3, 7, 10, 14, 17), "13": (0, 4, 7, 10, 14, 21)}
NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
DEFAULT_CONFIG = {"genre": "house", "key": "A", "mode": "minor", "bpm": None,
                  "progression": "", "seed": 42, "density": 0.5, "swing": None,
                  "variation": 0.3, "drum_variants": True, "chord_style": "block",
                  "lead_style": "lead", "muted_roles": []}


def _load_recipes():
    data = json.loads((BASE / "recipes.json").read_text(encoding="utf-8"))
    recipes = data["genres"]
    for slug, recipe in recipes.items():
        if not isinstance(slug, str) or not isinstance(recipe, dict):
            raise ValueError("Genre recipes must map names to objects.")
        for family in DRUM_MAP:
            hits = recipe["drums"].get(family, [])
            if not all(isinstance(t, (int, float)) and not isinstance(t, bool)
                       and math.isfinite(t) and 0 <= t < 4 for t in hits):
                raise ValueError(f"Invalid {slug} / {family} beat positions.")
        for name in ("bass", "chord", "pluck", "lead"):
            if not recipe.get(name) or not all(isinstance(t, (int, float))
                    and not isinstance(t, bool) and math.isfinite(t) and 0 <= t < 4
                    for t in recipe[name]):
                raise ValueError(f"Invalid {slug} / {name} beat positions.")
        if recipe["arp_step"] not in (0.25, 0.5, 1):
            raise ValueError(f"Invalid {slug} arpeggio step.")
    return recipes


GENRES = _load_recipes()


def _number(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{name} must be a number from {lo} to {hi}.")
    return value


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Settings must be a JSON object.")
    unknown = set(config) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError("Unknown setting(s): " + ", ".join(sorted(unknown)))
    c = {**DEFAULT_CONFIG, **config}
    c["genre"] = str(c["genre"]).strip().lower().replace(" ", "_").replace("-", "_")
    if c["genre"] not in GENRES:
        raise ValueError("Unknown genre. Choose one from GENRES / recipes.json.")
    key = str(c["key"]).strip().replace("♯", "#").replace("♭", "b")
    c["key"] = key[:1].upper() + key[1:]
    if c["key"] not in KEYS:
        raise ValueError("Key must be a note such as A, C#, or Bb.")
    c["mode"] = str(c["mode"]).strip().lower().replace(" ", "_")
    if c["mode"] not in SCALES:
        raise ValueError("Choose major, minor, dorian, mixolydian, or harmonic_minor.")
    r = GENRES[c["genre"]]
    c["bpm"] = _number(r["bpm"] if c["bpm"] is None else c["bpm"], "BPM", 30, 300)
    c["swing"] = _number(r["swing"] if c["swing"] is None else c["swing"], "Swing", 0, 0.6)
    c["density"] = _number(c["density"], "Density", 0.1, 1)
    c["variation"] = _number(c["variation"], "Variation", 0, 1)
    if isinstance(c["seed"], bool) or not isinstance(c["seed"], int) or not 0 <= c["seed"] <= 2147483647:
        raise ValueError("Seed must be an integer from 0 to 2147483647.")
    if not isinstance(c["drum_variants"], bool):
        raise ValueError("drum_variants must be true or false.")
    if c["chord_style"] not in ("block", "rhythm") or c["lead_style"] not in ("lead", "pluck"):
        raise ValueError("Chord style: block/rhythm. Lead style: lead/pluck.")
    if not isinstance(c["muted_roles"], list) or any(role not in ROLES for role in c["muted_roles"]):
        raise ValueError("muted_roles must be a list of uppercase track roles.")
    c["muted_roles"] = list(dict.fromkeys(c["muted_roles"]))
    if not isinstance(c["progression"], str):
        raise ValueError("Progression must be text, e.g. 1 6 3 7 or Am F C G.")
    c["progression"] = c["progression"].strip() or ("1 5 6 4" if c["mode"] == "major" else r["progression"])
    _progression(c)
    return c


def _progression(c):
    tokens = re.split(r"[\s,|]+", c["progression"].strip())
    if len(tokens) not in (1, 2, 4, 8):
        raise ValueError("Enter 1, 2, 4, or 8 chords/degrees; they divide the eight bars equally.")
    scale = SCALES[c["mode"]]
    tonic = KEYS[c["key"]]
    result = []
    for i, token in enumerate(tokens):
        if re.fullmatch(r"[1-7]", token):
            d = int(token) - 1
            root = (tonic + scale[d]) % 12
            pcs = [(tonic + scale[(d + n) % 7]) % 12 for n in (0, 2, 4)]
            intervals = tuple((pc-root) % 12 for pc in pcs)
            suffix = next((q for q in ("", "m", "dim", "aug") if CHORDS[q] == intervals), "")
            symbol = NOTE_NAMES[root] + suffix
        else:
            match = re.fullmatch(r"([A-Ga-g])([#b]?)(maj9|madd9|add9|maj7|m7b5|dim7|sus2|sus4|m11|m9|m7|m6|dim|aug|m|13|11|9|7|6|5)?", token)
            if not match:
                raise ValueError(f"Unknown chord '{token}'. Use degrees 1–7 or symbols such as Am, Fmaj7, G7, Dsus2.")
            note = match[1].upper() + match[2]
            if note not in KEYS:
                raise ValueError(f"Unsupported chord root '{note}'. Use its enharmonic equivalent.")
            root = KEYS[note]
            suffix = match[3] or ""
            pcs = list(dict.fromkeys((root + n) % 12 for n in CHORDS[suffix]))
            symbol = note + suffix
        result.append({"bar": i * (8 // len(tokens)), "bars": 8 // len(tokens),
                       "symbol": symbol, "root": 48 + root, "pitch_classes": pcs})
    return result


def _rng(c, role):
    seed = hashlib.sha256(f"{c['seed']}|{c['genre']}|{role}".encode()).digest()
    return random.Random(int.from_bytes(seed[:8], "big"))


def _near(pc, target, low, high):
    return min((n for n in range(low, high+1) if n % 12 == pc), key=lambda n: (abs(n-target), n))


def _voicing(pcs, previous=None, center=61):
    # Search close positions; keep all chord tones while minimizing movement.
    choices = [[n for n in range(48, 81) if n % 12 == pc] for pc in pcs]
    best, best_score = None, float("inf")
    for candidate in itertools.product(*choices):
        ordered = sorted(candidate)
        if ordered[-1] - ordered[0] > 24:
            continue
        score = abs(sum(ordered)/len(ordered) - center) * 1.7 + (ordered[-1]-ordered[0])*.12
        if previous:
            score += sum(min(abs(n-old) for old in previous) for n in ordered)
        if score < best_score:
            best, best_score = ordered, score
    return best or sorted(_near(pc, center, 48, 80) for pc in pcs)


def _swing(t, amount):
    # Delay the offbeat eighth only. Downbeats and quarter notes stay fixed.
    frac = t % 1
    if .5 <= frac < 1:
        return t + amount * .25
    return t


def _add(notes, t, dur, pitch, vel, **extra):
    t = round(t * PPQ) / PPQ
    end = min(BEATS, round((t + dur) * PPQ) / PPQ)
    if 0 <= t < BEATS and end > t:
        notes.append({"start": t, "duration": end-t, "pitch": int(pitch),
                      "velocity": max(1, min(127, int(vel))), **extra})


def _thin_hits(hits, density, rng):
    hits = sorted(set(hits))
    if density >= .5:
        return hits
    # Keep the first attack of a bar even for the sparsest setting.
    return [h for i, h in enumerate(hits) if i == 0 or rng.random() < .3 + density]


def _drums(c, r):
    rng = _rng(c, "DRUMS")
    notes = []
    velocities = {"kick": 103, "snare": 94, "clap": 70, "closed_hat": 63,
                  "open_hat": 59, "ride": 48, "shaker": 43, "tom": 73}
    # A two-bar rhythm is repeated. Variation only changes phrase endings.
    family_hits = {family: _thin_hits(r["drums"].get(family, []), c["density"], rng)
                   for family in DRUM_MAP}
    for bar in range(8):
        for family, base in DRUM_MAP.items():
            hits = list(family_hits[family])
            if family == "ride" and bar not in (2, 6, 7):
                hits = []
            if family == "tom":
                hits = hits if bar in (3, 7) else []
                if bar in (3, 7) and c["variation"] > .45:
                    hits += [3.25, 3.5]
            if family == "closed_hat" and c["density"] > .75:
                hits += [h+.25 for h in hits if h+.25 < 4]
            if family == "closed_hat" and bar == 7 and c["variation"] > .6:
                hits += [3.25, 3.75]
            # Leave room for an open hat instead of triggering closed and open together.
            if family == "closed_hat":
                hits = [h for h in hits if h not in family_hits["open_hat"]]
            for j, hit in enumerate(sorted(set(hits))):
                variant = 0
                if c["drum_variants"]:
                    if family in ("tom",):
                        variant = j % 4
                    elif family in ("closed_hat", "shaker") and hit % 1:
                        variant = 1
                    elif bar in (3, 7) and hit >= 3 and c["variation"] > .2:
                        variant = 2 if bar == 3 else 3
                t = bar*4 + hit
                if family not in ("kick", "snare", "clap"):
                    t = _swing(t, c["swing"])
                vel = velocities[family] + rng.randint(-4, 4)
                if hit % 1:
                    vel -= 7
                duration = .12 if family not in ("open_hat", "ride", "tom") else .45
                _add(notes, t, duration, base+variant, vel, family=family, variant=variant)
    # Note lengths communicate hat choke timing to samplers that honor note-offs.
    hat_notes = sorted((n for n in notes if n["family"] in ("closed_hat", "open_hat")), key=lambda n:n["start"])
    for a, b in zip(hat_notes, hat_notes[1:]):
        if a["family"] == "open_hat":
            a["duration"] = min(a["duration"], max(1/PPQ, b["start"]-a["start"]))
    return notes


def _melody(c, r, progression, role):
    rng = _rng(c, role)
    notes = []
    motif = [0, 1, 2, 1, 0, 2, 1, 0]
    rotation = rng.randrange(3)
    motif = [(n + rotation) % 3 for n in motif]
    voice = None
    last_lead = 69
    for chord in progression:
        start, length = chord["bar"]*4, chord["bars"]*4
        pcs = chord["pitch_classes"]
        root_pc = chord["root"] % 12
        voice = _voicing(pcs, voice)
        if role == "PAD":
            for p in voice:
                _add(notes, start, length-.04, p, 47+rng.randint(-3,3))
            continue
        if role == "CHORDS" and c["chord_style"] == "block":
            for p in voice:
                _add(notes, start, length-.10, p, 64+rng.randint(-3,3))
            continue
        if role == "SUB_BASS":
            pitch = _near(root_pc, 33, 24, 43)
            for offset in range(0, length, 4):
                _add(notes, start+offset, 3.75, pitch, 75)
            continue
        for local_bar in range(chord["bars"]):
            bar_start = start + local_bar*4
            if role == "BASS":
                hits = _thin_hits(r["bass"], c["density"], rng)
                pitch = _near(root_pc, 45, 36, 55)
                for j, hit in enumerate(hits):
                    next_hit = hits[j+1] if j+1 < len(hits) else 4
                    duration = min(.65, next_hit-hit-.03)
                    _add(notes, _swing(bar_start+hit,c["swing"]), max(.1,duration), pitch, 85+rng.randint(-4,4))
            elif role == "CHORDS":
                for hit in _thin_hits(r["chord"],c["density"],rng):
                    for p in voice:
                        _add(notes, _swing(bar_start+hit,c["swing"]), .32, p, 62+rng.randint(-3,3))
            elif role == "ARPEGGIO":
                step = r["arp_step"] * (2 if c["density"] < .35 else 1)
                # Short, repeating bursts leave breathing space for the lead.
                for j in range(round(4/step)):
                    hit = j*step
                    if 1.5 <= hit < 2.5 and c["density"] < .8:
                        continue
                    p = _near(pcs[j % len(pcs)],72,60,84)
                    _add(notes,_swing(bar_start+hit,c["swing"]),step*.65,p,46+(j%3)*3)
            else:
                pattern = r["lead"] if role == "LEAD" else r["pluck"]
                hits = _thin_hits(pattern,c["density"],rng)
                for j, hit in enumerate(hits):
                    idx = motif[(j + (int(bar_start/4) % 2)*2) % len(motif)] % len(pcs)
                    if c["variation"] > 0 and int(bar_start/4) == 7 and j == len(hits)-1:
                        idx = 0  # End the eight-bar phrase on the current chord root.
                    pc = pcs[idx]
                    p = _near(pc,last_lead if role == "LEAD" else 72,60 if role=="LEAD" else 64,83 if role=="LEAD" else 88)
                    if role == "LEAD":
                        last_lead = p
                    next_hit = hits[j+1] if j+1<len(hits) else 4
                    length_limit = .2 if role == "PLUCK" or c["lead_style"] == "pluck" else 1.35
                    duration = min(length_limit,max(.1,next_hit-hit-.08))
                    _add(notes,_swing(bar_start+hit,c["swing"]),duration,p,(76 if role=="LEAD" else 58)+rng.randint(-4,4))
    # Notes stop at harmonic boundaries, including swung late-bar pickups.
    for note in notes:
        chord = next(ch for ch in progression if ch["bar"]*4 <= note["start"] < (ch["bar"]+ch["bars"])*4)
        note["duration"] = min(note["duration"], (chord["bar"]+chord["bars"])*4-note["start"])
    return notes


def _clean_notes(notes):
    # Prevent overlapping same-pitch notes; many synths release both on the first off.
    notes.sort(key=lambda n:(n["start"],n["pitch"]))
    last = {}
    result = []
    for n in notes:
        old = last.get(n["pitch"])
        if old is not None and abs(old["start"]-n["start"]) < .5/PPQ:
            old["velocity"] = max(old["velocity"],n["velocity"])
            old["duration"] = max(old["duration"],n["duration"])
            continue
        if old is not None and old["start"]+old["duration"] > n["start"]:
            old["duration"] = n["start"]-old["start"]
        n["duration"] = max(1/PPQ,round(n["duration"]*PPQ)/PPQ)
        n["duration"] = min(n["duration"],BEATS-n["start"])
        last[n["pitch"]] = n
        result.append(n)
    return result


def generate(config=None):
    """Return a fresh eight-bar arrangement. All starts/durations are in beats."""
    c = validate_config(config or {})
    r = GENRES[c["genre"]]
    progression = _progression(c)
    channels = {"DRUMS":9,"PAD":0,"PLUCK":1,"CHORDS":2,"ARPEGGIO":3,
                "LEAD":4,"BASS":5,"SUB_BASS":6,"NOISE":7}
    programs = {"DRUMS":None,"PAD":89,"PLUCK":10,"CHORDS":0,"ARPEGGIO":81,
                "LEAD":80,"BASS":38,"SUB_BASS":38,"NOISE":None}
    tracks = []
    for role in ROLES:
        if role == "DRUMS":
            notes = _drums(c,r)
        elif role == "NOISE":
            notes = []
            _add(notes,0,BEATS,60,70)
        else:
            notes = _melody(c,r,progression,role)
        tracks.append({"role":role,"channel":channels[role],"program":programs[role],
                       "muted":role in c["muted_roles"],"notes":_clean_notes(notes)})
    arrangement = {"schema_version":1,"bars":BARS,"beats_per_bar":4,"ppq":PPQ,
                   "bpm":c["bpm"],"key":c["key"],"mode":c["mode"],"seed":c["seed"],
                   "genre":c["genre"],"config":c,"progression":progression,"tracks":tracks,
                   "drum_map":DRUM_MAP,
                   "notes":"Generic, editable genre starting points, not artist transcriptions. NOISE is a sampler/synth trigger, not a pitched accompaniment. Custom drum map; use gm/DRUMS.mid for a General MIDI kit. MIDI exports do not send program changes."}
    validate_arrangement(arrangement)
    return arrangement


def validate_arrangement(a):
    if a.get("bars") != 8 or a.get("beats_per_bar") != 4 or a.get("ppq") != PPQ:
        raise ValueError("This version requires eight bars of 4/4 at 480 PPQ.")
    _number(a.get("bpm"),"BPM",30,300)
    roles = [t["role"] for t in a["tracks"]]
    if sorted(roles) != sorted(ROLES):
        raise ValueError("Arrangement must contain each of the nine roles once.")
    for track in a["tracks"]:
        if not isinstance(track["channel"],int) or not 0 <= track["channel"] < 16:
            raise ValueError("MIDI channel out of range.")
        if not track["notes"]:
            raise ValueError(f"{track['role']} has no notes.")
        for n in track["notes"]:
            _number(n["start"],"Note start",0,BEATS-1/PPQ)
            _number(n["duration"],"Note duration",1/PPQ,BEATS)
            if n["start"]+n["duration"] > BEATS+1e-8:
                raise ValueError("A note extends past bar eight.")
            for field,lo,hi in (("pitch",0,127),("velocity",1,127)):
                if isinstance(n[field],bool) or not isinstance(n[field],int) or not lo <= n[field] <= hi:
                    raise ValueError(f"Invalid MIDI {field}.")
        if max(n["start"]+n["duration"] for n in track["notes"]) <= 28:
            raise ValueError(f"{track['role']} needs musical content in bar eight for FLP auto-length.")


def _vlq(value):
    if not isinstance(value,int) or not 0 <= value <= 0x0fffffff:
        raise ValueError("MIDI delta time out of range.")
    data = [value & 0x7f]
    while value > 0x7f:
        value >>= 7
        data.insert(0,(value & 0x7f)|0x80)
    return bytes(data)


def _meta(kind,data):
    return b"\xff"+bytes([kind])+_vlq(len(data))+data


def _chunk(events):
    body = bytearray()
    tick = 0
    for at,order,data in sorted(events,key=lambda event:(event[0],event[1])):
        body += _vlq(at-tick)+data
        tick = at
    body += _vlq(BEATS*PPQ-tick)+_meta(0x2f,b"")
    return b"MTrk"+struct.pack(">I",len(body))+body


def midi_bytes(arrangement, tracks=None, gm_drums=False):
    """Standard MIDI type 1, no program changes, exact eight-bar end markers."""
    validate_arrangement(arrangement)
    tracks = arrangement["tracks"] if tracks is None else tracks
    conductor = [(0,0,_meta(3,b"Plain MIDI - 8 bars")),
                 (0,1,_meta(0x51,round(60000000/arrangement["bpm"]).to_bytes(3,"big"))),
                 (0,2,_meta(0x58,bytes([4,2,24,8])))]
    for chord in arrangement["progression"]:
        conductor.append((chord["bar"]*4*PPQ,3,_meta(6,chord["symbol"].encode("utf-8"))))
    chunks = [_chunk(conductor)]
    for track in tracks:
        events = [(0,0,_meta(3,track["role"].encode("ascii")))]
        if not track.get("muted",False):
            for note in track["notes"]:
                pitch = note["pitch"]
                if track["role"] == "DRUMS" and gm_drums:
                    family = next((f for f,base in DRUM_MAP.items() if base <= pitch < base+4),None)
                    if family is None:
                        raise ValueError("Unknown custom drum pitch.")
                    pitch = (45,47,48,50)[pitch-DRUM_MAP[family]] if family == "tom" else GM_DRUM_MAP[family]
                start = round(note["start"]*PPQ)
                end = round((note["start"]+note["duration"])*PPQ)
                events.append((start,2,bytes([0x90|track["channel"],pitch,note["velocity"]])))
                events.append((end,1,bytes([0x80|track["channel"],pitch,0])))
        chunks.append(_chunk(events))
    return b"MThd"+struct.pack(">IHHH",6,1,len(chunks),PPQ)+b"".join(chunks)


def write_bundle(arrangement, output_dir):
    """Write a complete bundle, refusing to overwrite any existing file."""
    validate_arrangement(arrangement)
    target = Path(output_dir).resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise FileExistsError("Choose a new or empty output folder; existing files are never overwritten.")
    target.parent.mkdir(parents=True,exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".plain-midi-",dir=target.parent))
    try:
        (stage/"parts").mkdir()
        (stage/"gm").mkdir()
        (stage/"arrangement.mid").write_bytes(midi_bytes(arrangement))
        (stage/"arrangement.json").write_text(json.dumps(arrangement,indent=2)+"\n",encoding="utf-8")
        (stage/"settings.json").write_text(json.dumps(arrangement["config"],indent=2)+"\n",encoding="utf-8")
        for track in arrangement["tracks"]:
            (stage/"parts"/(track["role"]+".mid")).write_bytes(midi_bytes(arrangement,[track]))
        drum = next(t for t in arrangement["tracks"] if t["role"] == "DRUMS")
        (stage/"gm"/"DRUMS.mid").write_bytes(midi_bytes(arrangement,[drum],gm_drums=True))
        text = "PLAIN MIDI - EIGHT-BAR STARTER\n\n"
        text += f"{GENRES[arrangement['genre']]['label']} | {arrangement['key']} {arrangement['mode']} | {arrangement['bpm']} BPM\n"
        text += "Chords: " + " | ".join(c["symbol"] for c in arrangement["progression"])+"\n\n"
        text += "Import arrangement.mid for all nine parts, or parts/*.mid individually.\nAll files start at beat zero and end at eight bars. No MIDI program changes.\n"
        text += "NOISE is a held trigger for the noise instrument; mute it during plain MIDI audition.\n"
        text += "DRUMS uses the custom 32-pad map below. The four slots in each family\nare variations, not four sounds that should be layered on every hit.\n"
        text += "gm/DRUMS.mid is an alternative for General MIDI kits; do not import both.\n\n"
        for family,base in DRUM_MAP.items():
            text += f"{family:12s} MIDI {base}-{base+3}\n"
        text += "\nEdit settings.json to regenerate, recipes.json for genre rhythms, or the MIDI notes in your DAW.\n"
        text += "Genre names are broad starting points. Use your ears and edit freely.\n"
        (stage/"READ ME.txt").write_text(text,encoding="utf-8")
        if target.exists():
            # An empty caller-created folder is allowed. Check it again before removal.
            if any(target.iterdir()):
                raise FileExistsError("Output folder changed while generating.")
            target.rmdir()
        stage.rename(target)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    return {"folder":str(target),"midi":str(target/"arrangement.mid"),
            "json":str(target/"arrangement.json"),"settings":str(target/"settings.json"),
            "parts":str(target/"parts"),"gm_drums":str(target/"gm"/"DRUMS.mid")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings",type=Path,help="Load an editable settings.json")
    parser.add_argument("--out",type=Path,help="A new output folder")
    parser.add_argument("--list-genres",action="store_true")
    parser.add_argument("--genre",choices=sorted(GENRES))
    parser.add_argument("--key")
    parser.add_argument("--mode",choices=tuple(SCALES))
    parser.add_argument("--bpm",type=float)
    parser.add_argument("--progression")
    parser.add_argument("--seed",type=int)
    args = parser.parse_args()
    if args.list_genres:
        for slug,recipe in GENRES.items():
            print(f"{slug:22s} {recipe['bpm']:3d} BPM  {recipe['label']}")
        return
    config = json.loads(args.settings.read_text(encoding="utf-8")) if args.settings else {}
    for name in ("genre","key","mode","bpm","progression","seed"):
        value = getattr(args,name)
        if value is not None:
            config[name] = value
    if args.out is None:
        parser.error("--out is required (choose a new folder).")
    try:
        result = write_bundle(generate(config),args.out)
    except (ValueError,FileExistsError,OSError) as exc:
        parser.exit(2,f"Error: {exc}\n")
    print(result["folder"])


if __name__ == "__main__":
    main()
