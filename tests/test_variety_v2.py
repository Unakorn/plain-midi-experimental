"""Musical invariants and an empirical structural-diversity audit.

Run normally for checks; --audit writes a 1,000-seed-per-genre JSON report.
Fingerprints deliberately discard velocities, drum sound variants and sub-
sixteenth timing differences. These samples are evidence, not a probability
guarantee for every possible seed, setting or human perception of similarity.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import argparse
import hashlib
import json
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generator as g


def track(a, role):
    return next(t for t in a["tracks"] if t["role"] == role)


def pitch_id(role, note):
    if role == "DRUMS":
        return next(f for f, base in g.DRUM_MAP.items() if base <= note["pitch"] < base+4)
    return note["pitch"]


def structure(t, rhythm_only=False, pitch_only=False):
    notes = t["notes"]
    if rhythm_only:
        # Count simultaneous chord voices as a single attack. Quantization
        # also removes swing offsets and any future tiny timing jitter.
        return tuple(sorted({round(n["start"]*4) for n in notes}))
    if pitch_only:
        # Remove repeated-pitch runs so changing only rhythm/gates does not
        # pass the independent melodic-contour audit.
        pitches = [pitch_id(t["role"], n) for n in notes]
        return tuple(p for i, p in enumerate(pitches) if not i or p != pitches[i-1])
    return tuple((round(n["start"]*4), round(n["duration"]*4), pitch_id(t["role"], n))
                 for n in notes)


def digest(value):
    return hashlib.sha256(repr(value).encode()).hexdigest()


def metrics(values):
    counts = Counter(values)
    n = len(values)
    pairs = sum(k*(k-1)//2 for k in counts.values())
    adjacent = sum(a == b for a, b in zip(values, values[1:]))
    return {"samples": n, "unique": len(counts), "duplicate_samples": n-len(counts),
            "duplicate_sample_rate": (n-len(counts))/n,
            "matching_pairs": pairs, "pair_collision_rate": pairs/(n*(n-1)/2) if n > 1 else 0,
            "adjacent_repeats": adjacent,
            "adjacent_repeat_rate": adjacent/(n-1) if n > 1 else 0}


def audit(count=1000):
    report = {"seed_range": [0, count-1], "settings": "genre defaults; A minor; variation 0.3; density 0.5",
              "fingerprint": "onsets and durations rounded to sixteenths; velocity excluded; drum family collapses all four sound variants",
              "caveat": "Finite deterministic sample; neither a universal collision probability nor a musical-quality guarantee.",
              "genres": {}}
    started = time.perf_counter()
    for genre in g.GENRES:
        signatures = {name: [] for name in ("arrangement", "all_onset_rhythms", "drums",
                       "drum_onsets", "lead", "lead_rhythm", "lead_pitch_contour", "bass_rhythm", "pluck", "arpeggio")}
        note_counts = []
        for seed in range(count):
            a = g.generate({"genre": genre, "seed": seed})
            signatures["arrangement"].append(digest(tuple((t["role"], structure(t)) for t in a["tracks"])))
            signatures["all_onset_rhythms"].append(digest(tuple((t["role"], structure(t, rhythm_only=True)) for t in a["tracks"])))
            for role, label in (("DRUMS", "drums"), ("LEAD", "lead"), ("PLUCK", "pluck"), ("ARPEGGIO", "arpeggio")):
                signatures[label].append(digest(structure(track(a, role))))
            for role, label in (("DRUMS", "drum_onsets"), ("LEAD", "lead_rhythm"), ("BASS", "bass_rhythm")):
                signatures[label].append(digest(structure(track(a, role), rhythm_only=True)))
            signatures["lead_pitch_contour"].append(digest(structure(track(a, "LEAD"), pitch_only=True)))
            note_counts.append(len(track(a, "LEAD")["notes"]))
        result = {name: metrics(values) for name, values in signatures.items()}
        result["lead_note_count"] = {"min": min(note_counts), "max": max(note_counts), "mean": sum(note_counts)/count}
        report["genres"][genre] = result
        print(genre, " ".join(f"{k}={v['unique']}/{count}" for k, v in result.items() if "unique" in v), flush=True)
    report["elapsed_seconds"] = round(time.perf_counter()-started, 3)
    path = ROOT / "variety_v2_metrics.json"
    path.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return report


class VarietyTests(unittest.TestCase):
    def test_editable_drum_recipes_remain_influential(self):
        # A distinctive legal recipe hit must be reachable for every family.
        # These edits must not become silently unused after adding variation.
        for genre in g.GENRES:
            original = g.GENRES[genre]
            try:
                for family in g.DRUM_MAP:
                    custom = deepcopy(original)
                    custom["drums"][family] = [.125]
                    g.GENRES[genre] = custom
                    reached = False
                    for seed in range(32):
                        a = g.generate({"genre": genre, "seed": seed, "swing": 0})
                        if any(n["family"] == family and abs(n["start"] % 4-.125) < 1e-8
                               for n in track(a, "DRUMS")["notes"]):
                            reached = True
                            break
                    self.assertTrue(reached, (genre, family))
            finally:
                g.GENRES[genre] = original

    def test_many_actual_rhythms_and_melodies(self):
        for genre in g.GENRES:
            ideas, rhythms, contours, drums = set(), set(), set(), set()
            for seed in range(160):
                a = g.generate({"genre": genre, "seed": seed})
                lead = track(a, "LEAD")
                ideas.add(structure(lead))
                rhythms.add(structure(lead, rhythm_only=True))
                contours.add(structure(lead, pitch_only=True))
                drums.add(structure(track(a, "DRUMS")))
            with self.subTest(genre=genre):
                self.assertGreaterEqual(len(ideas), 128)
                self.assertGreaterEqual(len(rhythms), 128)
                self.assertGreaterEqual(len(contours), 128)
                self.assertGreaterEqual(len(drums), 128)

    def test_zero_variation_still_gets_new_seed_ideas(self):
        for genre in g.GENRES:
            signatures = set()
            for seed in range(32):
                a = g.generate({"genre": genre, "seed": seed, "variation": 0})
                signatures.add((structure(track(a, "LEAD")), structure(track(a, "DRUMS"))))
            self.assertEqual(len(signatures), 32, genre)

    def test_seed_and_role_independence(self):
        for genre in g.GENRES:
            settings = {"genre": genre, "seed": 7123}
            a = g.generate(settings)
            self.assertEqual(a, g.generate(settings))
            style = g.generate({**settings, "lead_style": "pluck"})
            mute = g.generate({**settings, "muted_roles": ["DRUMS", "LEAD"]})
            variants = g.generate({**settings, "drum_variants": False})
            for t in a["tracks"]:
                role = t["role"]
                if role != "LEAD":
                    self.assertEqual(t, track(style, role))
                self.assertEqual(t["notes"], track(mute, role)["notes"])
                self.assertEqual(structure(t), structure(track(variants, role)))

    def test_genre_anchors_chords_ranges_and_hat_chokes(self):
        for genre in g.GENRES:
            for density in (.1, .5, 1):
                for seed in range(5):
                    a = g.generate({"genre": genre, "seed": seed, "density": density,
                                    "variation": 1, "swing": .6,
                                    "progression": "Am F C G Dm E7 Fmaj7 G7"})
                    drums = track(a, "DRUMS")["notes"]
                    snares = {n["start"] for n in drums if n["family"] == "snare"}
                    self.assertEqual(snares, set(range(1, 32, 2)))
                    if genre not in ("pop", "rap"):
                        kicks = {n["start"] for n in drums if n["family"] == "kick"}
                        self.assertEqual(kicks, set(range(32)), (genre, density, seed))
                    hats = sorted((n for n in drums if n["family"] in ("open_hat", "closed_hat")), key=lambda n: n["start"])
                    self.assertEqual(len(hats), len({n["start"] for n in hats}))
                    for n, following in zip(hats, hats[1:]):
                        if n["family"] == "open_hat":
                            self.assertLessEqual(n["start"]+n["duration"], following["start"]+1e-8)
                    for t in a["tracks"]:
                        self.assertGreater(max(n["start"]+n["duration"] for n in t["notes"]), 28)
                        for n in t["notes"]:
                            self.assertLessEqual(n["start"]+n["duration"], 32+1e-8)
                            if t["role"] in ("DRUMS", "NOISE"):
                                continue
                            chord = next(ch for ch in a["progression"] if ch["bar"]*4 <= n["start"] < (ch["bar"]+ch["bars"])*4)
                            self.assertIn(n["pitch"] % 12, chord["pitch_classes"])
                            self.assertLessEqual(n["start"]+n["duration"], (chord["bar"]+chord["bars"])*4+1e-8)
                            if t["role"] in ("BASS", "SUB_BASS"):
                                self.assertEqual(n["pitch"] % 12, chord["root"] % 12)
                                self.assertLessEqual(n["pitch"], 55)
                            if t["role"] == "LEAD":
                                self.assertTrue(60 <= n["pitch"] <= 83)
                        if t["role"] in ("BASS", "SUB_BASS", "LEAD", "PLUCK", "ARPEGGIO"):
                            notes = sorted(t["notes"], key=lambda n: n["start"])
                            for n, following in zip(notes, notes[1:]):
                                self.assertLessEqual(n["start"]+n["duration"], following["start"]+1e-8)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--seeds", type=int, default=1000)
    args = parser.parse_args()
    if args.audit:
        report = audit(args.seeds)
        for genre, result in report["genres"].items():
            assert result["arrangement"]["duplicate_sample_rate"] < .01, genre
            assert result["drums"]["duplicate_sample_rate"] < .01, genre
            assert result["lead"]["duplicate_sample_rate"] < .01, genre
            assert result["lead_rhythm"]["unique"] >= min(128, args.seeds), genre
            assert result["lead_pitch_contour"]["unique"] >= min(128, args.seeds), genre
    else:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
