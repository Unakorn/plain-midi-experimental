# Plain MIDI

**EXPERIMENTAL — NEEDS TESTING — UNTESTED IN FL STUDIO**

Generic eight-bar musical building blocks, editable Python scripts, and raw synthesized drum kits. No AI training, model downloads, or external Python packages.

**Feel free to fork ;)**

This is an early experiment. Automated structural checks have passed; that does **not** establish musical quality, reliable DAW import, or production readiness. Listening tests and real FL Studio testing are still needed.

## What it makes

- Eight bars of 4/4, with one shared chord progression.
- Nine MIDI parts: drums, pad, pluck, chords, arpeggio, lead, bass, sub-bass, and an optional-use noise trigger.
- A 32-sound drum kit: four kicks, snares, claps, closed hats, open hats, rides, shakers, and toms.
- Separate recipes for trance, hard dance, hard trance, house, hard house, hardstyle, pop, and rap.
- Multitrack and per-part MIDI, exact note data in JSON, raw WAVs, and an optional plain-tone preview.

The sounds are dry procedural starters inspired by basic 808/909 synthesis ideas. They are **not** commercial drum samples, circuit emulations, or finished hardstyle kicks. The genre recipes are intentionally simple starting points.

## Start

Install Python 3.10 or newer. The Windows GUI needs a Python installation with Tkinter/Tcl-Tk support.

```sh
python app.py
```

On Windows you can also double-click **Launch Plain MIDI.cmd**.

Choose a genre, key and tempo, then **Generate to Desktop**. The public release saves a randomly named **`.mid` file** directly on your Desktop, with its WAV kit and individual parts in `Desktop/Plain MIDI Files/<matching name>/`. It does not require a private FL Studio template.

Generate chooses a fresh seed by default. **Regenerate** always makes a fresh idea. Enable **Repeat seed** when you want Generate to reproduce the displayed seed.

On Windows, hold **Drag MIDI** after generation and drag into your DAW, Desktop, or a folder. It offers one saved multitrack `.mid` containing all nine parts, using native Windows file dragging with copy-only behavior. The session-named source stays in the companion folder. Escape cancels. A receiving DAW controls track import; MIDI does not contain the instrument presets, WAVs, or FLP automation. Other platforms can import the saved MIDI directly; native drag-out is currently Windows-only.

The first kit needs a few seconds to synthesize. To prepare reusable kits for quicker exports:

```sh
python prepare_kits.py
```

The cache is checked against the synthesis source and sound profile. Changing the code invalidates it. The GUI preview renders only when you click **Play preview**. Windows GUI playback uses `winsound`; the command-line tools also run without the Windows GUI.

## Command line

```sh
python make.py --desktop --genre hard_trance --key F# --seed 17
python make.py --genre pop --key D --mode major --progression "1 5 6 4" --output ./ideas
python make.py --genre rap --key A --bpm 92 --no-preview --output ./ideas
python make.py --help
```

`--output` creates a new uniquely named subfolder. Existing ideas are not overwritten. MIDI carries notes and tempo; it does not install instruments or load drum WAVs into an arbitrary sampler.

## Controls and editing

Use 1, 2, 4, or 8 scale degrees, such as `1 6 3 7`, or explicit chords such as `Am F C G`. The chords divide the eight bars equally. Basic triads, suspended chords, sevenths, and several extensions are supported; `CHORDS` in `generator.py` lists them.

Bass and sub-bass follow the chord roots. Other pitched parts use chord tones with their own rhythms. Density controls how busy parts get; swing delays offbeat positions; variation adds alternate phrase endings and details. New seeds change rhythms and melodies even at zero variation. Keep selected parts while rerolling others. Kept melodic parts require unchanged harmony.

The same settings and seed reproduce a generated idea within one engine version. Exact notes are saved in `arrangement.json`; use its saved MIDI to retain any manually edited or previously kept parts. Loading a JSON file through `--config` reads settings and regenerates notes. Seeds from an older engine can produce different notes after an update, so keep saved MIDI for exact recall.

Two-bar rhythm cells and chord-tone contours form A / A-prime / B / A phrases. Roles and percussion families have independent seeded choices. The EDM kick/backbeat, sustained pad, block chords and sub foundation stay deliberately simple; bass, lead, pluck, arpeggio and percussion get fresh phrasing.

