"""Export generated eight- or 32-bar parts into a copy of a compatible prepared FLP.

Nine placed patterns' note payloads and the tempo are replaced. Optional drum
kit integration sets 32 sample paths and disables Gate/Auto C/genre tuning in
the measured Drum Buddy state for raw one-shot playback. Routing, controller
links, mutes and other plugin parameters are preserved. For 32-bar songs,
playlist clips and the existing automation calibration ramps are stretched;
the adapter does not compose new musical automation.
This is a measured adapter for the supplied template, not a general FLP API.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import wave
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from arranger_support.flp_binary import (
    AutomationPoint, FlpError, automation_points, clone_playlist_record,
    inspect_project, parse_flp, replace_automation_points,
)


DEFAULT_TEMPLATE = Path(__file__).resolve().parent / "templates" / "automation_template.flp"
# role: (pattern ID, native instrument channel ID, mixer insert, channel name)
ROLE_MAP = {
    "DRUMS": (4, 1, 1, "Drums"),
    "PAD": (5, 2, 9, "Pad"),
    "PLUCK": (6, 3, 10, "Pluck"),
    "CHORDS": (7, 4, 11, "Chords"),
    "ARPEGGIO": (8, 5, 12, "Arpeggio"),
    "LEAD": (9, 6, 13, "Lead"),
    "BASS": (10, 7, 14, "Bass"),
    "SUB_BASS": (11, 8, 15, "Sub-bass"),
    "NOISE": (19, 9, 16, "Noise"),
}
AUTOMATION_NAMES = (
    "AUTO_LP_BASS", "AUTO_VOLUME_BASS", "AUTO_HOLD_BASS_COMP_KNEE",
    "AUTO_DELAY_WET_LEAD", "AUTO_REVERB_WET_LEAD", "AUTO_WIDTH_PAD",
    "AUTO_PAN_PAD", "AUTO_HOLD_ARPEGGIO_CROSSOVER", "AUTO_DELAY_WET_PLUCK",
    "AUTO_SUSTAIN_PLUCK", "AUTO_LP_PAD", "AUTO_LP_PLUCK", "AUTO_LP_CHORDS",
    "AUTO_LP_ARPEGGIO", "AUTO_LP_LEAD", "AUTO_LP_NOISE", "AUTO_VOLUME_PAD",
    "AUTO_VOLUME_PLUCK", "AUTO_VOLUME_CHORDS", "AUTO_VOLUME_ARPEGGIO",
    "AUTO_VOLUME_LEAD", "AUTO_VOLUME_SUB_BASS", "AUTO_NOISE_VOLUME_NOISE",
    "AUTO_DELAY_WET_PAD", "AUTO_REVERB_WET_PAD", "AUTO_REVERB_WET_PLUCK",
    "AUTO_DELAY_WET_CHORDS", "AUTO_REVERB_WET_CHORDS", "AUTO_DELAY_WET_ARPEGGIO",
    "AUTO_REVERB_WET_ARPEGGIO", "AUTO_SUSTAIN_ARPEGGIO", "AUTO_HOLD_COMP_KNEE_PAD",
    "AUTO_HOLD_COMP_KNEE_PLUCK", "AUTO_HOLD_COMP_KNEE_CHORDS",
    "AUTO_HOLD_COMP_KNEE_ARPEGGIO", "AUTO_HOLD_COMP_KNEE_LEAD",
    "AUTO_HOLD_COMP_KNEE_SUB_BASS",
)
DRUM_BASES = (36, 40, 44, 48, 52, 56, 60, 64)
TEMPO_TARGET = 0x40000005


def _sync_tempo_initializers(project, bpm):
    """Match measured tempo initializers to event 156, never other globals."""
    raw_tempo = round(bpm * 1000)
    changes = []
    changed_events = set()
    for index, event in enumerate(project.events):
        if event.id not in (216, 225):
            continue
        if len(event.data) % 12:
            raise FlpError("Unsupported initialized-control record layout.")
        data = bytearray(event.data)
        for offset in range(0, len(data), 12):
            _, target, value = struct.unpack_from("<III", data, offset)
            if target != TEMPO_TARGET:
                continue
            if not 10000 <= value <= 522000:
                raise FlpError("Unsupported saved tempo initializer value.")
            struct.pack_into("<I", data, offset + 8, raw_tempo)
            changes.append({"event_id": event.id, "event_index": index,
                            "record_offset": offset, "target": "0x40000005",
                            "before_bpm": value / 1000, "after_bpm": raw_tempo / 1000})
            changed_events.add(index)
        event.data = bytes(data)
    return changed_events, changes


def _chunks(data, start=4):
    """Decode the measured wrapper's uint32-ID/uint64-length chunks."""
    result = []
    at = start
    while at < len(data):
        if at + 12 > len(data):
            raise FlpError("Truncated Drum Buddy wrapper chunk header.")
        identity, length = struct.unpack_from("<IQ", data, at)
        end = at + 12 + length
        if end > len(data):
            raise FlpError("Truncated Drum Buddy wrapper chunk.")
        result.append((identity, data[at + 12:end]))
        at = end
    if len({identity for identity, _ in result}) != len(result):
        raise FlpError("Duplicate Drum Buddy wrapper chunks.")
    return result


