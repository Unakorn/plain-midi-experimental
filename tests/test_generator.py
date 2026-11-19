"""Independent MIDI/ensemble invariants for the exported musical artifact."""
from pathlib import Path
import json
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import generator as g


def parse_smf(data):
    assert data[:4] == b"MThd"
    length,kind,count,ppq = struct.unpack_from(">IHHH",data,4)
    assert length == 6 and kind == 1 and ppq == 480
    pos = 14
    tracks = []
    for _ in range(count):
        assert data[pos:pos+4] == b"MTrk"
        size, = struct.unpack_from(">I",data,pos+4)
        buf = data[pos+8:pos+8+size]
        pos += 8+size
        i = 0
        now = 0
        events = []
        def vlq():
            nonlocal i
            n = 0
            for _ in range(4):
                value = buf[i]
                i += 1
                n = (n << 7) | (value & 127)
                if value < 128:
                    return n
            raise AssertionError("Oversized VLQ")
        while i < len(buf):
            now += vlq()
            status = buf[i]
            i += 1
            if status == 255:
                meta = buf[i]
                i += 1
                length = vlq()
                payload = buf[i:i+length]
                i += length
                events.append((now,"meta",meta,payload))
            else:
                assert status & 240 in (128,144),f"Unexpected/program-change MIDI event {status:x}"
                pitch,velocity = buf[i:i+2]
                i += 2
                assert pitch < 128 and velocity < 128
                events.append((now,"note",status,pitch,velocity))
        assert events[-1] == (32*480,"meta",47,b"")
        tracks.append(events)
    assert pos == len(data)
    return tracks


class GeneratorTests(unittest.TestCase):
    def assert_music(self,a):
        self.assertEqual(len(a["tracks"]),9)
        for track in a["tracks"]:
            self.assertTrue(track["notes"])
            self.assertGreater(max(n["start"]+n["duration"] for n in track["notes"]),28)
            same_pitch = {}
            for note in track["notes"]:
                self.assertGreater(note["duration"],0)
                self.assertGreaterEqual(note["start"],0)
                self.assertLessEqual(note["start"]+note["duration"],32+1e-8)
                self.assertGreaterEqual(note["start"]+1e-8,same_pitch.get(note["pitch"],0))
                same_pitch[note["pitch"]] = note["start"]+note["duration"]
                if track["role"] not in ("DRUMS","NOISE"):
                    chord = next(ch for ch in a["progression"] if ch["bar"]*4 <= note["start"] < (ch["bar"]+ch["bars"])*4)
                    self.assertIn(note["pitch"]%12,chord["pitch_classes"])
                    self.assertLessEqual(note["start"]+note["duration"],(chord["bar"]+chord["bars"])*4+1e-8)
                    if track["role"] in ("BASS","SUB_BASS"):
                        self.assertEqual(note["pitch"]%12,chord["root"]%12)
        drum = next(t for t in a["tracks"] if t["role"]=="DRUMS")
        hats = {}
        kicks = set()
        for n in drum["notes"]:
            self.assertGreaterEqual(n["pitch"],36)
            self.assertLess(n["pitch"],68)
            if 48 <= n["pitch"] < 56:
                kind = "closed" if n["pitch"]<52 else "open"
                previous = hats.setdefault(n["start"],kind)
                self.assertEqual(previous,kind,"Simultaneous open and closed hats")
            if n["pitch"] < 40:
                self.assertNotIn(n["start"],kicks)
                kicks.add(n["start"])

    def test_eight_requested_genres(self):
        self.assertEqual(set(g.GENRES),{"trance","hard_dance","hard_trance","house","hard_house","hardstyle","pop","rap"})
        fingerprints=set()
        for genre in g.GENRES:
            a=g.generate({"genre":genre})
            self.assert_music(a)
            fingerprints.add(json.dumps([(t["role"],t["notes"]) for t in a["tracks"]],sort_keys=True))
        self.assertEqual(len(fingerprints),8)

    def test_all_keys_modes_genres(self):
        for genre in g.GENRES:
            for key in ("C","C#","D","Eb","E","F","F#","G","Ab","A","Bb","B"):
                for mode in g.SCALES:
                    with self.subTest(genre=genre,key=key,mode=mode):
                        self.assert_music(g.generate({"genre":genre,"key":key,"mode":mode}))

    def test_extremes_and_symbols(self):
        for genre in g.GENRES:
            for density in (.1,1):
                for variation in (0,1):
                    a=g.generate({"genre":genre,"density":density,"variation":variation,"swing":.6,
                                  "chord_style":"rhythm","lead_style":"pluck",
                                  "progression":"Am9 Fmaj7 Cadd9 G7 Dsus2 Esus4 Bm7b5 E7"})
                    self.assert_music(a)

    def test_deterministic_and_independent(self):
        original=g.generate({"seed":98})
        self.assertEqual(original,g.generate({"seed":98}))
        changed=g.generate({"seed":98,"lead_style":"pluck"})
        self.assertNotEqual(original["tracks"][5]["notes"],changed["tracks"][5]["notes"])
        for a,b in zip(original["tracks"],changed["tracks"]):
            if a["role"]!="LEAD": self.assertEqual(a,b)
        changed=g.generate({"seed":98,"drum_variants":False})
        for a,b in zip(original["tracks"],changed["tracks"]):
            if a["role"]!="DRUMS": self.assertEqual(a,b)
        self.assertTrue(all(n["pitch"] in g.DRUM_MAP.values() for n in changed["tracks"][0]["notes"]))

    def test_independent_smf_parser(self):
        for genre in g.GENRES:
            a=g.generate({"genre":genre,"variation":1})
            parsed=parse_smf(g.midi_bytes(a))
            self.assertEqual(len(parsed),10)
            for track in parsed:
                active=set()
                for e in track:
                    if e[1]!="note": continue
                    at,_,status,pitch,velocity=e
                    ident=(status&15,pitch)
                    if status&240==144 and velocity:
                        self.assertNotIn(ident,active,"Retrigger before note-off")
                        active.add(ident)
                    else:
                        self.assertIn(ident,active,"Unmatched note-off")
                        active.remove(ident)
                self.assertFalse(active,"Stuck notes at end")
            gm=parse_smf(g.midi_bytes(a,[a["tracks"][0]],gm_drums=True))
            pitches={e[3] for e in gm[1] if e[1]=="note"}
            self.assertTrue(pitches.issubset({36,38,39,42,46,51,70,45,47,48,50}))

    def test_invalid_input(self):
        bad=[{"genre":"fake"},{"bpm":0},{"bpm":float("nan")},{"seed":True},
             {"key":"H"},{"mode":"fake"},{"progression":" ".join(["Am"]*33)},
             {"progression":"1 2 3 8"},{"progression":"An F"},
             {"swing":.7},{"density":0},{"drum_variants":"false"},
             {"muted_roles":["INVALID"]},{"typo":1}]
        for config in bad:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):g.generate(config)

    def test_bundle_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/"bundle"
            a=g.generate()
            result=g.write_bundle(a,out)
            self.assertEqual(Path(result["folder"]),out)
            self.assertEqual(len(list((out/"parts").glob("*.mid"))),9)
            for path in out.rglob("*.mid"):
                parse_smf(path.read_bytes())
            self.assertEqual(json.loads((out/"arrangement.json").read_text()),a)
            original=(out/"arrangement.mid").read_bytes()
            with self.assertRaises(FileExistsError):g.write_bundle(g.generate({"seed":9}),out)
            self.assertEqual((out/"arrangement.mid").read_bytes(),original)


if __name__=="__main__":
    unittest.main(verbosity=2)
