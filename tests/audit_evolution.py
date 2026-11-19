"""Small deterministic development audit; perceived musical quality is subjective."""
from pathlib import Path
import json
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import generator as g
import evolution as e

elapsed, retained, examples, voices = [], {key: [] for key in "BCD"}, [], 0
for genre in g.GENRES:
    for seed in range(4):
        source = g.generate({"genre": genre, "seed": seed, "auto_chords": True,
                             "chord_creativity": .75, "density": .5})
        start = time.perf_counter()
        song = e.evolve_song(source, seed=1000+seed, amount=.7)
        g.midi_bytes(song)
        elapsed.append(time.perf_counter()-start)
        voices += sum(len(t["notes"]) for t in song["tracks"])
        for identity in "BCD":
            section = song["section_bank"][identity]["arrangement"]
            for role in ("LEAD", "PLUCK", "ARPEGGIO", "BASS", "SUB_BASS"):
                old = {round(n["start"]*96) for t in source["tracks"] if t["role"] == role for n in t["notes"]}
                new = {round(n["start"]*96) for t in section["tracks"] if t["role"] == role for n in t["notes"]}
                retained[identity].append(len(old & new)/len(old))
        if seed == 0:
            examples.append({"genre": genre,
                             "source_chords": len(source["progression"]),
                             "song_chords": len(song["progression"]),
                             "notes": sum(len(t["notes"]) for t in song["tracks"])})

report = {
    "songs": len(elapsed), "source_seeds_per_genre": [0, 1, 2, 3],
    "evolution_seeds_per_genre": [1000, 1001, 1002, 1003],
    "amount": .7, "source_chord_creativity": .75,
    "generation_and_midi_seconds": {"mean": round(statistics.mean(elapsed), 4),
                                     "max": round(max(elapsed), 4)},
    "notes": voices,
    "source_attack_retention": {identity: {"minimum": round(min(values), 4),
                                           "mean": round(statistics.mean(values), 4)}
                                for identity, values in retained.items()},
    "examples": examples,
    "limits": "Timing covers evolution and MIDI serialization, excluding disk, preview and FLP. Attack retention is measured on FL Studio's 96 PPQ grid, over five melodic/bass roles. This finite audit does not measure perceived musical quality or a universal duplicate probability."
}
(ROOT / "evolution_metrics.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
print(json.dumps(report, indent=2))