def _join_chunks(prefix, chunks):
    return prefix + b"".join(struct.pack("<IQ", identity, len(payload)) + payload
                             for identity, payload in chunks)


def _decode_drum_state(data):
    """Identify our measured Drum Buddy v2 state and its 32 sample slots."""
    if len(data) < 4 or data[:4] != struct.pack("<I", 12):
        raise FlpError("Unsupported Fruity Wrapper state for Drum Buddy.")
    outer = _chunks(data)
    by_id = dict(outer)
    if (by_id.get(54) != b"Drum Buddy" or by_id.get(56) != b"ClearShape Audio"
            or by_id.get(52) != bytes.fromhex("01efcdab8291ebfa4373686144726264")):
        raise FlpError("The drum instrument must be the measured ClearShape Audio Drum Buddy.")
    vst = by_id.get(53, b"")
    if vst[:4] != struct.pack("<I", 1):
        raise FlpError("Unsupported Drum Buddy VST3 state container.")
    inner = _chunks(vst)
    if [identity for identity, _ in inner] != [1, 3, 4] or len(inner[0][1]) != 64:
        raise FlpError("The captured Drum Buddy VST3 state layout changed.")
    # Chunk 4 is the wrapper's parameter-ID table, not parameter values.
    # Match its declared IDs against the installed plugin's source order.
    table = dict(inner)[4]
    if len(table) < 4 or len(table) != 4 + 4 * struct.unpack_from("<I", table)[0]:
        raise FlpError("Unsupported Drum Buddy parameter-ID table.")
    suffixes = ("volume", "pan", "attack", "decay", "sustain", "release",
                "tune", "fine", "output", "autoTune", "gated", "mute")
    names = [f"s{i}_{suffix}" for i in range(1, 33) for suffix in suffixes]
    names += ["kitTuning", "genre", "key", "scale"]
    names += [f"s{i}_type" for i in range(1, 33)]
    expected_ids = []
    for name in names:
        value = 0
        for char in name:
            value = (31 * value + ord(char)) & 0xffffffff
        expected_ids.append(value & 0x7fffffff)
    if len(table) < 4 + 4 * len(expected_ids) or list(struct.unpack_from("<420I", table, 4)) != expected_ids:
        raise FlpError("Drum Buddy's measured parameter identifiers changed.")
    component = dict(inner)[3]
    if component[:4] != b"VC2!" or len(component) < 8:
        raise FlpError("Expected the measured JUCE XML state marker.")
    length = struct.unpack_from("<I", component, 4)[0]
    if not 0 < length <= len(component) - 8:
        raise FlpError("Invalid Drum Buddy XML length.")
    xml_data = component[8:8 + length]
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise FlpError("Malformed Drum Buddy state XML.") from exc
    if root.tag != "DRUM_BUDDY" or root.get("version") != "2":
        raise FlpError("Expected Drum Buddy version-2 saved state.")
    samples = root.find("Samples")
    if samples is None or len(samples) != 32 or any(s.tag != "Slot" for s in samples):
        raise FlpError("Drum Buddy state must contain its 32 sample slots.")
    if {s.get("index") for s in samples} != {str(i) for i in range(32)}:
        raise FlpError("Drum Buddy sample slots must be indexed 0 through 31.")
    return {"outer": outer, "inner": inner, "component": component,
            "xml": xml_data, "root": root, "xml_length": length}


