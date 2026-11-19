"""Dry, procedural drum-machine starter kits. No samples, models or downloads.

These are 808/909-inspired building blocks, not circuit emulations or recordings
of those instruments. Edit GENRE_PROFILES, VARIANTS and the synthesis functions
to change the sound. Each family occupies four consecutive MIDI notes, 36..67.
"""
from __future__ import annotations

from array import array
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import struct
import sys
import tempfile
import wave

SAMPLE_RATE = 32000
FAMILIES = ("kick", "snare", "clap", "closed_hat", "open_hat", "ride", "shaker", "tom")

# A common neutral engine, with modest genre-oriented tuning/decay choices.
# "metal" blends metallic oscillators into the noise-based cymbals.
GENRE_PROFILES = {
    "trance":     dict(kick_hz=50, kick_decay=.24, kick_drop=120, snare_hz=185, hat_decay=.045, open_decay=.23, metal=.35, brightness=1.05, tom_hz=125),
    "hard_dance": dict(kick_hz=49, kick_decay=.29, kick_drop=155, snare_hz=195, hat_decay=.042, open_decay=.20, metal=.40, brightness=1.12, tom_hz=120),
    "hard_trance":dict(kick_hz=48, kick_decay=.30, kick_drop=145, snare_hz=190, hat_decay=.048, open_decay=.26, metal=.48, brightness=1.08, tom_hz=118),
    "house":      dict(kick_hz=52, kick_decay=.22, kick_drop=100, snare_hz=180, hat_decay=.052, open_decay=.25, metal=.38, brightness=.98, tom_hz=135),
    "hard_house": dict(kick_hz=53, kick_decay=.25, kick_drop=155, snare_hz=205, hat_decay=.038, open_decay=.19, metal=.45, brightness=1.15, tom_hz=140),
    "hardstyle":  dict(kick_hz=47, kick_decay=.38, kick_drop=180, snare_hz=200, hat_decay=.041, open_decay=.22, metal=.40, brightness=1.12, tom_hz=115),
    "pop":        dict(kick_hz=55, kick_decay=.20, kick_drop=95, snare_hz=195, hat_decay=.043, open_decay=.18, metal=.22, brightness=.95, tom_hz=145),
    "rap":        dict(kick_hz=44, kick_decay=.43, kick_drop=75, snare_hz=170, hat_decay=.030, open_decay=.16, metal=.70, brightness=1.03, tom_hz=105),
}
VARIANTS = (
    dict(label="main", tune=1.0, decay=1.0, brightness=1.0),
    dict(label="round", tune=2**(-1/12), decay=1.20, brightness=.80),
    dict(label="tight", tune=2**(1/12), decay=.76, brightness=1.20),
    dict(label="accent", tune=2**(2/12), decay=1.08, brightness=1.10),
)
PEAK_LEVELS = {"kick": .82, "snare": .64, "clap": .56, "closed_hat": .32,
               "open_hat": .38, "ride": .38, "shaker": .28, "tom": .66}
TAU = math.tau


