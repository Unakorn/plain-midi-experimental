from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generator
import workflow


def tiny_kit(genre, target, seed=42):
    target = Path(target)
    target.mkdir()
    (target / "kit.json").write_text(json.dumps({"genre": genre, "seed": seed}))
    return {"folder": str(target)}


class PublicWorkflowTests(unittest.TestCase):
    def test_default_desktop_exports_midi_without_private_template(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {"PLAIN_MIDI_TEMPLATE": ""}), \
             patch("drum_synth.build_kit", side_effect=tiny_kit), \
             patch("flp_export.export_template", side_effect=AssertionError("MIDI mode must not touch FLP")):
            desktop = Path(temporary)
            first = workflow.build_desktop_session({"seed": 8}, desktop=desktop)
            second = workflow.build_desktop_session({"seed": 8}, desktop=desktop)
            self.assertNotEqual(first["project"], second["project"])
            for result in (first, second):
                project = Path(result["project"])
                companion = Path(result["folder"])
                self.assertEqual(project.parent, desktop)
                self.assertEqual(project.suffix, ".mid")
                self.assertEqual(project.stem, companion.name)
                self.assertTrue(project.read_bytes().startswith(b"MThd"))
                self.assertEqual(project.read_bytes(), (companion / "arrangement.mid").read_bytes())
                self.assertEqual(len(list((companion / "parts").glob("*.mid"))), 9)
                self.assertIsNone(result["flp"])
                self.assertIsNone(result["preview"])
            self.assertFalse(list(desktop.rglob(".building-*")))

    def test_bad_opt_in_template_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {"PLAIN_MIDI_TEMPLATE": str(Path(temporary)/"missing.flp")}):
            with self.assertRaisesRegex(FileNotFoundError, "PLAIN_MIDI_TEMPLATE"):
                workflow.build_desktop_session({}, desktop=Path(temporary)/"Desktop")
            self.assertFalse((Path(temporary)/"Desktop").exists())

    def test_failed_export_does_not_publish(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {"PLAIN_MIDI_TEMPLATE": ""}), \
             patch("drum_synth.build_kit", side_effect=RuntimeError("simulated synthesis failure")):
            desktop = Path(temporary)
            existing = desktop / "existing.mid"
            existing.write_bytes(b"keep me")
            with self.assertRaises(RuntimeError):
                workflow.build_desktop_session({}, desktop=desktop)
            self.assertEqual(existing.read_bytes(), b"keep me")
            self.assertEqual(list(desktop.glob("*.mid")), [existing])
            self.assertFalse(list(desktop.rglob(".building-*")))

    def test_kept_melody_requires_same_harmony(self):
        original = generator.generate({"seed": 5})
        next_idea = generator.generate({"seed": 9})
        kept = workflow.keep_selected_parts(next_idea, original, ["LEAD"])
        original_lead = next(t for t in original["tracks"] if t["role"] == "LEAD")
        self.assertEqual(next(t for t in kept["tracks"] if t["role"] == "LEAD"), original_lead)
        changed = generator.generate({"key": "C"})
        with self.assertRaisesRegex(ValueError, "key or chords changed"):
            workflow.keep_selected_parts(changed, original, ["LEAD"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