def _drum_event(project, summary):
    channel = next(c for c in summary["channels"] if c["id"] == 1)
    start = channel["event_start_index"]
    end = next((i for i in range(start + 1, len(project.events))
                if project.events[i].id in (64, 99)), len(project.events))
    matches = [i for i in range(start + 1, end) if project.events[i].id == 213]
    if len(matches) != 1:
        raise FlpError("Expected one Drum Buddy plugin-state event.")
    index = matches[0]
    return index, _decode_drum_state(project.events[index].data)


def _kit_paths(kit_dir, reference_dir):
    actual = Path(kit_dir).resolve()
    references = Path(reference_dir).resolve() if reference_dir is not None else actual
    try:
        manifest = json.loads((actual / "kit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Read kit.json from the generated drum kit folder.") from exc
    samples = manifest.get("samples")
    if manifest.get("schema_version") != 1 or not isinstance(samples, list) or len(samples) != 32:
        raise ValueError("The kit manifest must describe 32 samples using schema version 1.")
    sample_rate = _integer(manifest.get("sample_rate"), "Kit sample rate", 8000, 192000)
    paths = {}
    file_names = set()
    for entry in samples:
        if not isinstance(entry, dict):
            raise ValueError("Each kit sample must be a manifest object.")
        note = _integer(entry.get("midi_note"), "Kit MIDI note", 36, 67)
        filename = entry.get("filename")
        if (not isinstance(filename, str) or not filename
                or Path(filename).name != filename or "/" in filename or "\\" in filename
                or Path(filename).suffix.lower() != ".wav"):
            raise ValueError("Kit sample filenames must be WAV basenames inside the kit folder.")
        if note in paths or filename.casefold() in file_names:
            raise ValueError("Kit sample notes and filenames must each be unique.")
        source = (actual / filename).resolve()
        if source.parent != actual or not source.is_file():
            raise ValueError(f"Missing kit sound: {filename}")
        try:
            with wave.open(str(source), "rb") as handle:
                if (handle.getcomptype() != "NONE" or handle.getnchannels() != 1
                        or handle.getsampwidth() != 2 or handle.getframerate() != sample_rate
                        or handle.getnframes() <= 0):
                    raise ValueError(f"Expected a nonempty mono 16-bit {sample_rate}-Hz PCM kit sound: {filename}")
        except (wave.Error, EOFError) as exc:
            raise ValueError(f"Invalid kit WAV: {filename}") from exc
        paths[note] = str(references / filename)
        file_names.add(filename.casefold())
    if set(paths) != set(range(36, 68)):
        raise ValueError("Kit note mapping must cover every note from 36 through 67.")
    return paths


def _replace_kit(data, decoded, paths):
    """Set paths and measured raw-playback switches; preserve other controls."""
    xml_data = decoded["xml"]
    block = re.search(rb"<Samples>(.*?)</Samples>", xml_data, re.DOTALL)
    if block is None:
        raise FlpError("The captured Drum Buddy sample XML layout changed.")
    seen = set()
    def replacement(match):
        slot = int(match.group(1))
        if slot in seen or slot not in range(32):
            raise FlpError("Unexpected or duplicate Drum Buddy sample slot.")
        seen.add(slot)
        path = escape(paths[slot + 36], {'"': '&quot;'}).encode("utf-8")
        return b'<Slot index="' + match.group(1) + b'" path="' + path + b'"/>'
    sample_xml = re.sub(rb'<Slot index="([0-9]+)" path="[^"]*"/>', replacement, block.group(1))
    if seen != set(range(32)):
        raise FlpError("Not all 32 Drum Buddy sample paths could be replaced.")
    updated_xml = xml_data[:block.start(1)] + sample_xml + xml_data[block.end(1):]
    raw_controls = {"kitTuning"} | {f"s{i}_{control}" for i in range(1, 33)
                                  for control in ("gated", "autoTune")}
    changed_controls = []
    control_counts = Counter()
    def raw_control(match):
        identity = match.group(1).decode("utf-8")
        if identity not in raw_controls:
            return match.group(0)
        control_counts[identity] += 1
        changed_controls.append({"parameter": identity,
                                 "before": float(match.group(2)), "after": 0.0})
        return b'<PARAM id="' + match.group(1) + b'" value="0.0"/>'
    updated_xml = re.sub(rb'<PARAM id="([^"]+)" value="([^"]+)"/>', raw_control, updated_xml)
    if control_counts != Counter(raw_controls):
        raise FlpError("The 65 measured Drum Buddy raw-playback controls changed or are missing.")
    component = (b"VC2!" + struct.pack("<I", len(updated_xml)) + updated_xml
                 + decoded["component"][8 + decoded["xml_length"]:])
    inner = [(identity, component if identity == 3 else payload)
             for identity, payload in decoded["inner"]]
    vst = _join_chunks(struct.pack("<I", 1), inner)
    outer = [(identity, vst if identity == 53 else payload)
             for identity, payload in decoded["outer"]]
    result = _join_chunks(data[:4], outer)
    checked = _decode_drum_state(result)
    # Reparse the actual bytes to verify nested length fields and slot paths.
    actual_paths = {int(s.get("index")) + 36: s.get("path")
                    for s in checked["root"].find("Samples")}
    if actual_paths != paths:
        raise RuntimeError("Drum Buddy kit paths failed serialization validation.")
    old_other = [ET.tostring(child) for child in decoded["root"]
                 if child.tag != "Samples" and child.get("id") not in raw_controls]
    new_other = [ET.tostring(child) for child in checked["root"]
                 if child.tag != "Samples" and child.get("id") not in raw_controls]
    if old_other != new_other:
        raise RuntimeError("A Drum Buddy control changed during sample path update.")
    return result, changed_controls


def _role(value):
    if not isinstance(value, str):
        raise ValueError("Every track needs a role name.")
    normalized = re.sub(r"[\s-]+", "_", value.strip()).upper()
    return {"ARP": "ARPEGGIO", "SUBBASS": "SUB_BASS"}.get(normalized, normalized)


def _number(value, label, low, high):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f"{label} must be a finite number from {low} to {high}.")
    return float(value)


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}.")
    return value


