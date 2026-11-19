# Plain MIDI

**EXPERIMENTAL — NEEDS TESTING — UNTESTED IN FL STUDIO**

Eight-bar ideas that develop into reorderable 32-bar songs, editable Python scripts, and raw synthesized drum kits. No AI training, model downloads, or external Python packages.

**Feel free to fork ;)**

This is an early experiment. Automated structural checks have passed; that does **not** establish musical quality, reliable DAW import, or production readiness. Listening tests and real FL Studio testing are still needed.

## What it makes

- Eight bars of 4/4, with one shared chord progression; optionally evolve into four related sections across 32 bars.
- Nine MIDI parts: drums, pad, pluck, chords, arpeggio, lead, bass, sub-bass, and an optional-use noise trigger.
- A 32-sound drum kit: four kicks, snares, claps, closed hats, open hats, rides, shakers, and toms.
- Separate recipes for trance, hard dance, hard trance, house, hard house, hardstyle, pop, and rap.
- A separate chord-creativity control for fresh shared progressions, more changes, richer chords, and controlled shared-note overlaps.
- Multitrack and per-part MIDI, exact note data in JSON, raw WAVs, and an optional plain-tone preview.

The sounds are dry procedural starters inspired by basic 808/909 synthesis ideas. They are **not** commercial drum samples, circuit emulations, or finished hardstyle kicks. The genre recipes are intentionally simple starting points.

## Start

Install Python 3.10 or newer. The Windows GUI needs a Python installation with Tkinter/Tcl-Tk support.

```sh
python app.py
```

On Windows you can also double-click **Launch Plain MIDI.cmd**.

Choose a genre, key and tempo, then **Generate 8 bars**. The public release saves a randomly named **`.mid` file** directly on your Desktop, with its WAV kit and individual parts in `Desktop/Plain MIDI Files/<matching name>/`. It does not require a private FL Studio template.

Generate chooses a fresh seed by default. **New 8-bar idea** always makes a fresh idea. Enable **Repeat seed** when you want Generate to reproduce the displayed seed.

**Generate chords** is on by default in the GUI. Raise **Chord creativity** to explore more chord changes across the same eight bars, richer extensions, and overlapping shared tones in the pad/chord parts. All pitched parts follow that new timeline. Turn Generate chords off to enter your own **1–32-chord** sequence instead.

On Windows, hold **Drag MIDI** after generation and drag into your DAW, Desktop, or a folder. It offers one saved multitrack `.mid` containing all nine parts, using native Windows file dragging with copy-only behavior. The session-named source stays in the companion folder. Escape cancels. A receiving DAW controls track import; MIDI does not contain the instrument presets, WAVs, or FLP automation. Other platforms can import the saved MIDI directly; native drag-out is currently Windows-only.

The first kit needs a few seconds to synthesize. To prepare reusable kits for quicker exports:

```sh
python prepare_kits.py
```

The cache is checked against the synthesis source and sound profile. Changing the code invalidates it. The GUI preview renders only when you click **Play preview**. Windows GUI playback uses `winsound`; the command-line tools also run without the Windows GUI.

## Evolve and rearrange a song

1. **Generate 8 bars** until you have an idea you like.
2. Set **Evolution**, then click **Evolve to 32 bars**. A keeps your original **Theme** exactly; B adds a **Lift**, C provides **Contrast**, and D makes a **Return** to the theme's harmony.
3. Click **Shuffle sections**, or choose A/B/C/D once each in the four Order boxes and click **Apply order**. Each action saves a new randomly named project. Section notes and chords move together; shuffling does not reroll them.

Evolution changes chord choices, melodic details, rhythmic activity and phrase endings while working from your saved theme's actual notes. Bass/sub follow the new roots and melodic parts follow each section's harmony. At 0%, all four sections repeat the original exactly. Higher values create more development; they are not a guarantee that every musical dimension changes monotonically. Even a manually entered source progression can be developed in B/C. A always retains the original. Keeping any pitched part freezes the source harmony in every section and repeats that part's exact notes. Keeping drums alone permits new harmony.

The upper genre/key/chord controls apply when generating a new eight-bar idea. **Re-evolve song** uses the saved A theme, the current Evolution amount and current Keep choices to create another B/C/D set. It resets the order to ABCD. **Repeat seed** makes that evolution reproducible from the same source and settings. Shuffle always chooses a different order, independently of Repeat seed.