A deterministic audit of **1,000 seeds per genre (8,000 arrangements)** found no repeated complete arrangements, drum structures, lead rhythms, bass rhythms, or pluck structures at the default settings. Fingerprints ignore velocities and drum sound variants and quantize timing to sixteenths. Individual pitch contours and arpeggios sometimes repeat. This is a finite sample, not a guaranteed 1% repeat probability or a measure of perceived musical similarity. See [the complete measurements](validation/variety_v2_metrics.json). Reproduce with `python tests/test_variety_v2.py --audit` (writes `variety_v2_metrics.json` in the repository root).

| File | Change here |
|---|---|
| `recipes.json` | Genre tempos, progression defaults, beat anchors and candidate rhythms |
| `generator.py` | Rhythm cells, phrase plans, chords, melodic contours, MIDI writing |
| `drum_synth.py` | Drum synthesis, tuning, decay, brightness |
| `workflow.py` | Session saving, Desktop names, part locks, kit cache |
| `file_drag.py` | Native Windows copy-only file dragging |

## Drum notes

The normal export uses a **custom map**, not General MIDI:

| MIDI notes | Family |
|---|---|
| 36–39 | Kick |
| 40–43 | Snare |
| 44–47 | Clap |
| 48–51 | Closed hat |
| 52–55 | Open hat |
| 56–59 | Ride |
| 60–63 | Shaker |
| 64–67 | Tom |

Load the numbered WAVs on those notes in your sampler. MIDI note numbers are authoritative; octave labels differ between DAWs. A `gm/DRUMS.mid` alternative maps the rhythms to General MIDI percussion. The noise track is a held trigger intended for a noise instrument; mute or omit it when importing into a normal pitched instrument. It is omitted from the plain-tone audio preview.

## Experimental FLP adapter

The public repository **does not include the original private FL Studio template**, installed plugins, plugin presets, or a blank-template builder. Ordinary users can use MIDI/WAV generation immediately.

`flp_export.py` is a measured adapter for one specific prepared layout. It expects nine fixed instrument roles, 37 automation controls, a specific Drum Buddy wrapper layout, and a 96-PPQ FLP. It rejects incompatible files. **Setting an arbitrary FLP as the template will not make it work.** The required layout and checks are explicit in `ROLE_MAP`, `AUTOMATION_NAMES`, and `_validate_template` for developers who want to adapt it.

If you already possess a compatible local template and its required plugins, opt in with the `PLAIN_MIDI_TEMPLATE` environment variable. For example, in PowerShell:

```powershell
$env:PLAIN_MIDI_TEMPLATE = 'D:\MyMusic\compatible-template.flp'
python app.py
```

Then Desktop export produces an `.flp` instead of `.mid`. It writes the new 32 samples into the verified Drum Buddy state and disables its gates/automatic tuning for raw one-shot playback. Other instrument states, effects, and routing are retained. Samples use absolute paths: keep the companion folder in place. The repository does not supply or install Drum Buddy. Use `--no-flp` with normal CLI output to force MIDI-only generation.

This adapter has structural serialization tests in the original development environment. The private fixture is not published, and that integration is **not independently reproducible from this repository alone**. Playback and compatibility remain unverified.

## Tests and contributions

```sh
python -m unittest discover -s tests -v
```

Tests cover chord membership, root-following bass, exact eight-bar MIDI length, paired note events, deterministic generation, WAV file validity, output naming, and failure cleanup. They cannot determine whether a groove sounds good. See [CONTRIBUTING.md](CONTRIBUTING.md) for the DAW/listening checks still needed.

The drag/variety update passed 26 public test groups on Windows. Native tests exercise real Shell file data, Unicode filenames, format enumeration, and cleanup; the interactive drag loop is substituted in automated tests. A user confirmed Drag MIDI worked in a manual test on the development Windows PC; the specific destination was not recorded. Broader destination and DAW track-import compatibility still needs testing. The diversity audit above is separate from the normal test suite.

MIT licensed. Please report the genre, settings, seed, OS, Python version, DAW/version, and the smallest reproducible problem. Do not upload private projects, presets, account data, or commercial sample packs.
