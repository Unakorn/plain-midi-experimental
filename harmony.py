"""Deterministic eight-bar harmony: manual progressions and creative tonal arcs.

The shared chord timeline never overlaps. Sustained instrument voices may
carry common tones across its boundaries; the renderer controls those tails.
At high creativity, explicitly marked borrowed and secondary-dominant chords
can contain tones outside the selected home scale.
"""
from __future__ import annotations

import hashlib
import math
import random
import re

BEATS = 32
HARMONY_PPQ = 96  # Exact in both the 96-PPQ FLP and the 480-PPQ MIDI export.
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
          "m11": (0, 3, 7, 10, 14, 17), "13": (0, 4, 7, 10, 14, 21),
          "mMaj7": (0, 3, 7, 11), "mMaj9": (0, 3, 7, 11, 14),
          "maj7#5": (0, 4, 8, 11), "maj9#5": (0, 4, 8, 11, 14),
          "7sus4": (0, 5, 7, 10)}
NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")

# These are home/departure/contrast/return scaffolds, expanded by contextual
# approaches and harmonic-rhythm subdivision, rather than complete output loops.
_MAJOR_ARCS = ((1, 6, 4, 5), (1, 4, 2, 5), (1, 3, 6, 4), (1, 5, 6, 4),
               (1, 2, 4, 5), (1, 6, 2, 5), (1, 4, 5, 1), (1, 3, 4, 5))
_MINOR_ARCS = ((1, 6, 3, 7), (1, 4, 6, 5), (1, 7, 6, 7), (1, 3, 4, 5),
               (1, 6, 4, 5), (1, 7, 4, 1), (1, 4, 7, 3), (1, 3, 6, 7))
_GENRE_ARCS = {
    "trance": ((1, 6, 3, 7), (1, 4, 6, 5)),
    "hard_dance": ((1, 7, 6, 7), (1, 6, 4, 5)),
    "hard_trance": ((1, 6, 7, 1), (1, 4, 6, 5)),
    "house": ((1, 6, 2, 5), (1, 4, 7, 3)),
    "hard_house": ((1, 7, 6, 5), (1, 4, 6, 7)),
    "hardstyle": ((1, 6, 3, 7), (1, 3, 4, 5)),
    "pop": ((1, 5, 6, 4), (1, 6, 4, 5)),
    "rap": ((1, 4, 6, 5), (1, 6, 3, 7)),
}


