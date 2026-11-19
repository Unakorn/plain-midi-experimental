"""Independent file/PCM checks for procedural kit and preview exports."""
from array import array
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import drum_synth as synth


class TestDrums(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="drum-tests-")
        cls.root = Path(cls.tmp.name)
        cls.result = synth.build_kit("house",cls.root/"house",42)
        cls.manifest = json.loads(Path(cls.result["manifest"]).read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_family_has_four_unique_valid_samples(self):
        items = self.manifest["samples"]
        self.assertEqual([x["midi_note"] for x in items],list(range(36,68)))
        for family in synth.FAMILIES:
            sounds = [x for x in items if x["family"] == family]
            self.assertEqual([x["variant"] for x in sounds],[0,1,2,3])
            hashes = set()
            for item in sounds:
                path = Path(self.result["folder"])/item["filename"]
                with wave.open(str(path),"rb") as wav:
                    self.assertEqual((wav.getnchannels(),wav.getsampwidth(),wav.getframerate()),(1,2,32000))
                    pcm = array("h",wav.readframes(wav.getnframes()))
                hashes.add(hashlib.sha256(pcm.tobytes()).hexdigest())
                self.assertGreater(len(pcm),3200)
                self.assertEqual(pcm[0],0)
                self.assertEqual(pcm[-1],0)
                self.assertGreater(max(abs(x) for x in pcm),1000)
                self.assertLess(max(abs(x) for x in pcm),32767)
                self.assertLess(abs(sum(pcm)/len(pcm)),200)
            self.assertEqual(len(hashes),4)

    def test_seed_repeatable_and_profiles_distinct(self):
        first,_ = synth.synthesize("snare","house",1,99)
        same,_ = synth.synthesize("snare","house",1,99)
        different,_ = synth.synthesize("snare","house",1,100)
        self.assertEqual(first.tobytes(),same.tobytes())
        self.assertNotEqual(first.tobytes(),different.tobytes())
        signatures = []
        for genre in synth.GENRE_PROFILES:
            sound,_ = synth.synthesize("kick",genre,0,42)
            signatures.append(hashlib.sha256(sound.tobytes()).hexdigest())
        self.assertEqual(len(set(signatures)),8)

    def test_refuses_overwrite(self):
        before = Path(self.result["manifest"]).read_bytes()
        with self.assertRaises(FileExistsError):
            synth.build_kit("rap",self.result["folder"])
        self.assertEqual(Path(self.result["manifest"]).read_bytes(),before)

    def test_preview_timing_clipping_and_noise_omission(self):
        arrangement = {"bars":8,"bpm":120,"tracks":[
            {"role":"DRUMS","notes":[{"start":0,"duration":.25,"pitch":36,"velocity":100},
                                       {"start":4,"duration":.25,"pitch":52,"velocity":90},
                                       {"start":4.5,"duration":.25,"pitch":48,"velocity":90}]},
            {"role":"BASS","notes":[{"start":0,"duration":4,"pitch":45,"velocity":85}]},
            {"role":"LEAD","notes":[{"start":8,"duration":1,"pitch":69,"velocity":90}]},
            {"role":"NOISE","notes":[{"start":0,"duration":32,"pitch":60,"velocity":127}]}]}
        output = self.root/"preview.wav"
        result = synth.render_preview(arrangement,self.result["manifest"],output)
        self.assertTrue(result["noise_omitted"])
        with wave.open(str(output),"rb") as wav:
            self.assertEqual(wav.getnframes(),32000*16)
            pcm = array("h",wav.readframes(wav.getnframes()))
        self.assertEqual(pcm[0],0)
        self.assertEqual(pcm[-1],0)
        self.assertLess(max(abs(x) for x in pcm),32767)
        self.assertTrue(all(x == 0 for x in pcm[32000*10:]))
        with self.assertRaises(FileExistsError):
            synth.render_preview(arrangement,self.result["manifest"],output)

    def test_invalid_input(self):
        for genre in ("unknown","EDM",""):
            with self.assertRaises(ValueError):
                synth.build_kit(genre,self.root/"bad")
        with self.assertRaises(ValueError):
            synth.render_preview({"bars":8,"bpm":float("nan")},self.result["manifest"],self.root/"bad.wav")


if __name__ == "__main__":
    unittest.main()