**Open saved idea** restores `arrangement.json` from a companion folder, including all section notes and the saved order. Generation from a 32-bar song returns to eight bars and uses A as the source for kept parts. The saved JSON also includes a section bank, and `sections/A.mid` through `D.mid` let you arrange the individual eight-bar sections in your DAW. Drag MIDI offers the full current 8- or 32-bar arrangement.

Section swaps preserve each section exactly, including its existing ending/fill. They do not compose a fresh transition for every possible neighbor. The main MIDI has section markers; note tails stay inside their section boundaries. This is a 32-bar song sketch, ready for further arrangement and listening.

## Command line

```sh
python make.py --desktop --genre hard_trance --key F# --seed 17
python make.py --genre pop --key D --mode major --progression "1 5 6 4" --output ./ideas
python make.py --genre rap --key A --bpm 92 --no-preview --output ./ideas
python make.py --genre trance --auto-chords --chord-creativity .85 --seed 17 --no-preview --output ./ideas
python make.py --genre trance --auto-chords --song --evolution .7 --order ACBD --seed 17 --no-preview --output ./ideas
python make.py --idea "saved/arrangement.json" --song --evolution .6 --seed 21 --no-preview --output ./ideas
python make.py --idea "saved-song/arrangement.json" --shuffle --seed 8 --no-preview --output ./ideas
python make.py --help
```

`--idea` preserves exact saved notes/settings; `--config` reads settings and regenerates. A loaded 32-bar song stays intact even with `--song`; use `--order ACBD` or `--shuffle` to rearrange it. These two ordering flags are mutually exclusive. `--evolution` accepts 0–1 and applies when expanding eight bars.

`--output` creates a new uniquely named subfolder. Existing ideas are not overwritten. MIDI carries notes and tempo; it does not install instruments or load drum WAVs into an arbitrary sampler.

## Controls and editing

**Generated harmony:** Chord creativity controls the number of changes, extensions, harmonic movement and shared-note overlaps. The sequence follows a tonal arc with a resolution. At high creativity, sparse borrowed chords and resolving secondary dominants add color; some notes may fall outside the selected base scale. All pitched parts use the actual chord tones. Bass/sub follow the current chord root. Only pad/chord voices shared by neighboring chords can sustain across changes; low notes and melodic lines are clipped at the boundaries.

| Chord creativity | Chords across eight bars |
|---|---|
| 0% | 4 |
| 25% | 6–8 |
| 50% | 9–12 |
| 75% | 15–20 |
| 100% | 24–32 |

The exact count depends on the seed. Raising creativity for the same seed refines its timing and never reduces its chord count. The eight-bar view shows the actual count, all chord names, and change markers within each bar. The 32-bar view shows section labels and boundaries.

**Manual harmony:** turn Generate chords off and enter any 1–32 scale degrees, such as `1 6 3 7`, or explicit chords such as `Am F C G`. The chords divide the eight bars as evenly as the shared MIDI/FLP timing grid permits. Basic triads, suspended chords, sevenths, and several extensions are supported; `CHORDS` in `harmony.py` lists them. The manual field is ignored in generated mode. Chord creativity applies to generated mode; manual harmony is kept exact. CLI calls remain manual by default for compatibility; add `--auto-chords` for automatic harmony.

Scale-degree values run from **1–7**. The chord count is the number of entries you type: an eight-chord sequence contains eight entries; entering `8` as a degree is invalid.

Density controls how busy parts get; swing delays offbeat positions; phrase variation adds alternate rhythmic endings and details. These are separate from chord creativity. New seeds change rhythms and melodies even at zero phrase variation. Keeping any melodic part also keeps the exact generated chord timeline, so newly generated parts still fit. Uncheck kept melodic parts before changing genre, key, scale, chord mode or chord creativity. Keeping drums alone allows new harmony.

The same settings and seed reproduce a generated idea within one engine version. Exact notes are saved in `arrangement.json`; use Open saved idea or `--idea` to retain previously kept parts and validated saved notes. Editing a 32-bar JSON requires keeping its section bank and flattened notes consistent. Loading a JSON file through `--config` reads settings and regenerates notes. Seeds from an older engine can produce different notes after an update, so keep saved MIDI for exact recall.

