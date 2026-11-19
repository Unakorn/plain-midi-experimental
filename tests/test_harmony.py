"""Shared-timeline correctness and creative chord behavior."""
from pathlib import Path
import copy
import argparse
import hashlib
import json
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import harmony as h

GENRES = ("trance", "hard_dance", "hard_trance", "house", "hard_house", "hardstyle", "pop", "rap")


def config(**updates):
    return {"genre": "house", "key": "A", "mode": "minor", "seed": 42,
            "progression": "1 6 3 7", "auto_chords": True,
            "chord_creativity": .5, **updates}


class HarmonyTests(unittest.TestCase):
    def test_manual_one_to_thirty_two_and_legacy_timing(self):
        for count in range(1, 33):
            progression = h.build_progression(config(auto_chords=False, progression=" ".join(str(i % 7+1) for i in range(count))))
            self.assertEqual(len(progression), count)
            h.validate_progression(progression)
            for i, chord in enumerate(progression):
                self.assertAlmostEqual(chord["start"]*96, round(i*32*96/count))
                self.assertAlmostEqual(chord["start"]*480, round(chord["start"]*480))
                self.assertGreater(chord["duration"], 0)
            self.assertEqual(progression[-1]["start"]+progression[-1]["duration"], 32)
            if count in (1, 2, 4, 8):
                self.assertEqual([c["bar"] for c in progression], [i*8//count for i in range(count)])
                self.assertTrue(all(c["bars"] == 8//count for c in progression))
        explicit = h.build_progression(config(auto_chords=False, progression="AmMaj9 Fmaj7 Cmaj7#5 G7sus4"))
        self.assertEqual([c["symbol"] for c in explicit], ["AmMaj9", "Fmaj7", "Cmaj7#5", "G7sus4"])

    def test_creativity_refines_timing_monotonically(self):
        for genre in GENRES:
            for seed in range(20):
                previous_count = 0
                previous_starts = set()
                for i in range(21):
                    settings = config(genre=genre, seed=seed, chord_creativity=i/20)
                    p = h.build_progression(settings)
                    self.assertGreaterEqual(len(p), previous_count)
                    starts = {c["start"] for c in p}
                    self.assertTrue(previous_starts <= starts)
                    self.assertTrue(all(c["duration"] >= 1 for c in p))
                    self.assertEqual(p[0]["root"] % 12, h.KEYS[settings["key"]])
                    if len(p) >= 6:
                        self.assertEqual(p[-1]["root"] % 12, h.KEYS[settings["key"]])
                    previous_count, previous_starts = len(p), starts
                self.assertGreaterEqual(previous_count, 24)

    def test_all_modes_keys_genres_and_harmonic_metadata(self):
        for genre in GENRES:
            for key in ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"):
                for mode in h.SCALES:
                    settings = config(genre=genre, key=key, mode=mode, chord_creativity=1)
                    p = h.build_progression(settings)
                    home = {(h.KEYS[key]+n) % 12 for n in h.SCALES[mode]}
                    for i, chord in enumerate(p):
                        if chord["source"] == "diatonic":
                            self.assertTrue(set(chord["pitch_classes"]) <= home)
                        if chord["source"] == "secondary_dominant":
                            self.assertEqual(chord["secondary_target"], p[i+1]["root"] % 12)
                            self.assertEqual(chord["root"] % 12, (p[i+1]["root"]+7) % 12)
                        if i:
                            self.assertNotEqual(set(chord["pitch_classes"]), set(p[i-1]["pitch_classes"]), (settings, p))

    def test_seed_diversity_reproduction_and_independence(self):
        for genre in GENRES:
            signatures = set()
            for seed in range(100):
                settings = config(genre=genre, seed=seed, chord_creativity=.65)
                p = h.build_progression(settings)
                self.assertEqual(p, h.build_progression(settings))
                # Melody and drum controls must not reroll the harmony.
                self.assertEqual(p, h.build_progression({**settings, "density": .9, "variation": 1,
                                                       "lead_style": "pluck", "swing": .6}))
                signatures.add(tuple((c["start"], c["duration"], tuple(c["pitch_classes"])) for c in p))
            self.assertGreaterEqual(len(signatures), 98)

    def test_richer_harmony_and_invalid_timeline_rejection(self):
        sparse, rich = [], []
        for seed in range(100):
            sparse.extend(h.build_progression(config(seed=seed, chord_creativity=0)))
            rich.extend(h.build_progression(config(seed=seed, chord_creativity=1)))
        self.assertTrue(all(len(c["pitch_classes"]) == 3 for c in sparse))
        self.assertGreater(sum(len(c["pitch_classes"]) for c in rich)/len(rich), 3.5)
        self.assertTrue(any(c["source"] == "borrowed" for c in rich))
        self.assertTrue(any(c["source"] == "secondary_dominant" for c in rich))
        for count in (0, 33):
            with self.assertRaises(ValueError):
                h.build_progression(config(auto_chords=False, progression=" ".join(["Am"]*count)))
        for value in ("Am wrong F G", "8", "H", "A/xx"):
            with self.assertRaises(ValueError):
                h.build_progression(config(auto_chords=False, progression=value))
        valid = h.build_progression(config())
        for field, bad in (("bar", .001), ("bars", 0), ("root", True), ("root", 70),
                           ("pitch_classes", [9, 9, 0]), ("symbol", ""), ("start", 7)):
            changed = copy.deepcopy(valid)
            changed[0][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                h.validate_progression(changed)


def audit(count=100):
    report = {"seed_range": [0, count-1], "key": "A", "mode": "minor",
              "creativity_levels": [0, .25, .5, .75, 1],
              "engine_sha256": hashlib.sha256(Path(h.__file__).read_bytes()).hexdigest(),
              "comparison": "ordered chord start/duration and root-first pitch-class list; no velocities or instrument notes",
              "caveat": "Finite deterministic sample; perceived musical quality and universal collision probability are not established.",
              "genres": {}, "examples": {}}
    started = time.perf_counter()
    generated = 0
    for genre in GENRES:
        report["genres"][genre] = {}
        for creativity in report["creativity_levels"]:
            fingerprints, counts, voices = set(), [], []
            shared = transitions = 0
            sources = {}
            for seed in range(count):
                p = h.build_progression(config(genre=genre, seed=seed, chord_creativity=creativity))
                fingerprints.add(tuple((c["start"], c["duration"], tuple(c["pitch_classes"])) for c in p))
                counts.append(len(p))
                voices.extend(len(c["pitch_classes"]) for c in p)
                for chord in p:
                    sources[chord["source"]] = sources.get(chord["source"], 0)+1
                for a, b in zip(p, p[1:]):
                    transitions += 1
                    shared += bool(set(a["pitch_classes"]) & set(b["pitch_classes"]))
                generated += 1
            report["genres"][genre][str(creativity)] = {
                "samples": count, "unique_progressions": len(fingerprints),
                "duplicate_samples": count-len(fingerprints),
                "chord_count_min": min(counts), "chord_count_max": max(counts),
                "chord_count_mean": round(sum(counts)/count, 3),
                "mean_chord_tones": round(sum(voices)/len(voices), 3),
                "adjacent_pairs_with_common_tones": shared,
                "adjacent_pairs": transitions,
                "common_tone_ratio": round(shared/transitions, 4), "source_counts": sources,
            }
        print(genre, "; ".join(f"{level}: {result['unique_progressions']}/{count} unique, {result['chord_count_min']}-{result['chord_count_max']} chords"
                              for level, result in report["genres"][genre].items()), flush=True)
    report["examples"] = {str(level): h.build_progression(config(seed=42, chord_creativity=level))
                          for level in report["creativity_levels"]}
    report["total_progressions"] = generated
    report["elapsed_seconds"] = round(time.perf_counter()-started, 3)
    report["mean_generation_ms"] = round(report["elapsed_seconds"]*1000/generated, 4)
    destination = ROOT / "harmony_metrics.json"
    destination.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--seeds", type=int, default=100)
    args = parser.parse_args()
    if args.audit:
        result = audit(args.seeds)
        for genre, levels in result["genres"].items():
            for level in ("0.5", "1"):
                assert levels[level]["unique_progressions"] >= args.seeds*.98, (genre, level)
    else:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
