"""Retained parts keep exact generated chord timing while other parts reroll."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generator as g
import workflow as w


class HarmonyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.config = {"auto_chords": True, "chord_creativity": .8, "seed": 14}
        self.first = g.generate(self.config)

    def test_kept_part_carries_exact_harmonic_timeline_across_generations(self):
        before = copy.deepcopy(self.first)
        second = w._generate_with_kept_harmony({**self.config, "seed": 97}, self.first, ["LEAD"])
        third = w._generate_with_kept_harmony({**self.config, "seed": 709}, second, ["LEAD"])
        self.assertEqual(self.first, before)
        for result in (second, third):
            self.assertTrue(result["harmony_kept"])
            self.assertEqual(result["progression"], self.first["progression"])
            self.assertEqual(result["tracks"][5], self.first["tracks"][5])
            self.assertNotEqual(result["tracks"][2], self.first["tracks"][2])
            self.assertEqual(result["kept_roles"], ["LEAD"])
            g.validate_arrangement(result)

    def test_harmony_control_changes_are_explicit_when_a_melodic_part_is_kept(self):
        changes = [{"genre": "rap"}, {"key": "C"}, {"mode": "major"},
                   {"auto_chords": False}, {"chord_creativity": .1}]
        for change in changes:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Chord settings changed"):
                w._generate_with_kept_harmony({**self.config, **change}, self.first, ["BASS"])
        # Nonharmonic controls still allow new accompaniment around the kept line.
        result = w._generate_with_kept_harmony({**self.config, "bpm": 142, "density": .8,
                                              "swing": .3, "variation": .8, "seed": 81}, self.first, ["BASS"])
        self.assertEqual(result["progression"], self.first["progression"])
        self.assertEqual(result["tracks"][6], self.first["tracks"][6])

    def test_drums_and_noise_do_not_lock_harmony(self):
        next_config = {**self.config, "seed": 97, "key": "C"}
        result = w._generate_with_kept_harmony(next_config, self.first, ["DRUMS", "NOISE"])
        self.assertFalse(result.get("harmony_kept", False))
        self.assertEqual(result["progression"], g.generate(next_config)["progression"])
        self.assertNotEqual(result["progression"], self.first["progression"])
        self.assertEqual(result["tracks"][0], self.first["tracks"][0])

    def test_published_json_and_midi_preserve_kept_harmony(self):
        def tiny_kit(genre, folder, seed=42):
            Path(folder).mkdir()
            return {"folder": str(folder)}
        with tempfile.TemporaryDirectory() as temporary, patch("drum_synth.build_kit", side_effect=tiny_kit):
            result = w.build_session({**self.config, "seed": 97}, temporary,
                                     previous=self.first, keep_roles=["LEAD"], audio=False, flp=False)
            saved = json.loads((Path(result["folder"]) / "arrangement.json").read_text())
            self.assertEqual(saved["progression"], self.first["progression"])
            self.assertTrue(saved["harmony_kept"])
            self.assertEqual(Path(result["midi"]).read_bytes(), g.midi_bytes(saved))


if __name__ == "__main__":
    unittest.main(verbosity=2)
