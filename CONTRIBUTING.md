# Help test Plain MIDI

**Experimental, needs testing, untested in FL Studio. Feel free to fork ;)**

Run `python -m unittest discover -s tests -v` first. Keep fixes small enough to review and include the seed/settings that reproduce a musical or export problem.

Manual testing still needed:

1. Generate each of the eight genres in major and minor. Import the multitrack file and verify tempo, separate tracks, channel mapping, and exact eight-bar duration.
2. Assign the 32 WAVs to notes 36–67. Check all four variations of every family, hat choking, tails, clicks, and useful loudness balance.
3. Audition bass/sub harmony, lead phrasing, chord transitions and the loop seam. Listen at sparse and dense settings, with straight and swung timing.
4. Keep a bass or lead part and generate a variation. Confirm the kept notes stay unchanged. Changing harmony should require unlocking pitched parts.
5. Generate twice and verify two distinct Desktop filenames and companion folders. Confirm existing work was not overwritten.
6. For developers with the exact compatible private FLP layout: open the exported copy, inspect all sample slots and effects, verify tempo during playback, listen through the full eight bars, and confirm save/reopen works. The public repo has no private fixture and cannot exercise this path on its own.

Bug reports should include OS, Python and DAW versions, settings and seed, expected behavior, actual behavior, and a minimal MIDI or sanitized reproduction if useful. Avoid uploading commercial samples or private FLPs/presets.

The public default exports MIDI/WAVs. Keep this path functional without proprietary plugins or a prepared native project. CI success alone does not mean the music has been auditioned or the FLP adapter is ready for production.