def _validate_template(project, bars=8):
    """Refuse changed mapping, incomplete clips, or unprepared automation."""
    if type(bars) is not int or bars not in (8, 32):
        raise ValueError("Template validation supports only 8 or 32 bars.")
    total_beats = bars * 4
    total_ticks = total_beats * project.ppq
    summary = inspect_project(project)
    for event in project.events:
        if event.id == 223:
            if len(event.data) % 12:
                raise FlpError("Unsupported pattern automation record layout.")
            if any(target == TEMPO_TARGET for _, target, _ in struct.iter_unpack("<III", event.data)):
                raise FlpError("Remove recorded tempo automation before generating a fixed-tempo source.")
        elif (event.id == 227 and len(event.data) == 20
              and struct.unpack_from("<I", event.data, 8)[0] == TEMPO_TARGET):
            raise FlpError("Remove linked tempo automation before generating a fixed-tempo source.")
    if project.ppq != 96 or summary["time_signature"] != [4, 4]:
        raise FlpError("Use the supplied 96-PPQ, 4/4 automation template.")
    if len(summary["playlist"]) != 1:
        raise FlpError("The prepared template needs exactly one Playlist arrangement.")
    patterns = {p["id"]: p for p in summary["patterns"]}
    channels = {c["id"]: c for c in summary["channels"]}
    if len(channels) != len(summary["channels"]):
        raise FlpError("Duplicate instrument channels in template.")
    records = summary["playlist"][0]["records"]
    musical = [r for r in records if r["pattern_id"] is not None]
    expected_ids = {spec[0] for spec in ROLE_MAP.values()}
    if Counter(r["pattern_id"] for r in musical) != Counter(expected_ids):
        raise FlpError("The template must contain the nine original placed instrument parts.")
    for role, (pattern_id, channel_id, mixer, channel_name) in ROLE_MAP.items():
        channel = channels.get(channel_id)
        pattern = patterns.get(pattern_id)
        if (channel is None or channel["type"] == 5
                or channel["name"] != channel_name or channel.get("route") != mixer):
            raise FlpError(f"The prepared {role} instrument or mixer route changed.")
        if (pattern is None or pattern["channels"] != [channel_id]
                or pattern["notes_event_index"] is None or not pattern["note_count"]):
            raise FlpError(f"The prepared {role} pattern no longer contains its own instrument.")
        if _role(pattern["name"]) != role:
            raise FlpError(f"The prepared {role} pattern name changed.")
        clip = next(r for r in musical if r["pattern_id"] == pattern_id)
        if (clip["position"] != 0 or clip["length"] != total_ticks
                or bytes.fromhex(clip["raw_hex"])[24:32] != b"\xff" * 8):
            raise FlpError(f"{role} must be placed once, untrimmed, for {bars} bars from bar 1.")
        if not total_ticks - 4 * project.ppq < pattern["length_ticks"] <= total_ticks:
            raise FlpError(f"{role} must have native Auto length of {bars} bars.")
    actual_controls = {a["channel_id"]: a for a in summary["automation"]}
    if set(actual_controls) != set(range(10, 47)):
        raise FlpError("The template must retain its 37 prepared automation controls.")
    auto_records = [r for r in records if r["channel_id"] is not None]
    if Counter(r["channel_id"] for r in auto_records) != Counter(actual_controls.keys()):
        raise FlpError("Every prepared automation must be placed exactly once.")
    for channel_id, expected_name in enumerate(AUTOMATION_NAMES, 10):
        auto = actual_controls[channel_id]
        points = auto["points"]
        if auto["name"] != expected_name or len(points) != 2:
            raise FlpError("Prepared automation names or reference points changed.")
        if (points[0]["position_beats"] != 0 or points[1]["position_beats"] != total_beats
                or points[0]["value"] == points[1]["value"]
                or any(not math.isfinite(p["value"]) or not 0 <= p["value"] <= 1 for p in points)):
            raise FlpError("Prepared automation needs its two valid calibration points.")
        clip = next(r for r in auto_records if r["channel_id"] == channel_id)
        offsets = struct.unpack_from("<ff", bytes.fromhex(clip["raw_hex"]), 24)
        if (clip["position"] != 0 or clip["length"] != total_ticks
                or offsets not in ((-1.0, -1.0), (0.0, float(total_beats)))):
            raise FlpError(f"Automation clips must cover {bars} bars from bar 1, untrimmed.")
        links = [e for e in project.events if e.id == 227 and len(e.data) == 20
                 and struct.unpack_from("<H", e.data, 2)[0] == channel_id]
        if len(links) != 1:
            raise FlpError("Each automation needs one native controller link.")
    return summary


