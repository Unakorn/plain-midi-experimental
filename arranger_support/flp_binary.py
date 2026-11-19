"""Minimal, byte-preserving FL Studio 26.1.7 project editing.

Only the measured 26.1.7.5653 layout is enabled. All opaque events and plugin
state stay byte-identical. This is an unofficial format adapter, not an FL API.
Format references: PyFLP's event documentation and clarkipeng/flpkit. No code
from those libraries is included. Changes still need verification in FL Studio.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import struct

SUPPORTED_VERSION = "26.1.7.5653"
PLAYLIST_STRIDE = 88


class FlpError(ValueError):
    """Unsupported or malformed FLP data; refuse to guess."""


def _varint(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    for shift in range(0, 35, 7):
        if offset >= len(data):
            raise FlpError("Truncated event length")
        byte = data[offset]
        offset += 1
        result |= (byte & 127) << shift
        if byte < 128:
            return result, offset
    raise FlpError("Invalid event length")


def _fixed_size(event_id: int) -> int | None:
    # Measured exception in this exact FL2026 build. The following bytes are
    # separate events; treating 172 as a DWORD desynchronizes the whole file.
    if event_id == 172:
        return 1
    return 1 if event_id < 64 else 2 if event_id < 128 else 4 if event_id < 192 else None


@dataclass
class Event:
    id: int
    data: bytes
    start: int = -1
    payload_start: int = -1
    _raw: bytes = field(default=b"", repr=False)
    _original_data: bytes = field(default=b"", repr=False)

    def encode(self) -> bytes:
        if not 0 <= self.id <= 255:
            raise FlpError("Event id must fit in one byte")
        if self._raw and self.data == self._original_data and self.id == self._raw[0]:
            return self._raw
        size = _fixed_size(self.id)
        if size is not None and len(self.data) != size:
            raise FlpError(f"Event {self.id} needs {size} bytes")
        return bytes([self.id]) + (_varint(len(self.data)) if size is None else b"") + self.data


@dataclass
class FlpFile:
    events: list[Event]
    ppq: int
    version: str
    _prefix: bytes = field(repr=False)
    source: Path | None = None

    @property
    def tempo(self) -> float:
        matches = [event for event in self.events if event.id == 156]
        if len(matches) != 1:
            raise FlpError("Expected exactly one modern tempo event")
        return struct.unpack("<I", matches[0].data)[0] / 1000

    def to_bytes(self) -> bytes:
        payload = b"".join(event.encode() for event in self.events)
        prefix = bytearray(self._prefix)
        struct.pack_into("<I", prefix, len(prefix) - 4, len(payload))
        return bytes(prefix) + payload

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        if self.source is not None and destination.resolve() == self.source.resolve():
            raise FlpError("Refusing to overwrite the source template")
        # Exclusive creation also protects any previously generated project.
        with destination.open("xb") as handle:
            handle.write(self.to_bytes())


def parse_flp(data: bytes, source: str | Path | None = None) -> FlpFile:
    if len(data) < 24 or data[:4] != b"FLhd" or struct.unpack_from("<I", data, 4)[0] != 6:
        raise FlpError("Expected an FL Studio FLhd project header")
    if data[14:18] != b"FLdt" or struct.unpack_from("<I", data, 18)[0] != len(data) - 22:
        raise FlpError("Unsupported chunk layout or inconsistent FLdt size")
    if struct.unpack_from("<H", data, 8)[0] != 0:
        raise FlpError("This is not a song project")
    # The version precedes the unusual event-172 encoding, so establish it
    # before choosing that override.
    if data[22] != 199:
        raise FlpError("Expected a version event at the start of FLdt")
    version_length, version_at = _read_varint(data, 23)
    try:
        version = data[version_at:version_at + version_length].rstrip(b"\0").decode("ascii")
    except UnicodeError as exc:
        raise FlpError("Invalid version string") from exc
    if version != SUPPORTED_VERSION:
        raise FlpError(f"This adapter is validated for FL {SUPPORTED_VERSION}; file is {version}")
    events: list[Event] = []
    offset = 22
    while offset < len(data):
        start = offset
        event_id = data[offset]
        offset += 1
        size = _fixed_size(event_id)
        if size is None:
            size, offset = _read_varint(data, offset)
        end = offset + size
        if end > len(data):
            raise FlpError(f"Truncated event {event_id} at byte {start}")
        payload = data[offset:end]
        events.append(Event(event_id, payload, start, offset, data[start:end], payload))
        offset = end
    ppq = struct.unpack_from("<H", data, 12)[0]
    if not ppq:
        raise FlpError("Invalid PPQ")
    return FlpFile(events, ppq, version, data[:22], Path(source) if source is not None else None)


def load_flp(path: str | Path) -> FlpFile:
    return parse_flp(Path(path).read_bytes(), path)


def playlist_records(event: Event) -> list[bytes]:
    if event.id != 233 or len(event.data) % PLAYLIST_STRIDE:
        raise FlpError("Expected FL2026 88-byte Playlist records")
    records = [event.data[i:i + PLAYLIST_STRIDE] for i in range(0, len(event.data), PLAYLIST_STRIDE)]
    for record in records:
        if struct.unpack_from("<H", record, 4)[0] != 20480:
            raise FlpError("Unsupported Playlist pattern base")
        if not 0 <= struct.unpack_from("<H", record, 12)[0] <= 499:
            raise FlpError("Unsupported Playlist track index")
    return records


def clone_playlist_record(seed: bytes, position: int, length: int | None = None,
                          item_index: int | None = None, track: int | None = None,
                          source_end_beats: float | None = None) -> bytes:
    """Clone one native record. Track is 1-based. Opaque tail is preserved.

    Pattern offset sentinels are kept. For a full-song automation clip, set
    source_end_beats to the new curve end as well as extending its tick length.
    """
    if len(seed) != PLAYLIST_STRIDE:
        raise FlpError("A native 88-byte seed record is required")
    result = bytearray(seed)
    for value, label in [(position, "position"), (length, "length")]:
        if value is not None and (not isinstance(value, int) or not 0 <= value < 2**32):
            raise FlpError(f"Invalid Playlist {label}")
    struct.pack_into("<I", result, 0, position)
    if length is not None:
        if length <= 0:
            raise FlpError("Playlist length must be positive")
        struct.pack_into("<I", result, 8, length)
    if item_index is not None:
        if not 0 <= item_index < 65536:
            raise FlpError("Invalid Playlist item index")
        struct.pack_into("<H", result, 6, item_index)
    if track is not None:
        if not 1 <= track <= 500:
            raise FlpError("Playlist track must be between 1 and 500")
        struct.pack_into("<H", result, 12, 500 - track)
    if source_end_beats is not None:
        if not math.isfinite(source_end_beats) or source_end_beats < 0:
            raise FlpError("Invalid source window end")
        # Native full automation clips can store -1/-1 rather than 0/end.
        # Once an explicit end is supplied, use an explicit start as well.
        if struct.unpack_from('<ff', result, 24) == (-1.0, -1.0):
            struct.pack_into('<f', result, 24, 0.0)
        struct.pack_into("<f", result, 28, source_end_beats)
    return bytes(result)


@dataclass(frozen=True)
class AutomationPoint:
    position_beats: float
    value: float
    tension: float = 0.0
    flags: bytes = b"\0\0\0\0"


def automation_points(event: Event) -> list[AutomationPoint]:
    if event.id != 234 or len(event.data) < 21:
        raise FlpError("Expected an automation points event")
    count = struct.unpack_from("<I", event.data, 17)[0]
    if count > 100000 or 21 + count * 24 > len(event.data):
        raise FlpError("Invalid automation point count")
    position = 0.0
    points = []
    for index in range(count):
        offset = 21 + index * 24
        delta, value, tension = struct.unpack_from("<ddf", event.data, offset)
        if not all(math.isfinite(number) for number in (delta, value, tension)) or delta < 0:
            raise FlpError("Malformed automation point")
        position += delta
        points.append(AutomationPoint(position, value, tension, event.data[offset + 20:offset + 24]))
    return points


def replace_automation_points(event: Event, points: list[AutomationPoint]) -> None:
    old_points = automation_points(event)
    if not 2 <= len(points) <= 100000:
        raise FlpError("Automation requires 2 to 100,000 points")
    encoded = bytearray(event.data[:17])
    encoded.extend(struct.pack("<I", len(points)))
    previous = 0.0
    for point in points:
        if not all(math.isfinite(value) for value in (point.position_beats, point.value, point.tension)):
            raise FlpError("Automation values must be finite")
        if point.position_beats < previous or not 0 <= point.value <= 1 or not -1 <= point.tension <= 1:
            raise FlpError("Automation points must be ordered with normalized values")
        if len(point.flags) != 4:
            raise FlpError("Automation point flags must be 4 bytes")
        encoded.extend(struct.pack("<ddf", point.position_beats - previous, point.value, point.tension))
        encoded.extend(point.flags)
        previous = point.position_beats
    encoded.extend(event.data[21 + len(old_points) * 24:])
    event.data = bytes(encoded)


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-16-le").rstrip("\0")
    except UnicodeError as exc:
        raise FlpError("Malformed Unicode name") from exc


def inspect_project(project: FlpFile) -> dict:
    patterns: dict[int, dict] = {}
    channels: list[dict] = []
    playlists: list[dict] = []
    automation: list[dict] = []
    current_pattern: dict | None = None
    current_channel: dict | None = None
    numerator = denominator = 4
    for index, event in enumerate(project.events):
        if event.id == 17:
            numerator = event.data[0]
        elif event.id == 18:
            denominator = event.data[0]
        elif event.id == 65:
            pattern_id = struct.unpack("<H", event.data)[0]
            current_pattern = patterns.setdefault(pattern_id, {"id": pattern_id, "name": f"Pattern {pattern_id}",
                "note_count": 0, "length_ticks": 0, "channels": [], "notes_event_index": None})
        elif event.id == 193 and current_pattern is not None:
            current_pattern["name"] = _text(event.data)
        elif event.id == 224 and current_pattern is not None:
            if len(event.data) % 24:
                raise FlpError("Unsupported note record size")
            used_channels: set[int] = set()
            length = 0
            for offset in range(0, len(event.data), 24):
                position = struct.unpack_from("<I", event.data, offset)[0]
                duration = struct.unpack_from("<I", event.data, offset + 8)[0]
                used_channels.add(struct.unpack_from("<H", event.data, offset + 6)[0])
                length = max(length, position + duration)
            current_pattern.update(note_count=len(event.data) // 24, length_ticks=length,
                                   channels=sorted(used_channels), notes_event_index=index)
        elif event.id == 64:
            channel_id = struct.unpack("<H", event.data)[0]
            current_channel = {"id": channel_id, "name": f"Channel {channel_id + 1}", "type": None,
                               "route": None, "event_start_index": index}
            channels.append(current_channel)
        elif event.id == 99:
            current_channel = None
        elif event.id == 21 and current_channel is not None:
            current_channel["type"] = event.data[0]
        elif event.id in (192, 203) and current_channel is not None:
            current_channel["name"] = _text(event.data)
        elif event.id == 22 and current_channel is not None:
            current_channel["route"] = struct.unpack("<b", event.data)[0]
        elif event.id == 104 and current_channel is not None:
            current_channel["route"] = struct.unpack("<H", event.data)[0]
        elif event.id == 234 and current_channel is not None:
            current_channel["automation_event_index"] = index
            automation.append({"channel_id": current_channel["id"], "name": current_channel["name"],
                "event_index": index, "points": [{"position_beats": point.position_beats,
                "value": point.value, "tension": point.tension, "flags_hex": point.flags.hex()}
                for point in automation_points(event)]})
        elif event.id == 233:
            records = []
            for record in playlist_records(event):
                item_index = struct.unpack_from("<H", record, 6)[0]
                records.append({"position": struct.unpack_from("<I", record, 0)[0],
                    "length": struct.unpack_from("<I", record, 8)[0], "item_index": item_index,
                    "pattern_id": item_index - 20480 if item_index >= 20480 else None,
                    "channel_id": item_index if item_index < 20480 else None,
                    "track": 500 - struct.unpack_from("<H", record, 12)[0], "raw_hex": record.hex()})
            playlists.append({"event_index": index, "records": records})
    return {"version": project.version, "ppq": project.ppq, "tempo": project.tempo,
            "time_signature": [numerator, denominator], "patterns": list(patterns.values()),
            "channels": channels, "playlist": playlists, "automation": automation}
