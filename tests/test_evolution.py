"""Independent musical, saved-song, MIDI and preview checks for song evolution."""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generator as g
import evolution as e
import drum_synth as ds


def tracks(arrangement):
    return {track["role"]: track for track in arrangement["tracks"]}


def smf(data, bars):
    """Read serialized MIDI independently, including all note pairs/end times."""
    assert data[:4] == b"MThd"
    length, kind, count, ppq = struct.unpack_from(">IHHH", data, 4)
    assert (length, kind, ppq) == (6, 1, 480)
    cursor, streams = 14, []
    for _ in range(count):
        assert data[cursor:cursor+4] == b"MTrk"
        size, = struct.unpack_from(">I", data, cursor+4)
        payload = data[cursor+8:cursor+8+size]
        cursor += 8+size
        index, time, events, active = 0, 0, [], set()
        def vlq():
            nonlocal index
            value = 0
            for _ in range(4):
                byte = payload[index]
                index += 1
                value = (value << 7) | (byte & 127)
                if byte < 128:
                    return value
            raise AssertionError("Invalid variable-length MIDI integer")
        while index < len(payload):
            time += vlq()
            status = payload[index]
            index += 1
            if status == 255:
                meta = payload[index]
                index += 1
                size = vlq()
                events.append((time, "meta", meta, payload[index:index+size]))
                index += size
            else:
                assert status & 240 in (128, 144)
                pitch, velocity = payload[index:index+2]
                index += 2
                identity = (status & 15, pitch)
                if status & 240 == 144 and velocity:
                    assert identity not in active, (time, identity, "retrigger without off")
                    active.add(identity)
                else:
                    assert identity in active, (time, identity, "unpaired off")
                    active.remove(identity)
                events.append((time, "note", status, pitch, velocity))
        assert not active
        assert events[-1] == (bars*4*480, "meta", 47, b"")
        streams.append(events)
    assert cursor == len(data)
    return streams


class EvolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = g.generate({"seed": 42, "auto_chords": True, "chord_creativity": .8,
                                 "swing": .6, "chord_style": "rhythm"})
        cls.song = e.evolve_song(cls.source, seed=817, amount=.7)

    def assert_music(self, arrangement):
        """Check sounding spans against harmony at both SMF and FL native grids."""
        beats = arrangement["bars"]*4
        for resolution in (480, 96):
            progression = [(round(c["bar"]*4*resolution),
                            round((c["bar"]+c["bars"])*4*resolution), c)
                           for c in arrangement["progression"]]
            self.assertEqual(progression[0][0], 0)
            self.assertEqual(progression[-1][1], beats*resolution)
            self.assertTrue(all(a[1] == b[0] for a, b in zip(progression, progression[1:])))
            for role, track in tracks(arrangement).items():
                previous, last_end = {}, 0
                self.assertGreater(max(n["start"]+n["duration"] for n in track["notes"]), beats-4)
                for note in track["notes"]:
                    start = round(note["start"]*resolution)
                    end = round((note["start"]+note["duration"])*resolution)
                    self.assertGreater(end, start)
                    self.assertGreaterEqual(start, previous.get(note["pitch"], 0))
                    previous[note["pitch"]] = end
                    self.assertLessEqual(end, beats*resolution)
                    # Every note belongs entirely to a movable eight-bar section.
                    self.assertEqual(start//(32*resolution), (end-1)//(32*resolution))
                    if role in ("DRUMS", "NOISE"):
                        continue
                    sounding = [c for lo, hi, c in progression if lo < end and hi > start]
                    self.assertTrue(sounding)
                    for chord in sounding:
                        self.assertIn(note["pitch"] % 12, chord["pitch_classes"], (role, note, chord))
                        if role in ("BASS", "SUB_BASS"):
                            self.assertEqual(note["pitch"] % 12, chord["root"] % 12)
                    if len(sounding) > 1:
                        self.assertIn(role, ("PAD", "CHORDS"))
                        self.assertTrue(note.get("tie_boundaries"))
                    if role in ("BASS", "SUB_BASS", "LEAD", "PLUCK", "ARPEGGIO"):
                        self.assertGreaterEqual(start, last_end)
                        last_end = end

    def test_source_exact_determinism_and_independence(self):
        original = deepcopy(self.source)
        song = e.evolve_song(self.source, seed=817, amount=.7)
        self.assertEqual(song, self.song)
        self.assertEqual(self.source, original)
        self.assertEqual(song["section_bank"]["A"]["arrangement"], original)
        self.assertEqual(song["seed"], 817)
        song["tracks"][0]["notes"][0]["velocity"] = 1
        self.assertEqual(self.source, original)
        self.assertEqual(song["section_bank"]["A"]["arrangement"], original)
        changed = e.evolve_song(self.source, seed=818, amount=.7)
        self.assertNotEqual(changed["tracks"], self.song["tracks"])

    def test_zero_is_four_exact_theme_copies(self):
        song = e.evolve_song(self.source, seed=99, amount=0)
        for section in song["section_bank"].values():
            self.assertEqual(section["arrangement"], self.source)
        self.assert_music(song)

    def test_themes_keep_attacks_while_harmony_and_energy_develop(self):
        bank = self.song["section_bank"]
        self.assertNotEqual(bank["B"]["arrangement"]["progression"], self.source["progression"])
        self.assertNotEqual(bank["C"]["arrangement"]["progression"], self.source["progression"])
        self.assertEqual(bank["D"]["arrangement"]["progression"], self.source["progression"])
        for identity in "BCD":
            section = bank[identity]["arrangement"]
            self.assertNotEqual(section["tracks"], self.source["tracks"])
            for role in ("LEAD", "PLUCK", "ARPEGGIO", "BASS", "SUB_BASS"):
                original = {round(n["start"]*96) for n in tracks(self.source)[role]["notes"]}
                evolved = {round(n["start"]*96) for n in tracks(section)[role]["notes"]}
                ratio = len(original & evolved)/len(original)
                self.assertGreaterEqual(ratio, .74 if identity == "C" else .99, (identity, role))
            for role in ("BASS", "SUB_BASS"):
                onsets = {round(n["start"]*96) for n in tracks(section)[role]["notes"]}
                self.assertTrue({round(c["bar"]*4*96) for c in section["progression"]} <= onsets)

    def test_genres_modes_density_and_harmony_extremes(self):
        for index, genre in enumerate(g.GENRES):
            for level in (0, .35, .75, 1):
                source = g.generate({"genre": genre, "seed": index*17+13, "auto_chords": True,
                                     "chord_creativity": level, "swing": .6,
                                     "key": ("C#", "F", "Bb")[index % 3],
                                     "mode": ("minor", "major")[index % 2],
                                     "chord_style": "rhythm" if index % 2 else "block",
                                     "density": .1 if level < .5 else 1})
                song = e.evolve_song(source, seed=1000+index, amount=level or .1)
                with self.subTest(genre=genre, creativity=level):
                    self.assert_music(song)
                    for section in song["section_bank"].values():
                        self.assert_music(section["arrangement"])
                    self.assertEqual(len(smf(g.midi_bytes(song), 32)), 10)

    def test_odd_manual_counts_and_saved_common_tone_support(self):
        for count in (1, 7, 13, 32):
            symbols = ("Am7", "Fmaj7", "Cmaj7", "G7", "Dm7", "E7")
            source = g.generate({"progression": " ".join(symbols[i % 6] for i in range(count)),
                                 "seed": 71, "chord_style": "rhythm", "swing": .6})
            song = e.evolve_song(source, seed=32+count, amount=1)
            self.assert_music(song)
            for section in song["section_bank"].values():
                self.assert_music(section["arrangement"])
        for identity in "BCD":
            section = self.song["section_bank"][identity]["arrangement"]
            for role in ("PAD", "CHORDS"):
                for chord in section["progression"]:
                    begin, end = chord["bar"]*4, (chord["bar"]+chord["bars"])*4
                    self.assertTrue(any(n["start"] < end and n["start"]+n["duration"] > begin
                                        for n in tracks(section)[role]["notes"]))

    def test_keep_roles_exact_and_harmony_lock(self):
        for kept in (("DRUMS",), ("LEAD",), ("BASS", "PAD", "DRUMS"), tuple(g.ROLES)):
            song = e.evolve_song(self.source, seed=817, amount=1, keep_roles=kept)
            for section in song["section_bank"].values():
                arrangement = section["arrangement"]
                for role in kept:
                    self.assertEqual(tracks(arrangement)[role], tracks(self.source)[role])
                if set(kept) & e.PITCHED:
                    self.assertEqual(arrangement["progression"], self.source["progression"])
            self.assert_music(song)
        self.assertNotEqual(e.evolve_song(self.source, seed=817, amount=1, keep_roles=("DRUMS",))
                            ["section_bank"]["C"]["arrangement"]["progression"], self.source["progression"])

    def test_all_orders_move_identical_sections_with_ties(self):
        original = deepcopy(self.song)
        for order in itertools.permutations("ABCD"):
            song = e.reorder_song(self.song, order)
            self.assertEqual(song["section_bank"], original["section_bank"])
            self.assertEqual(song["section_order"], list(order))
            self.assert_music(song)
            for position, identity in enumerate(order):
                for role, track in tracks(song).items():
                    restored = []
                    for note in track["notes"]:
                        if position*32 <= note["start"] < (position+1)*32:
                            note = deepcopy(note)
                            note["start"] -= position*32
                            if "tie_boundaries" in note:
                                note["tie_boundaries"] = [x-position*32 for x in note["tie_boundaries"]]
                            restored.append(note)
                    expected = deepcopy(tracks(original["section_bank"][identity]["arrangement"])[role]["notes"])
                    # Float offset subtraction may lose a few ulps; native and
                    # MIDI timing must be identical and other fields exact.
                    for notes in (restored, expected):
                        for note in notes:
                            note["start"] = round(note["start"], 8)
                    self.assertEqual(restored, expected)
        self.assertEqual(self.song, original)

    def test_shuffle_is_deterministic_and_never_identity(self):
        for seed in range(12):
            shuffled = e.shuffle_song(self.song, seed)
            self.assertNotEqual(shuffled["section_order"], self.song["section_order"])
            self.assertEqual(shuffled, e.shuffle_song(self.song, seed))
            self.assertEqual(shuffled["section_bank"], self.song["section_bank"])

    def test_midi_sections_endpoints_bundle_and_json_roundtrip(self):
        streams = smf(g.midi_bytes(self.song), 32)
        markers = [event for event in streams[0] if event[1:3] == ("meta", 6) and event[3].startswith(b"Section ")]
        self.assertEqual([event[0] for event in markers], [0, 32*480, 64*480, 96*480])
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "song"
            g.write_bundle(self.song, folder)
            saved = json.loads((folder / "arrangement.json").read_text(encoding="utf-8"))
            self.assertEqual(e.validate_song(saved), self.song)
            self.assertEqual(e.shuffle_song(saved, 9), e.shuffle_song(self.song, 9))
            for path in folder.rglob("*.mid"):
                smf(path.read_bytes(), 8 if path.parent.name == "sections" else 32)
            self.assertEqual(len(list((folder / "sections").glob("*.mid"))), 4)

    def test_invalid_orders_metadata_and_stale_flattening_rejected(self):
        for order in ("ABCD", [], ["A"]*4, ["A", "B", "C", "X"], ["A", "B", "C", 4]):
            with self.assertRaises(ValueError):
                e.reorder_song(self.song, order)
        for field, value in (("amount", -1), ("amount", True), ("amount", float("nan")), ("seed", -1), ("seed", True)):
            arguments = {"seed": 10, "amount": .5, field: value}
            with self.assertRaises(ValueError):
                e.evolve_song(self.source, **arguments)
        for change in (lambda s: s["tracks"][0]["notes"][0].update(velocity=1),
                       lambda s: s.update(section_order=list("DCBA")),
                       lambda s: s.update(section_bank=[]),
                       lambda s: s["section_bank"].update(B=None),
                       lambda s: s.update(sections=[None]*4),
                       lambda s: s["evolution"].update(keep_roles=[{}]),
                       lambda s: s["section_bank"]["C"]["arrangement"]["tracks"][1].update(channel=11)):
            song = deepcopy(self.song)
            change(song)
            with self.assertRaises(ValueError):
                e.validate_song(song)

    def test_preview_length_reaches_bar_32(self):
        # A short audible end note tests placement without rendering a heavy
        # full song. The preview still allocates/writes all 32 bars.
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            (temporary / "kit.json").write_text('{"samples": []}', encoding="utf-8")
            arrangement = {"bars": 32, "bpm": 300, "tracks": [{"role": "PAD", "notes": [
                {"start": 127, "duration": .75, "pitch": 60, "velocity": 100}]}]}
            result = ds.render_preview(arrangement, temporary, temporary / "preview.wav")
            self.assertAlmostEqual(result["duration_seconds"], 25.6)
            with wave.open(str(temporary / "preview.wav"), "rb") as stream:
                self.assertEqual(stream.getnframes(), round(25.6*ds.SAMPLE_RATE))
                stream.setpos(round(25.4*ds.SAMPLE_RATE))
                self.assertNotEqual(set(stream.readframes(1000)), {0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