Two-bar rhythm cells and chord-tone contours form A / A-prime / B / A phrases, adapted to the shared harmonic timeline, including changes within a bar. Roles and percussion families have independent seeded choices. The EDM kick/backbeat stays recognizable; bass, lead, pluck, arpeggio and percussion get fresh phrasing.

A deterministic rhythm audit of **1,000 seeds per genre (8,000 arrangements)** with manual four-chord harmony found no repeated complete arrangements, drum structures, lead rhythms, bass rhythms, or pluck structures. Fingerprints ignore velocities and drum sound variants and quantize timing to sixteenths. Individual pitch contours and arpeggios sometimes repeat. This is a finite sample, not a guaranteed 1% repeat probability or a measure of perceived musical similarity. See [the complete measurements](validation/variety_v2_metrics.json). Reproduce with `python tests/test_variety_v2.py --audit` (writes `variety_v2_metrics.json` in the repository root).

The new harmony audit covers **4,000 progressions**: 100 seeds per genre at five creativity levels. At each tested nonzero level, every genre produced 100 distinct timed progressions; the small four-chord bank at 0% intentionally repeats. See [chord audit and examples](validation/harmony_metrics.json) and [renderer timings/overlaps](validation/chord_renderer_metrics.json). Run `python tests/test_harmony.py --audit` to reproduce the chord audit; it writes `harmony_metrics.json` in the repository root.

| File | Change here |
|---|---|
| `recipes.json` | Genre tempos, progression defaults, beat anchors and candidate rhythms |
| `generator.py` | Rhythm cells, phrase plans, chords, melodic contours, MIDI writing |
| `evolution.py` | Develop the saved theme, retain sections, reorder and validate full songs |
| `harmony.py` | Creative progressions, chord vocabulary and harmonic timing |
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

For a 32-bar song, the adapter writes nine full-length native patterns, stretches the 46 playlist placements to 32 bars and stretches the 37 existing calibration automation ramps across that length. Their values and links stay intact; these are not newly composed section automation. Open that song directly in FL Studio. The separate older arranger expects eight-bar sources and does not preserve a 32-bar section arrangement.

This adapter has structural serialization tests in the original development environment. The private fixture is not published, and that integration is **not independently reproducible from this repository alone**. Playback and compatibility remain unverified.

## Tests and contributions

```sh
python -m unittest discover -s tests -v
```

Tests cover chord membership, root-following bass, exact eight- and 32-bar MIDI length, paired note events, deterministic generation, WAV file validity, output naming, and failure cleanup. They cannot determine whether a groove sounds good. See [CONTRIBUTING.md](CONTRIBUTING.md) for the DAW/listening checks still needed.

The drag/variety update passed 26 public test groups on Windows. Native tests exercise real Shell file data, Unicode filenames, format enumeration, and cleanup; the interactive drag loop is substituted in automated tests. A user confirmed Drag MIDI worked in a manual test on the development Windows PC; the specific destination was not recorded. Broader destination and DAW track-import compatibility still needs testing. The diversity audit above is separate from the normal test suite.

The chord-creativity update passes **40 public test groups**. They include all manual chord counts, generated harmony, chromatic-chord metadata, exact timeline retention with kept parts, common-tone crossings, MIDI note pairing, and shared 96/480-PPQ timing. Independent review also checked 400 mixed arrangements after actual native-note conversion. These checks do not establish whether the new progressions sound good with your chosen patches; listening and DAW testing remain necessary.

The song-evolution update passes **57 public tests**, including all 24 section orders, exact saved-theme retention, kept-part rules, saved JSON validation, full-length MIDI/preview output and CLI preservation. Separate native integration checks pass for eight- and 32-bar projects. Independent checks covered 160 evolved/shuffled songs and 186,134 pitched notes. GUI integration exercised real file publication, reopening, ordering and the MIDI drag path; actual DAW playback of the new songs remains untested.

A small 32-song benchmark averaged **0.215 seconds** for evolution plus MIDI serialization on the development machine (maximum 0.299 seconds). This excludes disk writing, kit preparation, FLP export and audio rendering. At 70% Evolution, B/D retained all source attack positions in the five measured melodic/bass roles, while C retained 90% on average. See [measurements and limitations](validation/evolution_metrics.json); reproduce with `python tests/audit_evolution.py`. Retained attacks do not establish subjective musical quality.

MIT licensed. Please report the genre, settings, seed, OS, Python version, DAW/version, and the smallest reproducible problem. Do not upload private projects, presets, account data, or commercial sample packs.