def _finite(value, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    if low is not None and value < low or high is not None and value > high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return float(value)


def _seed(seed, *labels):
    return int.from_bytes(hashlib.sha256((str(seed)+":"+":".join(labels)).encode()).digest()[:8], "big")


def _finish(samples, peak):
    """Short endpoint tapers remove clicks; fixed family peaks preserve balance."""
    edge = min(round(SAMPLE_RATE*.003), len(samples)//2)
    attack = min(round(SAMPLE_RATE*.0004), len(samples)//2)
    for i in range(attack):
        samples[i] *= i/max(1, attack)
    for i in range(edge):
        samples[-1-i] *= i/max(1, edge)
    maximum = max((abs(x) for x in samples), default=0)
    if maximum > 0:
        gain = peak/maximum
        samples = array("f", (x*gain for x in samples))
    return samples


def synthesize(family, profile, variant=0, seed=42):
    """Return float32 samples and the actual sound parameters for one pad."""
    if family not in FAMILIES:
        raise ValueError("Unknown drum family.")
    if isinstance(variant, bool) or not isinstance(variant, int) or variant not in range(4):
        raise ValueError("Variant must be 0, 1, 2 or 3.")
    p = GENRE_PROFILES[profile] if isinstance(profile, str) else profile
    v = VARIANTS[variant]
    rng = random.Random(_seed(seed, family, str(variant)))
    tune, decay, bright = v["tune"], v["decay"], v["brightness"]*p["brightness"]
    params = {"variant": v["label"], "tuning_ratio": tune, "decay_multiplier": decay,
              "brightness_multiplier": bright, "synthesis": "procedural dry drum-machine starter"}
    samples = array("f")
    if family in ("kick", "tom"):
        base = p["kick_hz"]*tune if family == "kick" else p["tom_hz"]*tune
        envelope = p["kick_decay"]*decay if family == "kick" else .24*decay
        drop = p["kick_drop"] if family == "kick" else base*.65
        duration = min(2.8, max(.3, envelope*6.0))
        params.update(fundamental_hz=base, amplitude_decay_seconds=envelope, pitch_drop_hz=drop)
        phase = 0.0
        noise_lp = 0.0
        for i in range(round(duration*SAMPLE_RATE)):
            t = i/SAMPLE_RATE
            phase += TAU*(base + drop*math.exp(-t/(.017 if family == "kick" else .035)))/SAMPLE_RATE
            noise = rng.uniform(-1, 1)
            noise_lp += .32*(noise-noise_lp)
            body = math.sin(phase)*math.exp(-t/envelope)
            if family == "tom":
                body += .17*math.sin(phase*1.58)*math.exp(-t/(envelope*.55))
            click = (noise-noise_lp)*math.exp(-t/.006)*(.22 if family == "kick" else .12)*bright
            samples.append(body + click)
    elif family == "snare":
        base = p["snare_hz"]*tune
        envelope = .115*decay
        duration = .72*decay
        params.update(body_hz=base, noise_decay_seconds=envelope, highpass_hz=900*bright)
        lp = 0.0
        alpha = 1-math.exp(-TAU*900*bright/SAMPLE_RATE)
        for i in range(round(duration*SAMPLE_RATE)):
            t = i/SAMPLE_RATE
            noise = rng.uniform(-1,1)
            lp += alpha*(noise-lp)
            body = (.50*math.sin(TAU*base*t)+.22*math.sin(TAU*base*1.83*t))*math.exp(-t/.055/decay)
            samples.append(body + (noise-lp)*.75*math.exp(-t/envelope))
    elif family == "clap":
        duration = .65*decay
        params.update(bursts_ms=[0, 11, 23, 35], tail_decay_seconds=.085*decay, band_hz=[800*bright,6500])
        low = high = 0.0
        a_low = 1-math.exp(-TAU*800*bright/SAMPLE_RATE)
        a_high = 1-math.exp(-TAU*6500/SAMPLE_RATE)
        for i in range(round(duration*SAMPLE_RATE)):
            t = i/SAMPLE_RATE
            noise = rng.uniform(-1,1)
            low += a_low*(noise-low)
            high += a_high*(noise-high)
            env = sum(math.exp(-(t-at)/.0045) for at in (0,.011,.023) if t >= at)
            if t >= .035:
                env += .85*math.exp(-(t-.035)/(.085*decay))
            samples.append((high-low)*env)
    elif family in ("closed_hat", "open_hat", "ride", "shaker"):
        envelope = {"closed_hat":p["hat_decay"], "open_hat":p["open_decay"], "ride":.52, "shaker":.065}[family]*decay
        duration = min(3.5, max(.15,envelope*6))
        cutoff = {"closed_hat":5800,"open_hat":4800,"ride":1600,"shaker":3400}[family]*bright
        metallic = p["metal"] if family != "shaker" else 0.0
        if family == "ride":
            metallic = .80+.10*p["metal"]
        params.update(decay_seconds=envelope, highpass_hz=cutoff, metallic_mix=metallic)
        alpha = 1-math.exp(-TAU*min(cutoff, 12000)/SAMPLE_RATE)
        lp = 0.0
        # Inharmonic partials, rather than a pitched sample played higher.
        freqs = (2053, 2879, 3617, 4493, 5737, 6971) if family != "ride" else (577, 941, 1573, 2633, 4219, 6373)
        increments = [TAU*f*tune/SAMPLE_RATE for f in freqs]
        phases = [rng.random()*TAU for _ in freqs]
        for i in range(round(duration*SAMPLE_RATE)):
            t = i/SAMPLE_RATE
            noise = rng.uniform(-1,1)
            if metallic:
                metal = 0.0
                for j in range(6):
                    phases[j] += increments[j]
                    metal += math.sin(phases[j])
                noise = (1-metallic)*noise + metallic*metal/3
            lp += alpha*(noise-lp)
            env = math.exp(-t/envelope)
            if family == "shaker":
                env *= min(1.0, t/.004)
            samples.append((noise-lp)*env)
    samples = _finish(samples, PEAK_LEVELS[family])
    params["duration_seconds"] = len(samples)/SAMPLE_RATE
    return samples, params


def _write_wave(path, samples, sample_rate=SAMPLE_RATE):
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Output already exists: {path}")
    pcm = array("h", (round(max(-1.0,min(1.0,float(x)))*32767) for x in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def build_kit(genre, output_dir, seed=42):
    """Build 32 WAV files, a note-map manifest and a sequential pad audition.

    Refuses a nonempty destination. A staging directory avoids partial kits on
    validation/synthesis failure. All generation is deterministic for a seed.
    """
    if genre not in GENRE_PROFILES:
        raise ValueError(f"Choose one of: {', '.join(GENRE_PROFILES)}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("Seed must be an integer.")
    destination = Path(output_dir).resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise FileExistsError(f"Kit destination is not empty: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".raw-kit-", dir=destination.parent) as staging_name:
        staging = Path(staging_name)
        manifest = {"schema_version":1,"genre":genre,"seed":seed,"sample_rate":SAMPLE_RATE,
                    "channels":1,"bits_per_sample":16,
                    "description":"Dry procedural 808/909-inspired starter sounds; not hardware emulations or recorded commercial samples.",
                    "mapping":"Four consecutive notes per family, 36 through 67. FL Studio calls MIDI 36 C3; octave labels vary between DAWs.",
                    "variant_policy":"main, round, tight, accent; four sound alternatives, not four simultaneous drum layers",
                    "profile":GENRE_PROFILES[genre], "samples":[]}
        audition = array("f")
        for family_index, family in enumerate(FAMILIES):
            for variant in range(4):
                pitch = 36+family_index*4+variant
                samples, parameters = synthesize(family, genre, variant, _seed(seed, genre))
                filename = f"{pitch:02d}_{family}_{variant+1}_{VARIANTS[variant]['label']}.wav"
                _write_wave(staging/filename,samples)
                manifest["samples"].append({"family":family,"variant":variant,"midi_note":pitch,
                                            "filename":filename,"parameters":parameters})
                # Audition every pad once, keeping space after its complete tail.
                audition.extend(samples)
                audition.extend(array("f", [0.0])*round(.14*SAMPLE_RATE))
        _write_wave(staging/"audition_32.wav", audition)
        (staging/"kit.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        rows = ["RAW DRUM STARTER KIT", "", manifest["description"], "",
                "Note   Family       Variant", "---------------------------"]
        rows += [f"{s['midi_note']:>4}   {s['family']:<12} {s['variant']+1}: {s['parameters']['variant']}" for s in manifest["samples"]]
        rows += ["", "Load each WAV on its numbered MIDI note in your sampler.",
                 "Set closed/open hats to a shared choke group if the sampler supports it.",
                 "Plain MIDI's FLP export loads these 32 sounds into the existing Drum Buddy instrument.",
                 "Hardstyle here means raw source sounds; it is not a finished distorted hardstyle kick.",
                 "Edit drum_synth.py to change tuning, envelope decay and brightness."]
        (staging/"KIT.txt").write_text("\n".join(rows)+"\n",encoding="utf-8")
        destination.mkdir(exist_ok=True)
        for file in staging.iterdir():
            file.replace(destination/file.name)
    return {"folder":str(destination),"manifest":str(destination/"kit.json"),
            "audition":str(destination/"audition_32.wav"),"samples":manifest["samples"]}


def _read_wave(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate()) != (1,2,SAMPLE_RATE):
            raise ValueError(f"Expected mono 16-bit {SAMPLE_RATE} Hz kit sound: {path}")
        pcm = array("h",wav.readframes(wav.getnframes()))
    if sys.byteorder != "little":
        pcm.byteswap()
    return array("f",(x/32768 for x in pcm))


def render_preview(arrangement, kit_dir_or_manifest, output_path):
    """Render an eight- or 32-bar sketch using this kit and plain tones.

    Pitch/timing are representative; this does not render FL Studio plugins,
    their effects or automation. The automation-only NOISE trigger is omitted.
    Open hats are choked when the next closed/open hat triggers.
    """
    target = Path(output_path).resolve()
    if target.exists():
        raise FileExistsError(f"Output already exists: {target}")
    bpm = _finite(arrangement.get("bpm"),"BPM",30,300)
    if type(arrangement.get("bars")) is not int or arrangement["bars"] not in (8, 32):
        raise ValueError("Preview requires an eight- or 32-bar arrangement.")
    beats = arrangement["bars"] * 4
    seconds_per_beat = 60/bpm
    duration = beats*seconds_per_beat
    count = round(duration*SAMPLE_RATE)
    mix = array("f", [0.0])*count
    manifest_path = Path(kit_dir_or_manifest)
    if manifest_path.is_dir():
        manifest_path /= "kit.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    kit_dir = manifest_path.parent.resolve()
    sound_map = {}
    for sample in manifest["samples"]:
        source = (kit_dir/sample["filename"]).resolve()
        if source.parent != kit_dir:
            raise ValueError("Kit samples must be beside kit.json.")
        sound_map[sample["midi_note"]] = _read_wave(source)
    tracks = arrangement.get("tracks",[])
    levels = {"SUB_BASS":.085,"BASS":.08,"PAD":.023,"CHORDS":.032,
              "PLUCK":.050,"ARPEGGIO":.038,"LEAD":.065}
    for track in tracks:
        role = track["role"]
        if role == "NOISE":
            continue
        if role not in (*levels,"DRUMS"):
            raise ValueError(f"Unknown preview role: {role}")
        notes = track["notes"]
        hats = sorted(n["start"] for n in notes if role == "DRUMS" and 48 <= n["pitch"] <= 55)
        for note in notes:
            start = _finite(note["start"],"Note start",0,beats)
            length = _finite(note["duration"],"Note duration",.0001,beats)
            if start+length > beats+.00001:
                raise ValueError("A note extends past the arrangement boundary.")
            pitch = note["pitch"]
            if isinstance(pitch,bool) or not isinstance(pitch,int) or pitch not in range(128):
                raise ValueError("MIDI pitch must be an integer from 0 to 127.")
            velocity = _finite(note["velocity"],"Velocity",1,127)/127
            offset = round(start*seconds_per_beat*SAMPLE_RATE)
            if role == "DRUMS":
                if pitch not in sound_map:
                    raise ValueError(f"Drum note {pitch} is absent from the kit.")
                sound = sound_map[pitch]
                available = min(len(sound),count-offset)
                if 48 <= pitch <= 55:
                    following = next((x for x in hats if x > start+.000001),None)
                    if following is not None:
                        available = min(available,max(0,round((following-start)*seconds_per_beat*SAMPLE_RATE)))
                for j in range(available):
                    # Fade a choked tail for 2ms; no hard waveform cut.
                    taper = min(1.0,(available-j)/max(1,round(.002*SAMPLE_RATE)))
                    mix[offset+j] += sound[j]*velocity*.65*taper
                continue
            n = min(count-offset, max(1,round(length*seconds_per_beat*SAMPLE_RATE)))
            hz = 440*2**((pitch-69)/12)
            phase_step = TAU*hz/SAMPLE_RATE
            attack = .025 if role == "PAD" else .004
            release = .045 if role in ("PAD","CHORDS") else .015
            level = levels[role]*velocity
            for j in range(n):
                t = j/SAMPLE_RATE
                remaining = (n-j)/SAMPLE_RATE
                env = min(1.0,t/attack,remaining/release)
                if role in ("PLUCK","ARPEGGIO"):
                    env *= math.exp(-t/.14)
                elif role == "CHORDS":
                    env *= .65+.35*math.exp(-t/.35)
                phase = phase_step*j
                tone = math.sin(phase)
                if role != "SUB_BASS":
                    tone += .18*math.sin(phase*2)+.07*math.sin(phase*3)
                mix[offset+j] += tone*env*level
    peak = max((abs(x) for x in mix),default=0)
    # Only attenuate an overload; retain the sensible balance at ordinary levels.
    gain = min(1.0,.92/peak) if peak else 1.0
    if gain != 1:
        mix = array("f",(x*gain for x in mix))
    edge = min(round(.008*SAMPLE_RATE),count//2)
    for j in range(edge):
        mix[j] *= j/max(1,edge)
        mix[-1-j] *= j/max(1,edge)
    target.parent.mkdir(parents=True,exist_ok=True)
    _write_wave(target,mix)
    return {"path":str(target),"duration_seconds":count/SAMPLE_RATE,"peak":peak*gain,
            "gain":gain,"noise_omitted":True,"description":"Plain-tone sketch; FL Studio plugins, effects and automation are not rendered."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("genre",choices=GENRE_PROFILES)
    parser.add_argument("output",type=Path)
    parser.add_argument("--seed",type=int,default=42)
    args = parser.parse_args()
    result = build_kit(args.genre,args.output,args.seed)
    print(result["manifest"])


if __name__ == "__main__":
    main()