def _track_notes(track, role, ppq, bars=8):
    total_beats = bars * 4
    notes = track.get("notes")
    if not isinstance(notes, list) or not notes:
        raise ValueError(f"{role} needs nonempty generated note data.")
    converted = []
    folded = 0
    for number, note in enumerate(notes, 1):
        if not isinstance(note, dict):
            raise ValueError(f"{role} note {number} must be a dictionary.")
        label = f"{role} note {number}"
        start = _number(note.get("start"), label + " start", 0, total_beats)
        duration = _number(note.get("duration"), label + " duration", 0, total_beats)
        pitch = _integer(note.get("pitch"), label + " pitch", 0, 127)
        velocity = _integer(note.get("velocity"), label + " velocity", 1, 127)
        if duration <= 0 or start + duration > total_beats + 1e-8:
            raise ValueError(f"{label} must end inside the {bars}-bar arrangement.")
        begin_tick = round(start * ppq)
        end_tick = min(total_beats * ppq, round((start + duration) * ppq))
        if end_tick <= begin_tick:
            raise ValueError(f"{label} is too short at the template's 96-PPQ resolution.")
        if role == "DRUMS":
            if not 36 <= pitch <= 67:
                raise ValueError("Drums must use the custom 32-note map, MIDI notes 36 through 67.")
        converted.append((begin_tick, end_tick - begin_tick, pitch, velocity))
    # Keep one trigger for duplicate events on the same slot and tick.
    coalesced = 0
    if role == "DRUMS":
        by_trigger = {}
        for start, duration, pitch, velocity in converted:
            key = (start, pitch)
            if key in by_trigger:
                old = by_trigger[key]
                duration, velocity = max(duration, old[1]), max(velocity, old[3])
                coalesced += 1
            by_trigger[key] = (start, duration, pitch, velocity)
        converted = list(by_trigger.values())
    if max(n[0] + n[1] for n in converted) <= (total_beats - 4) * ppq:
        raise ValueError(f"{role} must reach into bar {bars} to preserve native {bars}-bar Auto length.")
    return sorted(converted), folded, coalesced


