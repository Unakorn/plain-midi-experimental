# Help test Plain MIDI

**Experimental, needs testing, untested in FL Studio. Feel free to fork ;)**

Run `python -m unittest discover -s tests -v` first. Keep fixes small enough to review and include the seed/settings that reproduce a musical or export problem.

Manual testing still needed:

1. Generate each of the eight genres in major and minor. Import the multitrack file and verify tempo, separate tracks, channel mapping, and exact eight-bar duration.
2. Assign the 32 WAVs to notes 36–67. Check all four variations of every family, hat choking, tails, clicks, and useful loudness balance.
3. Audition bass/sub harmony, lead phrasing, chord transitions and the loop seam. Listen at sparse and dense settings, with straight and swung timing.
4. Keep a bass or lead part and generate a variation. Confirm the kept notes stay unchanged. Changing harmony should require unlocking pitched parts.
5. Generate twice and verify two distinct Desktop filenames and companion folders. Confirm existing work was not overwritten.
   Verify the new seeds produce different rhythms/melodies; enable Repeat seed to reproduce a specific idea. Keep expected genre anchors in mind when comparing.
6. For developers with the exact compatible private FLP layout: open the exported copy, inspect all sample slots and effects, verify tempo during playback, listen through the full eight bars, and confirm save/reopen works. The public repo has no private fixture and cannot exercise this path on its own.
7. On Windows, drag the Drag MIDI button into Explorer, Desktop and your DAW. Check one complete MIDI file arrives, all nine parts import correctly, the original remains available, and Escape cancels cleanly. Report the destination application and whether it runs as administrator when reporting a rejected drop.
8. With Generate chords enabled, compare the same seed at 0%, 25%, 50%, 75% and 100% chord creativity. Check the increasing count, more frequent changes, extensions and common-tone pad/chord overlaps. Listen to bass/sub at every change. At higher levels some chords intentionally leave the base scale. Keep a lead and regenerate: its exact harmony should remain. Unkeep it before changing harmony settings. Also test manual sequences longer than eight chords and non-power-of-two counts such as five or seven.

Bug reports should include OS, Python and DAW versions, settings and seed, expected behavior, actual behavior, and a minimal MIDI or sanitized reproduction if useful. Avoid uploading commercial samples or private FLPs/presets.

The public default exports MIDI/WAVs. Keep this path functional without proprietary plugins or a prepared native project. CI success alone does not mean the music has been auditioned or the FLP adapter is ready for production.