def _rng(config, purpose):
    token = "|".join(str(config.get(k, "")) for k in ("seed", "genre", "key", "mode"))
    digest = hashlib.sha256((token + "|HARMONY|" + purpose).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _chord(root, suffix, source="diatonic"):
    return {"symbol": NOTE_NAMES[root] + suffix, "root": 48 + root,
            "pitch_classes": list(dict.fromkeys((root+n) % 12 for n in CHORDS[suffix])),
            "source": source}


def _degree_chord(degree, tonic, scale):
    root = (tonic + scale[degree]) % 12
    pcs = [(tonic + scale[(degree+n) % 7]) % 12 for n in (0, 2, 4)]
    intervals = tuple((pc-root) % 12 for pc in pcs)
    suffix = next(q for q in ("", "m", "dim", "aug") if CHORDS[q] == intervals)
    return _chord(root, suffix)


def _parse_token(token, tonic, scale):
    if re.fullmatch(r"[1-7]", token):
        result = _degree_chord(int(token)-1, tonic, scale)
        result["source"] = "manual"
        return result
    match = re.fullmatch(r"([A-Ga-g])([#b]?)(.*)", token)
    if not match or match[3] not in CHORDS:
        raise ValueError(f"Unknown chord '{token}'. Use degrees 1–7 or symbols such as Am, Fmaj7, G7, Dsus2.")
    note, suffix = match[1].upper() + match[2], match[3]
    if note not in KEYS:
        raise ValueError(f"Unsupported chord root '{note}'. Use its enharmonic equivalent.")
    result = _chord(KEYS[note], suffix, "manual")
    result["symbol"] = note + suffix  # Respect explicitly typed enharmonic names.
    return result


def _with_time(chord, start_tick, end_tick):
    start, duration = start_tick/HARMONY_PPQ, (end_tick-start_tick)/HARMONY_PPQ
    return {"bar": start/4, "bars": duration/4, **chord,
            "start": start, "duration": duration}


def _manual(config):
    tokens = [t for t in re.split(r"[\s,|]+", config["progression"].strip()) if t]
    if not 1 <= len(tokens) <= 32:
        raise ValueError("Enter between 1 and 32 chords/degrees; they divide the eight bars equally.")
    tonic, scale = KEYS[config["key"]], SCALES[config["mode"]]
    boundaries = [round(i*BEATS*HARMONY_PPQ/len(tokens)) for i in range(len(tokens)+1)]
    return [_with_time(_parse_token(token, tonic, scale), boundaries[i], boundaries[i+1])
            for i, token in enumerate(tokens)]


def chord_count(config):
    """Seeded, monotonic density curve used by the auto-harmony timeline."""
    rng = _rng(config, "count")
    levels = (4, rng.randint(6, 8), rng.randint(9, 12),
              rng.randint(15, 20), rng.randint(24, 32))
    amount = config["chord_creativity"] * 4
    index = min(3, int(amount))
    fraction = amount-index
    return round(levels[index] + (levels[index+1]-levels[index])*fraction)


def _boundaries(config, count):
    """More creativity refines the same seed's rhythmic skeleton."""
    rng = _rng(config, "timing")
    boundaries = [0, 8, 16, 24, 32]
    while len(boundaries)-1 < count:
        candidates = [(a, b) for a, b in zip(boundaries, boundaries[1:]) if b-a > 1]
        a, b = rng.choices(candidates, weights=[(b-a)**1.6 for a, b in candidates])[0]
        cuts = list(range(a+1, b))
        weights = [(2 if cut % 2 == 0 else 1)/(1+abs(cut-(a+b)/2)) for cut in cuts]
        boundaries.append(rng.choices(cuts, weights=weights)[0])
        boundaries.sort()
    return boundaries


def _degree_sequence(config, boundaries):
    rng = _rng(config, "degrees")
    scale = SCALES[config["mode"]]
    minor = scale[2] == 3
    bank = (_MINOR_ARCS if minor else _MAJOR_ARCS) + _GENRE_ARCS.get(config["genre"], ())
    anchors = [d-1 for d in rng.choice(bank)]
    degrees = []
    for i, start in enumerate(boundaries[:-1]):
        span = int(start//8)
        target = anchors[span+1] if span < 3 else 0
        if start % 8 == 0:
            degree = anchors[span]
        elif boundaries[i+1] % 8 == 0:
            # A diatonic fifth relation leads toward the next harmonic area.
            degree = (target+4) % 7
        else:
            previous = degrees[-1]
            choices = [(previous+3) % 7, (previous+5) % 7,
                       (previous+1) % 7, target, anchors[span], (target+1) % 7]
            choices = [d for d in choices if d != previous]
            degree = rng.choice(choices)
        if degrees and degree == degrees[-1]:
            choices = [d for d in ((target+4) % 7, (degree+3) % 7, (degree+5) % 7)
                       if d != degrees[-1]]
            degree = rng.choice(choices)
        degrees.append(degree)
    degrees[0] = 0
    if len(degrees) >= 6:
        degrees[-2:] = [4, 0]
    return degrees


def _enrich(base, config, rng, *, stable=False):
    creativity = config["chord_creativity"]
    if creativity < .15:
        return base
    root = base["root"] % 12
    scale_pcs = {(KEYS[config["key"]]+n) % 12 for n in SCALES[config["mode"]]}
    base_intervals = {(pc-root) % 12 for pc in base["pitch_classes"]}
    candidates, weights = [], []
    for suffix, intervals in CHORDS.items():
        pcs = {(root+n) % 12 for n in intervals}
        if not pcs <= scale_pcs:
            continue
        if suffix in ("sus2", "sus4"):
            if stable or creativity < .25:
                continue
            weight = .35 + creativity
        elif not base_intervals <= {n % 12 for n in intervals}:
            continue
        else:
            voices = len(set(n % 12 for n in intervals))
            if voices > 4 and creativity < .6:
                continue
            if voices == 3:
                weight = max(.15, 2.5-3*creativity)
            elif voices == 4:
                weight = 1+creativity*2
            else:
                weight = (creativity-.5)*7
        candidates.append(suffix)
        weights.append(weight)
    if not candidates:
        return base
    return _chord(root, rng.choices(candidates, weights=weights)[0])


def _automatic(config):
    count = chord_count(config)
    boundaries = _boundaries(config, count)
    degrees = _degree_sequence(config, boundaries)
    tonic, scale = KEYS[config["key"]], SCALES[config["mode"]]
    creativity = config["chord_creativity"]
    rng = _rng(config, "colors")
    chords = [_enrich(_degree_chord(degree, tonic, scale), config, rng,
                      stable=i in (0, count-1)) for i, degree in enumerate(degrees)]
    chromatic = max(0, (creativity-.65)/.35)
    # Borrowed colors are sparse, so a highly active timeline still has a home.
    for i in range(1, count-2):
        if rng.random() < .055*chromatic:
            choices = ((5, "m"), (10, ""), (8, "maj7")) if scale[2] == 4 else ((5, ""), (7, "7"))
            interval, suffix = rng.choice(choices)
            chords[i] = _chord((tonic+interval) % 12, suffix, "borrowed")
    # Work backwards so every inserted dominant targets the actual next root.
    for i in range(count-3, 0, -1):
        if rng.random() < .13*chromatic:
            target = chords[i+1]["root"] % 12
            chords[i] = _chord((target+7) % 12, "9" if creativity > .9 and rng.random() < .4 else "7", "secondary_dominant")
            chords[i]["secondary_target"] = target
    if count >= 6 and creativity >= .55:
        # A brief dominant-to-home cadence also works in natural minor; the
        # raised leading tone is intentional and identified in the metadata.
        chords[-2] = _chord((tonic+7) % 12, "7", "cadential_dominant")
    # Resolve/repair right to left: changing a target can only affect an
    # approach that we have yet to visit. This preserves both the final home
    # chord and every explicitly marked secondary-dominant relationship.
    for i in range(count-2, 0, -1):
        if chords[i].get("source") == "secondary_dominant":
            target = chords[i+1]["root"] % 12
            chords[i] = _chord((target+7) % 12, "7", "secondary_dominant")
            chords[i]["secondary_target"] = target
        if set(chords[i]["pitch_classes"]) == set(chords[i+1]["pitch_classes"]):
            options = [_degree_chord(d, tonic, scale) for d in range(7)]
            chords[i] = rng.choice([ch for ch in options
                                    if set(ch["pitch_classes"]) != set(chords[i+1]["pitch_classes"])])
    if set(chords[0]["pitch_classes"]) == set(chords[1]["pitch_classes"]):
        options = [_degree_chord(d, tonic, scale) for d in range(1, 7)]
        chords[1] = rng.choice([ch for ch in options
                                if set(ch["pitch_classes"]) != set(chords[2]["pitch_classes"])])
    return [_with_time(chord, boundaries[i]*HARMONY_PPQ, boundaries[i+1]*HARMONY_PPQ)
            for i, chord in enumerate(chords)]


def validate_progression(progression, *, bars=8):
    """Check an eight-bar idea or an assembled 32-bar harmonic timeline."""
    if type(bars) is not int or bars not in (8, 32):
        raise ValueError("A chord timeline must be eight or 32 bars.")
    beats = bars * 4
    if not isinstance(progression, list) or not 1 <= len(progression) <= bars * 4:
        raise ValueError(f"A {bars}-bar chord timeline needs between 1 and {bars*4} chords.")
    previous_end = 0
    for chord in progression:
        if not isinstance(chord, dict):
            raise ValueError("Each chord must be an object.")
        ticks = []
        for field in ("bar", "bars"):
            value = chord.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"Chord {field} must be a finite number.")
            raw = value*4*HARMONY_PPQ
            if abs(raw-round(raw)) > 1e-7:
                raise ValueError("Chord timing must align to the shared MIDI/FLP grid.")
            ticks.append(round(raw))
        start, duration = ticks
        if start != previous_end or duration <= 0 or start+duration > beats*HARMONY_PPQ:
            raise ValueError("Chords must form one contiguous, positive timeline.")
        for field, expected in (("start", start/HARMONY_PPQ), ("duration", duration/HARMONY_PPQ)):
            if field in chord:
                value = chord[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value-expected) > 1e-8:
                    raise ValueError("Chord beat timing disagrees with its bar timing.")
        root, pcs = chord.get("root"), chord.get("pitch_classes")
        if isinstance(root, bool) or not isinstance(root, int) or not 48 <= root <= 59:
            raise ValueError("Chord root must be a MIDI number from 48 to 59.")
        if (not isinstance(pcs, list) or not 2 <= len(pcs) <= 7
                or any(isinstance(pc, bool) or not isinstance(pc, int) or not 0 <= pc <= 11 for pc in pcs)
                or len(pcs) != len(set(pcs)) or pcs[0] != root % 12):
            raise ValueError("Chord pitch classes must be distinct, with the root first.")
        symbol = chord.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 40 or any(ord(c) < 32 for c in symbol):
            raise ValueError("Each chord needs a short printable symbol.")
        previous_end = start+duration
    if previous_end != beats*HARMONY_PPQ:
        raise ValueError(f"Chord timeline must end exactly at {bars} bars.")


def build_progression(config):
    """Create harmony from validated settings; isolated RNG leaves drums intact."""
    progression = _automatic(config) if config.get("auto_chords", False) else _manual(config)
    validate_progression(progression)
    return progression