def _extend_song_timeline(project, summary, bars):
    """Stretch measured eight-bar placements and calibration timing only.

    Values, curves, native links and opaque record fields stay unchanged.
    This is intentionally not the older arranger's section automation engine.
    """
    if bars == 8:
        return set()
    total_beats = bars * 4
    changed = set()
    for auto in summary["automation"]:
        index = auto["event_index"]
        event = project.events[index]
        points = automation_points(event)
        replace_automation_points(event, [
            AutomationPoint(point.position_beats * bars / 8, point.value,
                            point.tension, point.flags)
            for point in points
        ])
        changed.add(index)
    playlist = summary["playlist"][0]
    project.events[playlist["event_index"]].data = b"".join(
        clone_playlist_record(bytes.fromhex(record["raw_hex"]), position=0,
                              length=total_beats * project.ppq,
                              source_end_beats=(total_beats if record["channel_id"] is not None else None))
        for record in playlist["records"]
    )
    changed.add(playlist["event_index"])
    return changed


def export_template(arrangement, output_path, template_path=None, *,
                    kit_dir=None, kit_reference_dir=None):
    """Write a NEW eight- or 32-bar FLP and return an integration report.

    arrangement: {bpm, bars: 8 or 32, tracks: [{role, channel, notes: [
        {start: beats, duration: beats, pitch: MIDI number, velocity: 1..127}
    ]}]}. Input MIDI channels are intentionally replaced with the existing
    native instrument channel IDs. Input PPQ does not affect beat-based times.

    The measured Drum Buddy instrument supports all 32 MIDI notes 36..67.
    With kit_dir, kit.json and each actual WAV are validated and sample paths
    are stored from kit_reference_dir (or kit_dir when not supplied). This
    allows a staging folder to be moved into its final published location.
    Gate and automatic tuning are disabled for the newly supplied raw kit;
    other parameters and all mixer routing remain unchanged. Without kit_dir,
    the instrument state and existing sounds remain byte-identical.
    """
    if not isinstance(arrangement, dict):
        raise ValueError("Arrangement must be a dictionary.")
    if type(arrangement.get("bars")) is not int or arrangement["bars"] not in (8, 32):
        raise ValueError("This template export requires exactly 8 or 32 bars.")
    bars = arrangement["bars"]
    bpm = _number(arrangement.get("bpm"), "BPM", 10, 522)
    supplied = arrangement.get("tracks")
    if not isinstance(supplied, list):
        raise ValueError("Arrangement tracks must be a list.")
    tracks = {}
    for track in supplied:
        if not isinstance(track, dict):
            raise ValueError("Each track must be a dictionary.")
        role = _role(track.get("role"))
        if role in tracks:
            raise ValueError(f"Duplicate {role} track.")
        if role not in ROLE_MAP:
            raise ValueError(f"Unsupported template role: {role}.")
        if track.get("muted", False):
            raise ValueError("FLP export preserves the template's channel mutes. Unmute all generated parts, or export MIDI only.")
        tracks[role] = track
    missing = set(ROLE_MAP) - tracks.keys()
    if missing:
        raise ValueError("Missing template roles: " + ", ".join(sorted(missing)))
    source = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE
    if not source.is_file():
        raise FileNotFoundError("A compatible prepared FLP template is required. The public release does not include one.")
    source = source.resolve()
    destination = Path(output_path).resolve()
    if source == destination:
        raise ValueError("Choose a new FLP filename; the source template is never overwritten.")
    if destination.suffix.lower() != ".flp":
        raise ValueError("The output filename must end in .flp.")
    if destination.exists():
        raise FileExistsError("That output already exists. Choose a new filename.")
    original_data = source.read_bytes()
    project = parse_flp(original_data, source)
    summary = _validate_template(project)
    drum_index, drum_state = _drum_event(project, summary)
    if kit_reference_dir is not None and kit_dir is None:
        raise ValueError("kit_reference_dir requires kit_dir to validate the actual WAV files.")
    kit_paths = _kit_paths(kit_dir, kit_reference_dir) if kit_dir is not None else None
    original_events = [(e.id, e.data) for e in project.events]
    patterns = {p["id"]: p for p in summary["patterns"]}
    changed_indexes = set()
    drum_parameter_changes = []
    if kit_paths is not None:
        project.events[drum_index].data, drum_parameter_changes = _replace_kit(
            project.events[drum_index].data, drum_state, kit_paths)
        changed_indexes.add(drum_index)
    track_report = []
    for role, (pattern_id, channel_id, mixer, _) in ROLE_MAP.items():
        notes, folded, coalesced = _track_notes(tracks[role], role, project.ppq, bars)
        index = patterns[pattern_id]["notes_event_index"]
        old_payload = project.events[index].data
        seed = next((old_payload[i:i+24] for i in range(0, len(old_payload), 24)
                     if not struct.unpack_from("<H", old_payload, i+4)[0] & 8), None)
        if seed is None:
            raise FlpError(f"{role} has no ordinary native note to use as a seed.")
        encoded = []
        for start, duration, pitch, velocity in notes:
            note = bytearray(seed)
            struct.pack_into("<I", note, 0, start)
            struct.pack_into("<H", note, 6, channel_id)
            struct.pack_into("<I", note, 8, duration)
            struct.pack_into("<HH", note, 12, pitch, 0)
            note[16] = 120
            note[18:24] = bytes((64, 0, 64, velocity, 128, 128))
            encoded.append(bytes(note))
        project.events[index].data = b"".join(encoded)
        changed_indexes.add(index)
        track_report.append({"role": role, "pattern_id": pattern_id,
                             "native_channel": channel_id, "mixer_insert": mixer,
                             "notes": len(notes), "drum_variants_folded": folded,
                             "coincident_drum_hits_coalesced": coalesced})
    tempos = [(i, e) for i, e in enumerate(project.events) if e.id == 156]
    if len(tempos) != 1:
        raise FlpError("Expected exactly one native tempo event.")
    tempo_index, tempo_event = tempos[0]
    tempo_event.data = struct.pack("<I", round(bpm * 1000))
    changed_indexes.add(tempo_index)
    initialized_indexes, tempo_initializer_updates = _sync_tempo_initializers(project, bpm)
    changed_indexes.update(initialized_indexes)
    changed_indexes.update(_extend_song_timeline(project, summary, bars))
    output_bytes = project.to_bytes()
    validated = parse_flp(output_bytes)
    _validate_template(validated, bars=bars)
    _drum_event(validated, inspect_project(validated))
    if len(validated.events) != len(original_events):
        raise RuntimeError("Unexpected FLP event count change.")
    for index, (before, event) in enumerate(zip(original_events, validated.events)):
        if index not in changed_indexes and before != (event.id, event.data):
            raise RuntimeError("A non-musical FLP event changed; export cancelled.")
        if before[0] != event.id:
            raise RuntimeError("Unexpected FLP event type change.")
    if source.read_bytes() != original_data:
        raise RuntimeError("Source template changed during export; please try again.")
    # Exclusive creation prevents accidental replacement, including concurrent
    # exports choosing the same target. No file is created before validation.
    with destination.open("xb") as handle:
        handle.write(output_bytes)
    return {
        "output": str(destination), "template": str(source), "bars": bars,
        "bpm": validated.tempo, "ppq": validated.ppq, "tracks": track_report,
        "tempo_initializer_updates": tempo_initializer_updates,
        "source_sha256": hashlib.sha256(original_data).hexdigest(),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
        "preserved_other_events": True, "preserved_automation_controls": 37,
        "automation_timing": "unchanged" if bars == 8 else "calibration_ramps_stretched_to_32_bars",
        "composed_section_automation": False,
        "arranger_source_compatible": bars == 8,
        "preserved_non_drum_plugin_states": True,
        "existing_drum_sounds_preserved": kit_paths is None,
        "drum_kit_referenced": kit_paths is not None,
        "drum_sample_paths": kit_paths,
        "drum_parameter_changes": drum_parameter_changes,
        "drum_mapping": "Drum Buddy slots 1..32 on MIDI notes 36..67",
        "controller_table": "Verified identifier table, not duplicated parameter values",
        "auditioned_in_fl_studio": False,
        "recommended_arranger_drum_mode": "original" if bars == 8 else None,
        "warnings": [
            ("The new raw drum kit is referenced by absolute paths. Keep its kit folder at the published location. Drum Buddy Gate, Auto C, and genre/key tuning are disabled for raw one-shot playback."
             if kit_paths is not None else "This FLP retains the template's existing drum samples and playback controls."),
            ("In FLP Arranger choose Keep original drums to retain the generated rhythms; Gentle intentionally removes some hits."
             if bars == 8 else "The 37 original two-point automation calibration ramps are stretched across 32 bars with unchanged endpoint values. No section-specific automation was composed."),
            ("This is an eight-bar source template. Open it in the existing FLP Arranger to create full-song sections and automation."
             if bars == 8 else "This is a 32-bar song for opening directly in FL Studio. The existing FLP Arranger expects eight-bar sources; use an eight-bar export with that workflow."),
            "Required plugins and original sample files must remain installed. Output has been structurally validated, not auditioned in FL Studio.",
        ],
    }
