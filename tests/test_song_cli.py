"""CLI selection must publish saved music without silently generating it again."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator import generate
from evolution import evolve_song, reorder_song


def load_cli(folder):
    spec = importlib.util.spec_from_file_location(folder.replace("-", "_") + "_cli",
        ROOT / "make.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SongCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apps = [load_cli(folder) for folder in ("plain-midi-public",)]
        cls.idea = generate({"genre": "trance", "auto_chords": True, "seed": 777,
                             "chord_creativity": .7})
        cls.song = reorder_song(evolve_song(cls.idea, seed=901, amount=.6), list("ACBD"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.idea_path = self.folder / "idea.json"
        self.song_path = self.folder / "song.json"
        self.idea_path.write_text(json.dumps(self.idea), encoding="utf-8")
        self.song_path.write_text(json.dumps(self.song), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, app, args, *, desktop=False):
        name = "build_desktop_session" if desktop else "build_session"
        with patch.object(app, name, return_value={"folder": "published"}) as publish, \
             patch.object(sys, "argv", ["make.py", *map(str, args)]), redirect_stdout(io.StringIO()):
            app.main()
        return publish

    def test_loaded_eight_bar_notes_and_settings_are_not_rerolled(self):
        for app in self.apps:
            with self.subTest(app=app.__name__), patch.object(app, "generate", side_effect=AssertionError("rerolled")):
                published = self.run_cli(app, ["--idea", self.idea_path, "--genre", "pop", "--seed", 99])
                args, kwargs = published.call_args
                self.assertEqual(args[0], self.idea["config"])
                self.assertEqual(kwargs["prepared_arrangement"], self.idea)

    def test_loaded_song_keeps_order_bank_and_notes_even_with_song_flag(self):
        for app in self.apps:
            with self.subTest(app=app.__name__), patch.object(app, "generate", side_effect=AssertionError("rerolled")), \
                 patch.object(app, "evolve_song", side_effect=AssertionError("re-evolved")):
                published = self.run_cli(app, ["--idea", self.song_path, "--song", "--evolution", 1, "--seed", 99])
                self.assertEqual(published.call_args.kwargs["prepared_arrangement"], self.song)

    def test_loaded_idea_develops_once_and_keeps_theme_exact(self):
        for app in self.apps:
            with self.subTest(app=app.__name__), patch.object(app, "generate", side_effect=AssertionError("rerolled")):
                published = self.run_cli(app, ["--idea", self.idea_path, "--song", "--evolution", .8, "--seed", 123])
                song = published.call_args.kwargs["prepared_arrangement"]
                self.assertEqual(song["bars"], 32)
                self.assertEqual(song["section_bank"]["A"]["arrangement"], self.idea)
                self.assertEqual(song["evolution"]["seed"], 123)
                self.assertEqual(song["evolution"]["amount"], .8)

    def test_explicit_reorder_and_shuffle_preserve_saved_section_bank(self):
        for app in self.apps:
            for options in (["--order", "DCBA"], ["--shuffle", "--seed", 99]):
                with self.subTest(app=app.__name__, options=options):
                    published = self.run_cli(app, ["--idea", self.song_path, *options])
                    song = published.call_args.kwargs["prepared_arrangement"]
                    self.assertEqual(song["section_bank"], self.song["section_bank"])
                    self.assertNotEqual(song["section_order"], self.song["section_order"])
                    if "--order" in options:
                        self.assertEqual(song["section_order"], list("DCBA"))

    def test_desktop_receives_exact_prepared_song_and_default_export_modes_survive(self):
        for app in self.apps:
            with self.subTest(app=app.__name__):
                desktop = self.run_cli(app, ["--idea", self.song_path, "--desktop"], desktop=True)
                self.assertEqual(desktop.call_args.kwargs["prepared_arrangement"], self.song)
                regular = self.run_cli(app, ["--idea", self.song_path])
                expected_flp = None if app.__name__.startswith("plain_midi_public") else True
                self.assertIs(regular.call_args.kwargs["flp"], expected_flp)

    def test_invalid_song_options_fail_before_publishing(self):
        variants = [["--idea", self.idea_path, "--shuffle"], ["--order", "ABCD"],
                    ["--song", "--evolution", "nan"], ["--song", "--evolution", "1.1"],
                    ["--song", "--order", "ABCA"], ["--song", "--order", "ABCD", "--shuffle"],
                    ["--idea", self.song_path, "--config", self.idea_path]]
        for app in self.apps:
            for args in variants:
                with self.subTest(app=app.__name__, args=args), patch.object(app, "build_session") as publish, \
                     patch.object(sys, "argv", ["make.py", *map(str, args)]), \
                     redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        app.main()
                    self.assertNotEqual(error.exception.code, 0)
                    publish.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
