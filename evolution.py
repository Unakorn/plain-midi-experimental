"""Develop one eight-bar idea into four related, reorderable sections.

The source is retained verbatim as A. Variations reuse its actual melodic
attacks and chord-relative pitch shapes, while harmony and energy develop.
Reordering moves whole saved sections; it never generates replacement notes.
"""
from __future__ import annotations

import copy
import hashlib
import math
import random

import generator as g
from harmony import build_progression, validate_progression

SECTION_LABELS = {"A": "Theme", "B": "Lift", "C": "Contrast", "D": "Return"}
SECTION_BEATS = 32
GRID = 96
PITCHED = set(g.ROLES) - {"DRUMS", "NOISE"}


def _seed(value):
    if type(value) is not int or not 0 <= value <= 2147483647:
        raise ValueError("Evolution seed must be an integer from 0 to 2147483647.")
    return value


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Evolution amount must be a number from 0 to 1.")
    return value


def _rng(seed, identity, role):
    digest = hashlib.sha256(f"{seed}|EVOLVE|{identity}|{role}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _track(arrangement, role):
    return next(t for t in arrangement["tracks"] if t["role"] == role)


def _chord(progression, beat):
    return progression[g._chord_index(progression, beat)]


def _bounds(chord):
    return g._chord_start(chord), g._chord_end(chord)


def _different_harmony(first, second):
    return [(c["bar"], c["bars"], c["pitch_classes"]) for c in first] != [
        (c["bar"], c["bars"], c["pitch_classes"]) for c in second]


def _variant_harmony(source, identity, seed, amount, locked):
    original = source["progression"]
    if not amount or locked or identity == "D":
        return copy.deepcopy(original)
    rng = _rng(seed, identity, "harmony")
    config = {**source["config"], "auto_chords": True,
              "seed": rng.randrange(2147483648)}
    # The contrast remains close in harmonic density to the original idea;
    # the evolution slider controls how far the song moves from its theme.
    config["chord_creativity"] = min(1, config.get("chord_creativity", .5) + amount * .12)
    candidate = build_progression(g.validate_config(config))
    if identity == "C" and amount >= .35:
        return candidate
    result = copy.deepcopy(original)
    chance = amount * (.65 if identity == "B" else .9)
    candidates = []
    for index, old in enumerate(original):
        if index == 0:
            continue  # Keep a recognizable point of departure.
        start, end = _bounds(old)
        replacement = copy.deepcopy(_chord(candidate, (start + end) / 2))
        for key in ("bar", "bars", "start", "duration"):
            replacement[key] = old.get(key, start if key == "start" else end-start)
        # A sampled chord no longer necessarily has its original next chord.
        replacement.pop("secondary_target", None)
        replacement["source"] = "evolved"
        if replacement["pitch_classes"] != old["pitch_classes"]:
            candidates.append((index, replacement))
            if rng.random() < chance:
                result[index] = replacement
    # A nonzero evolution should not accidentally choose velocity changes only.
    if candidates and not _different_harmony(original, result):
        index, replacement = rng.choice(candidates)
        result[index] = replacement
    validate_progression(result)
    return result


def _mapped_pitch(note, old_chord, new_chord, role):
    if role in ("BASS", "SUB_BASS"):
        return g._near(new_chord["root"] % 12, note["pitch"],
                       24 if role == "SUB_BASS" else 36,
                       43 if role == "SUB_BASS" else 55)
    old_pcs, new_pcs = old_chord["pitch_classes"], new_chord["pitch_classes"]
    pc = note["pitch"] % 12
    degree = old_pcs.index(pc) if pc in old_pcs else min(
        range(len(old_pcs)), key=lambda i: min((pc-old_pcs[i]) % 12, (old_pcs[i]-pc) % 12))
    new_pc = new_pcs[degree % len(new_pcs)]
    low, high = ((64, 88) if role == "PLUCK" else (60, 84) if role == "ARPEGGIO" else (60, 83))
    return g._near(new_pc, note["pitch"], low, high)


def _clip_line(notes, progression):
    """Align evolved attacks to native timing; clip before the next attack/chord."""
    by_start = {}
    for note in notes:
        note = copy.deepcopy(note)
        note["start"] = round(note["start"] * GRID) / GRID
        if note["start"] >= SECTION_BEATS:
            continue
        note.pop("tie_boundaries", None)
        prior = by_start.get(note["start"])
        if prior is None or note["velocity"] > prior["velocity"]:
            by_start[note["start"]] = note
    ordered = sorted(by_start.values(), key=lambda n: n["start"])
    result = []
    for index, note in enumerate(ordered):
        onset = note["start"]
        chord_end = _bounds(_chord(progression, onset))[1]
        following = ordered[index+1]["start"] if index+1 < len(ordered) else SECTION_BEATS
        end = min(chord_end, following, onset + note["duration"])
        ticks = min(round(chord_end * GRID), round(following * GRID), round(end * GRID)) - round(onset * GRID)
        if ticks >= 1:
            note["duration"] = ticks / GRID
            result.append(note)
    return result


def _adapt_line(source, progression, identity, seed, amount, role):
    source_notes = _track(source, role)["notes"]
    rng = _rng(seed, identity, role)
    result = []
    for index, source_note in enumerate(source_notes):
        note = copy.deepcopy(source_note)
        onset = round(note["start"] * GRID) / GRID
        if onset >= SECTION_BEATS:
            continue
        # A contrasting phrase leaves patterned breathing spaces. The initial
        # and final attacks stay, and most of the source motif remains intact.
        if (identity == "C" and amount > 0 and 0 < index < len(source_notes)-1
                and role not in ("BASS", "SUB_BASS")
                and index % 4 == 2 and rng.random() < amount):
            continue
        old = _chord(source["progression"], source_note["start"])
        new = _chord(progression, onset)
        note["pitch"] = _mapped_pitch(source_note, old, new, role)
        note["start"] = onset
        note.pop("tie_boundaries", None)
        result.append(note)
    if role in ("BASS", "SUB_BASS"):
        # Every changed root gets an attack, even if the source had a rest at
        # that point. Original rhythmic attacks are retained between changes.
        for chord in progression:
            start, end = _bounds(chord)
            if not any(abs(n["start"]-start) < .5/GRID for n in result):
                nearest = min(source_notes, key=lambda n: abs(n["start"]-start))
                note = copy.deepcopy(nearest)
                note.update(start=start, duration=min(end-start, 3.75 if role == "SUB_BASS" else .45),
                            pitch=g._near(chord["root"] % 12, 33 if role == "SUB_BASS" else 45,
                                          24 if role == "SUB_BASS" else 36, 43 if role == "SUB_BASS" else 55))
                note.pop("tie_boundaries", None)
                result.append(note)
    elif identity in ("B", "D") and amount > 0:
        # Add short answers at phrase endings, leaving the original melody
        # attacks in place. D uses a smaller answer than B.
        positions = (15.5, 31.5) if identity == "B" else (31.5,)
        for beat in positions:
            if beat < 20 and amount < .45:
                continue
            if any(abs(n["start"]-beat) < .5/GRID for n in result):
                continue
            old_note = min(source_notes, key=lambda n: abs(n["start"]-beat))
            chord = _chord(progression, beat)
            target_pc = chord["root"] % 12 if beat >= 31 else rng.choice(chord["pitch_classes"])
            low, high = ((64, 88) if role == "PLUCK" else (60, 84) if role == "ARPEGGIO" else (60, 83))
            result.append({"start": beat, "duration": .2 if role != "LEAD" else .35,
                           "pitch": g._near(target_pc, old_note["pitch"], low, high),
                           "velocity": max(1, old_note["velocity"]-7)})
    return _clip_line(result, progression)


def _drums(source, identity, seed, amount):
    notes = copy.deepcopy(_track(source, "DRUMS")["notes"])
    rng = _rng(seed, identity, "DRUMS")
    if identity == "C":
        # Keep the central pulse and backbeat, open space in the higher layer.
        notes = [n for n in notes if not (56 <= n["pitch"] < 64 and rng.random() < .75*amount)]
    elif identity == "B":
        # Reuse the source groove, adding a short pickup into each new phrase.
        for beat in (7.75, 15.75, 23.75):
            if amount >= .35 and not any(48 <= n["pitch"] < 56 and abs(n["start"]-beat) < .5/GRID for n in notes):
                notes.append({"start": beat, "duration": .12, "pitch": 49 if source["config"]["drum_variants"] else 48,
                              "velocity": 54, "family": "closed_hat", "variant": 1 if source["config"]["drum_variants"] else 0})
    # Small section-end fills stay inside their own eight-bar block, so every
    # shuffled order remains self-contained. A itself is never touched.
    if identity in ("B", "D"):
        fill = (30.5, 31, 31.5, 31.75) if amount >= .65 else (31.5, 31.75)
        for index, beat in enumerate(fill):
            if not any(64 <= n["pitch"] <= 67 and abs(n["start"]-beat) < .5/GRID for n in notes):
                variant = index % 4 if source["config"]["drum_variants"] else 0
                notes.append({"start": beat, "duration": min(.2, 32-beat), "pitch": 64+variant,
                              "velocity": 68+index*4, "family": "tom", "variant": variant})
    # Normalize hat choke timing after adding high-layer pickups.
    hats = sorted((n for n in notes if 48 <= n["pitch"] < 56), key=lambda n: n["start"])
    for note, following in zip(hats, hats[1:]):
        if note["pitch"] >= 52:
            note["duration"] = min(note["duration"], following["start"]-note["start"])
    return g._clean_notes(notes)


def _variant(source, identity, seed, amount, keep_roles):
    if not amount:
        return copy.deepcopy(source)
    locked = bool(set(keep_roles) & PITCHED)
    progression = _variant_harmony(source, identity, seed, amount, locked)
    result = copy.deepcopy(source)
    result["progression"] = progression
    result["engine_version"] = "4.0-song-evolution"
    result["variant"] = {"id": identity, "evolution_seed": seed, "amount": amount}
    for track in result["tracks"]:
        role = track["role"]
        if role in keep_roles or role == "NOISE":
            continue
        if role == "DRUMS":
            notes = _drums(source, identity, seed, amount)
        elif role in ("PAD", "CHORDS"):
            # Reuse the source seed/rhythm settings while revoicing the target
            # timeline. This also restores chord support after long source ties.
            config = {**source["config"], "auto_chords": True}
            notes = g._clean_notes(g._melody(config, g.GENRES[config["genre"]], progression, role))
        else:
            notes = _adapt_line(source, progression, identity, seed, amount, role)
        gain = 1 + amount * (.10 if identity == "B" else -.16 if identity == "C" else .04)
        for note in notes:
            note["velocity"] = min(127, max(1, round(note["velocity"] * gain)))
        track["notes"] = notes
    g.validate_arrangement(result)
    return result


def _assemble(bank, order, evolution):
    source = bank["A"]["arrangement"]
    song = {key: copy.deepcopy(value) for key, value in source.items()
            if key not in ("tracks", "progression", "sections", "section_bank", "section_order", "evolution")}
    song.update(bars=32, engine_version="4.0-song-evolution", seed=evolution["seed"],
                section_bank=copy.deepcopy(bank), section_order=list(order),
                evolution=copy.deepcopy(evolution), sections=[], progression=[], tracks=[])
    song["kept_roles"] = list(evolution["keep_roles"])
    for role in g.ROLES:
        track = copy.deepcopy(_track(source, role))
        track["notes"] = []
        song["tracks"].append(track)
    for position, identity in enumerate(order):
        section = bank[identity]
        arrangement = section["arrangement"]
        offset = position * SECTION_BEATS
        song["sections"].append({"id": identity, "label": section["label"], "bar": position*8, "bars": 8})
        for original in arrangement["progression"]:
            chord = copy.deepcopy(original)
            chord["bar"] += position*8
            chord["start"] = round(chord["bar"]*4*g.PPQ)/g.PPQ
            chord["duration"] = round(chord["bars"]*4*g.PPQ)/g.PPQ
            song["progression"].append(chord)
        for track in song["tracks"]:
            for original in _track(arrangement, track["role"])["notes"]:
                note = copy.deepcopy(original)
                note["start"] += offset
                if "tie_boundaries" in note:
                    note["tie_boundaries"] = [boundary+offset for boundary in note["tie_boundaries"]]
                track["notes"].append(note)
    song["notes"] = "Four related eight-bar sections. A is the saved theme; B lifts, C contrasts, D returns. Section order moves saved material without rerolling notes. MIDI has no program changes."
    return song


def validate_song(song):
    """Validate persisted bank/order and their exact flattened musical result."""
    g.validate_arrangement(song)
    if song.get("bars") != 32:
        raise ValueError("This action requires a 32-bar evolved song.")
    bank, order, metadata = song.get("section_bank"), song.get("section_order"), song.get("evolution")
    if not isinstance(bank, dict) or set(bank) != set(SECTION_LABELS):
        raise ValueError("The saved song needs sections A, B, C and D.")
    if not isinstance(order, list) or len(order) != 4 or any(type(x) is not str for x in order) or sorted(order) != list("ABCD"):
        raise ValueError("Section order must contain A, B, C and D exactly once.")
    if not isinstance(metadata, dict):
        raise ValueError("The saved song is missing its evolution settings.")
    _seed(metadata.get("seed"))
    _amount(metadata.get("amount"))
    kept = metadata.get("keep_roles")
    if (not isinstance(kept, list) or any(type(role) is not str for role in kept)
            or len(kept) != len(set(kept)) or any(role not in g.ROLES for role in kept)):
        raise ValueError("The song has invalid kept roles.")
    source = bank["A"]["arrangement"]
    if not isinstance(source.get("config"), dict):
        raise ValueError("The saved theme needs valid generation settings.")
    g.validate_config(source["config"])
    if metadata.get("source_seed") != source.get("seed"):
        raise ValueError("The song source seed disagrees with its saved theme.")
    for identity, section in bank.items():
        label = section.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 40 or any(ord(c) < 32 for c in label):
            raise ValueError("Section labels must be short printable text.")
        arrangement = section["arrangement"]
        for key in ("bpm", "key", "mode", "genre", "config"):
            if arrangement.get(key) != source.get(key):
                raise ValueError("Every saved section must share the source's tempo and generation settings.")
        for role in g.ROLES:
            if ({key: value for key, value in _track(arrangement, role).items() if key != "notes"}
                    != {key: value for key, value in _track(source, role).items() if key != "notes"}):
                raise ValueError("Every saved section must share the source's instrument routing.")
        if kept:
            if set(kept) & PITCHED and arrangement["progression"] != source["progression"]:
                raise ValueError("Kept melodic parts require the source harmony in every section.")
            for role in kept:
                if _track(arrangement, role) != _track(source, role):
                    raise ValueError("A kept part changed in the section bank.")
    expected = _assemble(bank, order, metadata)
    for key in ("tracks", "progression", "sections", "bars", "beats_per_bar", "ppq", "bpm", "key", "mode", "genre", "config", "seed", "kept_roles"):
        if song.get(key) != expected[key]:
            raise ValueError(f"Saved song {key} disagrees with its section bank/order.")
    return song


def evolve_song(source, *, seed, amount=.5, keep_roles=()):
    """Develop a saved eight-bar theme into A/B/C/D; source is never mutated."""
    _seed(seed)
    _amount(amount)
    g.validate_arrangement(source)
    if source["bars"] != 8:
        raise ValueError("Choose an eight-bar source; use the saved A section to evolve a song again.")
    g.validate_config(source["config"])
    if not isinstance(keep_roles, (list, tuple, set)) or any(type(role) is not str for role in keep_roles):
        raise ValueError("Kept roles must be instrument role names.")
    kept = sorted(set(keep_roles))
    if any(role not in g.ROLES for role in kept):
        raise ValueError("Unknown kept instrument role.")
    bank = {"A": {"label": SECTION_LABELS["A"], "arrangement": copy.deepcopy(source)}}
    for identity in "BCD":
        bank[identity] = {"label": SECTION_LABELS[identity], "arrangement": _variant(source, identity, seed, amount, kept)}
    metadata = {"seed": seed, "amount": amount, "source_seed": source["seed"], "keep_roles": kept}
    song = _assemble(bank, list("ABCD"), metadata)
    return validate_song(song)


def reorder_song(song, order):
    """Move complete saved sections; do not reroll harmony, melodies or fills."""
    validate_song(song)
    if (not isinstance(order, (list, tuple)) or len(order) != 4
            or any(type(x) is not str for x in order) or sorted(order) != list("ABCD")):
        raise ValueError("Section order must contain A, B, C and D exactly once.")
    result = _assemble(song["section_bank"], list(order), song["evolution"])
    return validate_song(result)


def shuffle_song(song, seed):
    """Choose a deterministic new order, always different from the current one."""
    validate_song(song)
    _seed(seed)
    order = list(song["section_order"])
    rng = _rng(seed, "ORDER", "sections")
    rng.shuffle(order)
    if order == song["section_order"]:
        order = order[1:] + order[:1]
    return reorder_song(song, order)
