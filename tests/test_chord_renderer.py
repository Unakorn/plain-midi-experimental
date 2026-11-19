"""Independent checks for rapid harmony, consonant overlaps and MIDI export."""
from copy import deepcopy
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import generator as g
from test_generator import parse_smf


def tracks(a):
    return {t["role"]: t for t in a["tracks"]}


def bounds(ch):
    return round(ch["bar"] * 4 * 480), round((ch["bar"] + ch["bars"]) * 4 * 480)


class CreativeRendererTests(unittest.TestCase):
    def assert_harmony(self, a, native_grid=False):
        progression = a["progression"]
        self.assertEqual(bounds(progression[0])[0], 0)
        self.assertEqual(bounds(progression[-1])[1], 32*480)
        for previous, current in zip(progression, progression[1:]):
            self.assertEqual(bounds(previous)[1], bounds(current)[0])
        for role, track in tracks(a).items():
            self.assertGreater(max(n["start"] + n["duration"] for n in track["notes"]), 28)
            pitches = {}
            for note in track["notes"]:
                begin = round(note["start"] * 480)
                end = round((note["start"] + note["duration"]) * 480)
                self.assertGreater(end, begin)
                self.assertLessEqual(end, 32*480)
                self.assertGreaterEqual(begin, pitches.get(note["pitch"], 0))
                pitches[note["pitch"]] = end
                if role in ("DRUMS", "NOISE"):
                    continue
                if native_grid:
                    self.assertEqual(begin % 5, 0)
                    self.assertEqual(end % 5, 0)
                sounding = [ch for ch in progression if bounds(ch)[0] < end and bounds(ch)[1] > begin]
                self.assertTrue(sounding)
                for chord in sounding:
                    self.assertIn(note["pitch"] % 12, chord["pitch_classes"],
                                  (role, note, chord["symbol"]))
                    if role in ("BASS", "SUB_BASS"):
                        self.assertEqual(note["pitch"] % 12, chord["root"] % 12)
                if len(sounding) > 1:
                    self.assertIn(role, ("PAD", "CHORDS"))
                    self.assertTrue(note.get("tie_boundaries"))
            if role in ("BASS", "SUB_BASS", "LEAD", "PLUCK", "ARPEGGIO"):
                for note, following in zip(track["notes"], track["notes"][1:]):
                    self.assertLessEqual(note["start"] + note["duration"], following["start"] + 1e-8)

    def test_all_manual_counts_follow_the_same_harmony(self):
        symbols = ("Am9", "Fmaj7", "Gsus4", "E7", "Dm7", "Cadd9", "Bm7b5")
        for count in range(1, 33):
            a = g.generate({"progression": " ".join(symbols[i % len(symbols)] for i in range(count)),
                            "swing": .6, "density": .1, "chord_style": "rhythm"})
            with self.subTest(count=count):
                self.assertEqual(len(a["progression"]), count)
                self.assert_harmony(a, native_grid=count not in (1, 2, 4, 8))
                self.assertEqual(len(parse_smf(g.midi_bytes(a))), 10)
                for role in ("BASS", "SUB_BASS", "LEAD", "PLUCK", "ARPEGGIO"):
                    for chord in a["progression"]:
                        lo, hi = bounds(chord)
                        self.assertTrue(any(lo <= round(n["start"]*480) < hi for n in tracks(a)[role]["notes"]),
                                        (count, role, chord))

    def test_auto_harmony_at_all_levels_and_genres(self):
        for genre in g.GENRES:
            for creativity in (0, .25, .5, .75, 1):
                for style in ("block", "rhythm"):
                    a = g.generate({"genre": genre, "auto_chords": True,
                                    "chord_creativity": creativity, "chord_style": style,
                                    "swing": .6, "density": .1, "seed": 71})
                    with self.subTest(genre=genre, creativity=creativity, style=style):
                        self.assert_harmony(a, native_grid=True)
                        parsed = parse_smf(g.midi_bytes(a))
                        for stream in parsed:
                            active = set()
                            for event in stream:
                                if event[1] != "note":
                                    continue
                                _, _, status, pitch, velocity = event
                                key = (status & 15, pitch)
                                if status & 240 == 144 and velocity:
                                    self.assertNotIn(key, active)
                                    active.add(key)
                                else:
                                    self.assertIn(key, active)
                                    active.remove(key)
                            self.assertFalse(active)

    def test_mixed_keys_modes_styles_and_extremes(self):
        rng = random.Random(1892)
        for _ in range(80):
            a = g.generate({"genre": rng.choice(list(g.GENRES)), "auto_chords": True,
                            "chord_creativity": rng.random(), "seed": rng.randrange(100000),
                            "key": rng.choice(list(g.KEYS)), "mode": rng.choice(list(g.SCALES)),
                            "density": rng.choice((.1, 1)), "swing": rng.random()*.6,
                            "chord_style": rng.choice(("block", "rhythm")),
                            "lead_style": rng.choice(("lead", "pluck"))})
            self.assert_harmony(a, native_grid=True)

    def test_common_tone_overlaps_are_real_and_controlled(self):
        fixed = g.generate({"progression": "Am7 Fmaj7 Cmaj7 G7 Am7 Fmaj7 Dm7 E7"})["progression"]
        counts = []
        for creativity in (0, .5, 1):
            a = g.generate({"auto_chords": True, "chord_creativity": creativity},
                           progression_override=fixed)
            self.assert_harmony(a, native_grid=True)
            ties = [n for role in ("PAD", "CHORDS") for n in tracks(a)[role]["notes"] if n.get("tie_boundaries")]
            counts.append(sum(len(n["tie_boundaries"]) for n in ties))
        self.assertEqual(counts[0], 0)
        self.assertGreater(counts[1], 0)
        self.assertGreater(counts[2], counts[1])

    def test_determinism_independence_and_kept_harmony_override(self):
        config = {"auto_chords": True, "chord_creativity": .8, "seed": 77}
        first = g.generate(config)
        self.assertEqual(first, g.generate(config))
        other = g.generate({**config, "chord_creativity": .2})
        self.assertEqual(tracks(first)["DRUMS"], tracks(other)["DRUMS"])
        self.assertEqual(tracks(first)["NOISE"], tracks(other)["NOISE"])
        original = deepcopy(first["progression"])
        override = g.generate({**config, "seed": 78}, progression_override=original)
        self.assertEqual(override["progression"], original)
        self.assertIsNot(override["progression"], original)
        self.assertTrue(override["config"]["auto_chords"])
        self.assert_harmony(override, native_grid=True)
        self.assertNotEqual(tracks(first)["LEAD"], tracks(override)["LEAD"])
        bad = deepcopy(original)
        bad[0]["bars"] /= 2
        with self.assertRaises(ValueError):
            g.generate(config, progression_override=bad)
        for invalid in ({"auto_chords": "yes"}, {"chord_creativity": 1.1},
                        {"chord_creativity": True}, {"chord_creativity": float("nan")}):
            with self.assertRaises(ValueError):
                g.generate(invalid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
