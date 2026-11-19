"""Plain MIDI: editable, rule-based eight-bar musical building blocks.

No model, internet connection or third-party package is required. Pitches use
MIDI numbers (middle C=60); the FL Studio octave display is one octave higher
than many other DAWs. The drum map is the custom 32-pad map, not General MIDI.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import shutil
import struct
import tempfile

from harmony import KEYS, SCALES, CHORDS, NOTE_NAMES, build_progression, validate_progression

BASE = Path(__file__).resolve().parent
PPQ = 480
BARS = 8
BEATS = 32
ROLES = ("DRUMS", "PAD", "PLUCK", "CHORDS", "ARPEGGIO", "LEAD", "BASS", "SUB_BASS", "NOISE")
DRUM_MAP = {"kick": 36, "snare": 40, "clap": 44, "closed_hat": 48,
            "open_hat": 52, "ride": 56, "shaker": 60, "tom": 64}
GM_DRUM_MAP = {"kick": 36, "snare": 38, "clap": 39, "closed_hat": 42,
               "open_hat": 46, "ride": 51, "shaker": 70, "tom": 45}
DEFAULT_CONFIG = {"genre": "house", "key": "A", "mode": "minor", "bpm": None,
                  "progression": "", "seed": 42, "density": 0.5, "swing": None,
                  "variation": 0.3, "drum_variants": True, "chord_style": "block",
                  "lead_style": "lead", "muted_roles": [], "auto_chords": False,
                  "chord_creativity": .5}


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
    c["chord_creativity"] = _number(c["chord_creativity"], "Chord creativity", 0, 1)
    if not isinstance(c["auto_chords"], bool):
        raise ValueError("auto_chords must be true or false.")
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
    return build_progression(c)


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


# Each cell lasts two beats. Choosing a small number of coherent cells gives a
# seed a real rhythmic identity without scattering independent random notes.
# Recipe positions remain part of the vocabulary, alongside these variations.
RHYTHM_CELLS = {
    "hook": ((0,), (.5,), (0, 1), (0, 1.5), (.5, 1.5),
             (0, .75), (.5, 1), (0, .5, 1.5), (0, .75, 1.5), (.75, 1.5)),
    "bounce": ((.5,), (.5, 1.5), (.5, 1.25), (0, 1.5), (.5, 1.75),
               (.5, .75, 1.5), (.5, 1.5, 1.75), (0, .75, 1.5), (.75, 1.5)),
    "rolling": ((.5, .75, 1.5), (.5, 1.5, 1.75), (.5, .75, 1.5, 1.75),
                (0, .5, 1.5), (.5, 1, 1.5), (.5, 1.25, 1.75), (.5, 1.5)),
    "sparse": ((0,), (.5,), (1,), (0, 1.5), (.5, 1.5), (0, .75),
               (.75, 1.5), (0, 1.75), (.5, 1.25)),
    "pulse": ((0, 1), (0, .5, 1), (0, 1, 1.5), (0, .75, 1),
              (0, 1, 1.75), (0,), (0, .5, 1.5)),
}
_BASS_FEEL = {"trance": "bounce", "hard_dance": "bounce",
              "hard_trance": "rolling", "house": "hook", "hard_house": "bounce",
              "hardstyle": "pulse", "pop": "hook", "rap": "sparse"}


def _rhythm_phrase(c, r, role, rng):
    """An eight-beat idea, composed from four reusable half-bar cells."""
    if role == "BASS":
        feel, recipe = _BASS_FEEL[c["genre"]], r["bass"]
    elif role == "CHORDS":
        feel, recipe = "bounce" if c["genre"] in ("house", "hard_house") else "sparse", r["chord"]
    else:
        feel = "sparse" if c["genre"] in ("rap", "pop") else "hook"
        if role == "PLUCK" and c["genre"] in ("house", "hard_house", "hard_dance"):
            feel = "bounce"
        recipe = r["lead"] if role == "LEAD" else r["pluck"]
    hits = []
    for cell in range(4):
        recipe_cell = tuple(h % 2 for h in recipe if int(h // 2) == cell % 2)
        choices = RHYTHM_CELLS[feel] + ((recipe_cell,) if recipe_cell else ())
        chosen = list(rng.choice(choices))
        if c["density"] < .5:
            chosen = [h for j, h in enumerate(chosen)
                      if j == 0 or rng.random() < c["density"] * 1.5]
        elif c["density"] > .65 and rng.random() < (c["density"]-.5):
            chosen.append(rng.choice((.25, .75, 1.25, 1.75)))
        hits.extend(cell * 2 + h for h in chosen)
    return sorted(set(hits))


def _arp_phrase(c, r, rng):
    step = r["arp_step"] * (2 if c["density"] < .35 else 1)
    positions = [j*step for j in range(round(8/step))]
    # A different rest window and pulse mask for every idea. The pulse remains
    # on the genre's eighth/sixteenth grid; it is not timing humanization.
    rest_cell = rng.randrange(3)
    mask = rng.choice(((1, 1, 1, 0), (1, 0, 1, 1), (1, 1, 0, 1), (1, 1, 1, 1)))
    hits = [h for j, h in enumerate(positions)
            if (int(h//2) != rest_cell or c["density"] > .8)
            and mask[j % 4]]
    if not any(h >= 4 for h in hits):
        hits.append(6)
    return sorted(set(hits))


def _phrase_plan(c, make_phrase, rng):
    """A / A-prime / B / A-ending: repetition within a fresh seeded idea."""
    a, b = make_phrase(), make_phrase()
    a_prime, ending = list(a), list(a)
    if c["variation"] > 0:
        detail = make_phrase()
        tail = 6 if c["variation"] < .65 else 4
        a_prime = [h for h in a if h < tail] + [h for h in detail if h >= tail]
        # Finishing earlier leaves a little space before looping around.
        ending = [h for h in a if h < 6] + [rng.choice((6, 6.5, 7))]
        if c["variation"] > .65:
            ending.append(7.5)
    return (a, sorted(set(a_prime)), b, sorted(set(ending)))


def _drum_phrase(c, r, family, rng):
    """Two bars of percussion. Genre-defining anchors are never randomized away."""
    edm = c["genre"] not in ("pop", "rap")
    recipe_phrase = [bar*4+h for bar in range(2) for h in r["drums"].get(family, [])]
    if family == "kick" and edm:
        return sorted(set(range(8)) | set(recipe_phrase))
    if family == "snare":
        return sorted({1, 3, 5, 7} | set(recipe_phrase))
    if family == "clap":
        return sorted(set(recipe_phrase) | ({1, 3, 5, 7} if edm or rng.randrange(3) == 0 else set()))
    hits = []
    for bar in range(2):
        if family == "kick":
            anchors = [0, 2] if c["genre"] == "pop" else [0]
            pool = (.75, 1.5, 2.75, 3.5, 3.75) if c["genre"] == "pop" else (.75, 1.5, 1.75, 2.5, 2.75, 3.5)
            count = 1 + int(c["density"] >= .4) + int(c["density"] > .8)
            local = anchors + rng.sample(pool, count)
        elif family == "open_hat":
            if edm:
                local = [.5] + [h for h in (1.5, 2.5, 3.5)
                                if rng.random() < .35 + c["density"]*.6]
            else:
                candidates = (1.5, 3.5) if c["genre"] == "pop" else (.75, 2.75, 3.5)
                local = [rng.choice(candidates)]
        elif family == "closed_hat":
            local = []
            for half in range(2):
                cells = ((0, 1), (0, .5, 1), (0, 1, 1.5), (0, .5, 1, 1.5),
                         (0, .75, 1, 1.75), (0, .25, 1, 1.25))
                if c["genre"] in ("hard_dance", "hard_trance", "hard_house", "rap"):
                    cells += ((0, .25, .75, 1, 1.25, 1.75),)
                cell = rng.choice(cells)
                local.extend(half*2+h for h in cell
                             if h in (0, 1) or rng.random() < .35+c["density"])
                if c["density"] > .8:
                    local.extend((half*2+.25, half*2+1.75))
        elif family == "shaker":
            cell = rng.choice(((.25, 1.25), (.75, 1.75), (.25, .75, 1.75),
                               (.25, 1.25, 1.75), (.5, 1.5)))
            local = [half*2+h for half in range(2) for h in cell]
            local = _thin_hits(local, c["density"], rng)
        elif family == "ride":
            local = list(rng.choice(((0, 2), (.5, 2.5), (1.5, 3.5), (0,), (2,))))
        else:  # Tom fills are used only at the ends of four-bar phrases.
            pool = sorted(set(r["drums"].get("tom", []) + [2.5, 2.75, 3, 3.25, 3.5, 3.75]))
            count = 1 + int(c["density"] >= .4) + int(c["variation"] > .6)
            local = sorted(rng.sample(pool, count))
        # User-editable recipes are one of the seed's choices, not a rhythm
        # that every seed is forced to repeat. Preserve kick anchors in pop/rap.
        recipe_hits = r["drums"].get(family, [])
        if recipe_hits and family != "tom" and rng.randrange(5) == 0:
            local = list(recipe_hits)
            if family == "kick":
                local = sorted(set(local + anchors))
        hits.extend(bar*4+h for h in local)
    return sorted(set(hits))


def _drums(c, r):
    rng = _rng(c, "DRUMS:velocity")
    notes = []
    velocities = {"kick": 103, "snare": 94, "clap": 70, "closed_hat": 63,
                  "open_hat": 59, "ride": 48, "shaker": 43, "tom": 73}
    families = {}
    for family in DRUM_MAP:
        rhythm_rng = _rng(c, "DRUMS:"+family)
        make = lambda: _drum_phrase(c, r, family, rhythm_rng)
        a, b = make(), make()
        detail = make()
        # Keep kick/snare/clap anchors intact, including in the final bar.
        a_prime = ([h for h in a if h < 6] + [h for h in detail if h >= 6]
                   if c["variation"] > 0 else a)
        families[family] = (a, a_prime, b, a_prime)
    for bar in range(8):
        for family, base in DRUM_MAP.items():
            phrase = families[family][bar//2]
            hits = [h % 4 for h in phrase if int(h//4) == bar % 2]
            if family == "ride" and bar not in (2, 6, 7):
                hits = []
            if family == "tom":
                hits = hits if bar in (3, 7) else []
            # Leave room for an open hat instead of triggering closed and open together.
            if family == "closed_hat":
                open_hits = {h % 4 for h in families["open_hat"][bar//2]
                             if int(h//4) == bar % 2}
                hits = [h for h in hits if h not in open_hits]
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
    # Keep note-off timing consistent when the four sound variants are toggled
    # off. Otherwise overlapping tom samples shorten only in single-sound mode.
    for family in DRUM_MAP:
        family_notes = sorted((n for n in notes if n["family"] == family), key=lambda n:n["start"])
        for a, b in zip(family_notes, family_notes[1:]):
            a["duration"] = min(a["duration"], b["start"]-a["start"])
    return notes


def _chord_start(chord):
    return round(chord["bar"] * 4 * PPQ) / PPQ


def _chord_end(chord):
    return round((chord["bar"] + chord["bars"]) * 4 * PPQ) / PPQ


def _chord_index(progression, onset):
    # Resolve after swing and tick rounding, not before, so a displaced note
    # cannot accidentally use the previous chord at a rapid harmonic change.
    tick = round(onset * PPQ)
    return next(i for i, ch in enumerate(progression)
                if round(ch["bar"] * 4 * PPQ) <= tick
                < round((ch["bar"] + ch["bars"]) * 4 * PPQ))


def _common_tone_tails(notes, progression, creativity):
    """Carry only shared tones; merge repeated pitches into unambiguous ties.

    A voice common to consecutive chords may ring through the transition.
    New chord roots and changing voices still arrive on time. Identical MIDI
    pitches become one held note rather than overlapping note-on/off pairs.
    """
    amount = max(0.0, (creativity - .35) / .65)
    if not amount:
        return notes
    for chord, following in zip(progression, progression[1:]):
        start, boundary = _chord_start(chord), _chord_end(chord)
        shared = set(chord["pitch_classes"]) & set(following["pitch_classes"])
        if not shared:
            continue
        tail = min(.125 + amount * .625, (_chord_end(following) - boundary) * .45)
        tail = round(tail * 96) / 96
        # One common voice appears first; higher settings carry more voices.
        latest = {}
        for note in notes:
            if start <= note["start"] < boundary and note["pitch"] % 12 in shared:
                latest[note["pitch"]] = note
        candidates = sorted(latest.values(), key=lambda n: (n["pitch"] % 12 != chord["root"] % 12, n["pitch"]))
        count = min(len(candidates), max(1, math.ceil(len(candidates) * amount)))
        for note in candidates[:count]:
            # Rhythmic stabs may have stopped early: sustain selected common
            # voices into the next chord as the creativity control rises.
            note["duration"] = boundary + tail - note["start"]
            note["tie_boundaries"] = [boundary]
    merged, last = [], {}
    for note in sorted(notes, key=lambda n: (n["start"], n["pitch"])):
        old = last.get(note["pitch"])
        if (old is not None and old.get("tie_boundaries")
                and old["start"] + old["duration"] >= note["start"] - .5 / PPQ):
            old["duration"] = max(old["start"] + old["duration"], note["start"] + note["duration"]) - old["start"]
            old["tie_boundaries"] = sorted(set(old["tie_boundaries"] + note.get("tie_boundaries", [])))
            continue
        merged.append(note)
        last[note["pitch"]] = note
    return merged


def _melody(c, r, progression, role):
    rng = _rng(c, role+":velocity")
    rhythm_rng, pitch_rng = _rng(c, role+":rhythm"), _rng(c, role+":pitch")
    notes = []
    make = (lambda: _arp_phrase(c, r, rhythm_rng)) if role == "ARPEGGIO" else (lambda: _rhythm_phrase(c, r, role, rhythm_rng))
    phrases = _phrase_plan(c, make, rhythm_rng)
    # A small bounded walk creates a singable contour. Store positions by
    # half-beat so repeated rhythm cells remember their melodic identity.
    contours = []
    for _ in range(2):
        contour, degree = [], pitch_rng.randrange(3)
        for i in range(16):
            if i:
                degree = max(0, min(5, degree+pitch_rng.choice((-1, -1, 0, 1, 1, 2))))
            contour.append(degree)
        contours.append(contour)
    contour_a, contour_b = contours
    contour_prime = list(contour_a)
    if c["variation"] > 0:
        for i in range(12, 16):
            contour_prime[i] = max(0, min(5, contour_prime[i]+pitch_rng.choice((-1, 1))))
    phrase_contours = (contour_a, contour_prime, contour_b, contour_a)
    arp_vocabulary = ((0, 1, 2, 1), (0, 2, 1, 2), (2, 1, 0, 1),
                      (0, 1, 2, 0), (0, 2, 0, 1), (1, 0, 2, 0),
                      (0, 1, 2), (0, 2, 1), (1, 0, 2),
                      (1, 2, 0), (2, 0, 1), (2, 1, 0))
    arp_a, arp_b = pitch_rng.choice(arp_vocabulary), pitch_rng.choice(arp_vocabulary)
    arp_orders = (arp_a, arp_a, arp_b, arp_a)
    gate = pitch_rng.choice((.55, .7, .85))
    # New sub-bar harmony uses the common 96-PPQ grid shared by the FLP and
    # MIDI exporters. Legacy bar-aligned manual ideas retain their 480 grid.
    sub_bar = c["auto_chords"] or any(abs(ch["bar"] - round(ch["bar"])) > 1e-8 for ch in progression)
    grid = 96 if sub_bar else PPQ
    voices, voice = [], None
    voice_rng = _rng(c, role + ":voicing")
    if role in ("PAD", "CHORDS"):
        for chord in progression:
            center = 61
            if c["auto_chords"]:
                center += voice_rng.choice((-2, 0, 2)) * c["chord_creativity"]
            voice = _voicing(chord["pitch_classes"], voice, center=center)
            voices.append(voice)
    sustained = role == "PAD" or (role == "CHORDS" and c["chord_style"] == "block")
    if sustained:
        for index, chord in enumerate(progression):
            start, end = _chord_start(chord), _chord_end(chord)
            gap = .04 if role == "PAD" else .10
            for pitch in voices[index]:
                duration = max(1 / grid, end - start - min(gap, (end-start) / 8))
                duration = max(1 / grid, round(duration * grid) / grid)
                _add(notes, start, min(end-start, duration), pitch,
                     (47 if role == "PAD" else 64) + rng.randint(-3, 3))
    else:
        # Compose all rhythmic attacks before assigning harmony. A rhythm can
        # span any number of chords; no integer-bar loop limits chord counts.
        hits = {}
        for bar in range(BARS):
            local_hits = [h % 4 for h in phrases[bar//2] if int(h//4) == bar % 2]
            for j, hit in enumerate(local_hits):
                nominal = bar * 4 + hit
                onset = round(_swing(nominal, c["swing"]) * grid) / grid
                if onset < BEATS:
                    hits[onset] = (nominal, j)
        if role == "SUB_BASS":
            hits = {float(beat): (float(beat), 0) for beat in range(0, BEATS, 4)}
        for chord in progression:
            start, end = _chord_start(chord), _chord_end(chord)
            if (role == "SUB_BASS" or (role == "BASS" and sub_bar)
                    or not any(start <= hit < end for hit in hits)):
                hits.setdefault(start, (start, 0))
        attacks = sorted(hits)
        for attack_index, onset in enumerate(attacks):
            nominal, phrase_index = hits[onset]
            chord_index = _chord_index(progression, onset)
            chord = progression[chord_index]
            pcs, root_pc = chord["pitch_classes"], chord["root"] % 12
            end = _chord_end(chord)
            next_onset = attacks[attack_index+1] if attack_index+1 < len(attacks) else BEATS
            bar = min(7, int(nominal // 4))
            available = min(next_onset, end) - onset
            if available < .5 / grid:
                continue
            if role in ("BASS", "SUB_BASS"):
                pitch = _near(root_pc, 33 if role == "SUB_BASS" else 45,
                              24 if role == "SUB_BASS" else 36,
                              43 if role == "SUB_BASS" else 55)
                duration = min(3.75, available * .97) if role == "SUB_BASS" else min(.65, max(.1, available * gate))
                velocity = 75 if role == "SUB_BASS" else 85 + rng.randint(-4, 4)
                pitches = [pitch]
            elif role == "CHORDS":
                pitches, duration = voices[chord_index], .32
                velocity = 62 + rng.randint(-3, 3)
            elif role == "ARPEGGIO":
                order = arp_orders[bar//2]
                # At richer settings the arp can address added upper tones.
                index = order[phrase_index % len(order)]
                if c["auto_chords"] and len(pcs) > 3:
                    index = (index + (phrase_index // len(order))) % len(pcs)
                pitches = [_near(pcs[index % len(pcs)], 72, 60, 84)]
                duration = min(.3, r["arp_step"] * .65, available * .65)
                velocity = 46 + (phrase_index % 3) * 3
            else:
                contour = phrase_contours[bar//2]
                degree = contour[min(15, int((nominal % 8) * 2))]
                if attack_index == len(attacks)-1:
                    degree = 0
                pc = pcs[degree % len(pcs)]
                root = _near(root_pc, 65 if role == "LEAD" else 72, 60, 76)
                target = root + (degree // len(pcs)) * 12 + (pc-root_pc) % 12
                pitches = [_near(pc, target, 60 if role == "LEAD" else 64, 83 if role == "LEAD" else 88)]
                limit = .2 if role == "PLUCK" or c["lead_style"] == "pluck" else 1.35
                duration = min(limit, max(.1, available * gate))
                velocity = (76 if role == "LEAD" else 58) + rng.randint(-4, 4)
            duration = min(available, max(1 / grid, round(duration * grid) / grid))
            for pitch in pitches:
                _add(notes, onset, duration, pitch, velocity)
    if c["auto_chords"] and role in ("PAD", "CHORDS"):
        notes = _common_tone_tails(notes, progression, c["chord_creativity"])
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


def generate(config=None, *, progression_override=None):
    """Create eight bars; optional exact harmony supports kept melodic parts.

    All note starts/durations are in beats. An override is deep-copied and
    validated, so callers can preserve one saved harmonic timeline safely.
    """
    c = validate_config(config or {})
    r = GENRES[c["genre"]]
    progression = _progression(c) if progression_override is None else copy.deepcopy(progression_override)
    validate_progression(progression)
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
    arrangement = {"schema_version":1,"engine_version":"3.0-chord-creativity",
                   "bars":BARS,"beats_per_bar":4,"ppq":PPQ,
                   "bpm":c["bpm"],"key":c["key"],"mode":c["mode"],"seed":c["seed"],
                   "genre":c["genre"],"config":c,"progression":progression,"tracks":tracks,
                   "drum_map":DRUM_MAP,
                   "notes":"Generic, editable genre starting points, not artist transcriptions. NOISE is a sampler/synth trigger, not a pitched accompaniment. Custom drum map; use gm/DRUMS.mid for a General MIDI kit. MIDI exports do not send program changes."}
    validate_arrangement(arrangement)
    return arrangement


def validate_arrangement(a):
    if (not isinstance(a, dict) or type(a.get("bars")) is not int or a["bars"] not in (8, 32)
            or a.get("beats_per_bar") != 4 or a.get("ppq") != PPQ):
        raise ValueError("This version supports eight or 32 bars of 4/4 at 480 PPQ.")
    beats = a["bars"] * 4
    _number(a.get("bpm"),"BPM",30,300)
    validate_progression(a.get("progression"), bars=a["bars"])
    tracks = a.get("tracks")
    if not isinstance(tracks, list) or any(not isinstance(t, dict) for t in tracks):
        raise ValueError("Arrangement tracks must be a list of instrument parts.")
    roles = [t.get("role") for t in tracks]
    if any(type(role) is not str for role in roles):
        raise ValueError("Every instrument part needs a role name.")
    if sorted(roles) != sorted(ROLES):
        raise ValueError("Arrangement must contain each of the nine roles once.")
    for track in a["tracks"]:
        if type(track.get("channel")) is not int or not 0 <= track["channel"] < 16:
            raise ValueError("MIDI channel out of range.")
        if not isinstance(track.get("notes"), list) or not track["notes"]:
            raise ValueError(f"{track['role']} has no notes.")
        for n in track["notes"]:
            if not isinstance(n, dict) or not all(k in n for k in ("start", "duration", "pitch", "velocity")):
                raise ValueError("Every MIDI note needs start, duration, pitch and velocity.")
            _number(n["start"],"Note start",0,beats-1/PPQ)
            _number(n["duration"],"Note duration",1/PPQ,beats)
            if n["start"]+n["duration"] > beats+1e-8:
                raise ValueError(f"A note extends past bar {a['bars']}.")
            for field,lo,hi in (("pitch",0,127),("velocity",1,127)):
                if isinstance(n[field],bool) or not isinstance(n[field],int) or not lo <= n[field] <= hi:
                    raise ValueError(f"Invalid MIDI {field}.")
        if max(n["start"]+n["duration"] for n in track["notes"]) <= beats-4:
            raise ValueError(f"{track['role']} needs musical content in the final bar for FLP auto-length.")
    if "sections" in a:
        sections = a["sections"]
        if (a["bars"] != 32 or not isinstance(sections, list) or len(sections) != 4
                or any(not isinstance(s, dict) or type(s.get("id")) is not str for s in sections)
                or [s.get("bar") for s in sections] != [0, 8, 16, 24]
                or any(s.get("bars") != 8 for s in sections)
                or sorted(s.get("id", "") for s in sections) != list("ABCD")):
            raise ValueError("A song needs four distinct eight-bar sections, A through D.")
    if "section_bank" in a:
        if a["bars"] != 32 or not isinstance(a["section_bank"], dict) or set(a["section_bank"]) != set("ABCD"):
            raise ValueError("A song section bank must contain A, B, C and D.")
        for section in a["section_bank"].values():
            if (not isinstance(section, dict) or not isinstance(section.get("arrangement"), dict)
                    or section["arrangement"].get("bars") != 8):
                raise ValueError("Each bank section must remain eight bars.")
            validate_arrangement(section["arrangement"])


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


def _chunk(events, end_tick=BEATS*PPQ):
    body = bytearray()
    tick = 0
    for at,order,data in sorted(events,key=lambda event:(event[0],event[1])):
        body += _vlq(at-tick)+data
        tick = at
    body += _vlq(end_tick-tick)+_meta(0x2f,b"")
    return b"MTrk"+struct.pack(">I",len(body))+body


def midi_bytes(arrangement, tracks=None, gm_drums=False):
    """Standard MIDI type 1, no program changes, exact arrangement end markers."""
    validate_arrangement(arrangement)
    tracks = arrangement["tracks"] if tracks is None else tracks
    end_tick = arrangement["bars"] * 4 * PPQ
    conductor = [(0,0,_meta(3,f"Plain MIDI - {arrangement['bars']} bars".encode("ascii"))),
                 (0,1,_meta(0x51,round(60000000/arrangement["bpm"]).to_bytes(3,"big"))),
                 (0,2,_meta(0x58,bytes([4,2,24,8])))]
    for chord in arrangement["progression"]:
        conductor.append((round(chord["bar"]*4*PPQ),3,_meta(6,chord["symbol"].encode("utf-8"))))
    for section in arrangement.get("sections", []):
        label = f"Section {section['id']}: {section.get('label', section['id'])}"
        conductor.append((round(section["bar"]*4*PPQ),2,_meta(6,label.encode("utf-8"))))
    chunks = [_chunk(conductor, end_tick)]
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
        chunks.append(_chunk(events, end_tick))
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
        if "section_bank" in arrangement:
            (stage/"sections").mkdir()
            for identity, section in arrangement["section_bank"].items():
                (stage/"sections"/(identity+".mid")).write_bytes(midi_bytes(section["arrangement"]))
            (stage/"sections"/"order.json").write_text(json.dumps(arrangement["sections"], indent=2)+"\n", encoding="utf-8")
        text = f"PLAIN MIDI - {arrangement['bars']}-BAR {'SONG' if arrangement['bars'] == 32 else 'STARTER'}\n\n"
        text += f"{GENRES[arrangement['genre']]['label']} | {arrangement['key']} {arrangement['mode']} | {arrangement['bpm']} BPM\n"
        text += "Chords: " + " | ".join(c["symbol"] for c in arrangement["progression"])+"\n\n"
        text += f"Import arrangement.mid for all nine parts, or parts/*.mid individually.\nThese files start at beat zero and end at {arrangement['bars']} bars. No MIDI program changes.\n"
        if "section_bank" in arrangement:
            text += "sections/A.mid through D.mid are the individual eight-bar sections.\n"
            text += "Song order: " + " -> ".join(arrangement["section_order"]) + ". Shuffle rearranges these exact sections.\n"
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
    parser.add_argument("--auto-chords", action="store_true", default=None,
                        help="Generate a new chord progression from the seed.")
    parser.add_argument("--chord-creativity", type=float,
                        help="0 to 1: more changes, richer harmony and common-tone overlaps.")
    parser.add_argument("--seed",type=int)
    args = parser.parse_args()
    if args.list_genres:
        for slug,recipe in GENRES.items():
            print(f"{slug:22s} {recipe['bpm']:3d} BPM  {recipe['label']}")
        return
    config = json.loads(args.settings.read_text(encoding="utf-8")) if args.settings else {}
    for name in ("genre","key","mode","bpm","progression","seed","auto_chords","chord_creativity"):
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
