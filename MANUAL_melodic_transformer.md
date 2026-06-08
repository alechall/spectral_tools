# melodic_transformer.py — Manual

MusicXML melody transformation tool. Reads a melodic line from a MusicXML file,
applies spectral/serial transforms, and writes new MusicXML output with optional
chord passages connecting each variant.

## Quick Start

```bash
# Fully interactive — prompts guide you through every choice
python3 melodic_transformer.py

# Provide an input file
python3 melodic_transformer.py my_melody.musicxml
```

There is no non-interactive batch mode — the script always presents an
interactive menu for choosing transforms and parameters. The input file path is
the only CLI argument.

## What It Does

Takes a monophonic or 2-voice melody in MusicXML and generates transformed
variants using 11 transformation techniques. Output can be arranged in four
modes:

- **Auto-generate** — progressive divergence: each variant builds on the
  previous one, drifting further from the original with smooth line elongation
- **Harmonize** — preserves the original melody verbatim, adds harmonic-average
  chords below each melodic fragment, and generates auto-transformed variants
  each with their own harmony staff (uses raw XML to preserve notation)
- **Chain** — applies transforms one after another in sequence on one staff
- **Multi-voice** — each variant on a separate staff in a single score

## Dependencies

- Python 3.8+
- `music21` — music analysis and MusicXML parsing
- `questionary` (optional — provides rich TUI menus; falls back to plain
  `input()` prompts if not installed)

Install:
```bash
pip install music21 questionary
```

## Input

Any MusicXML file containing a melody. The script reads the first Part and
extracts notes from it. Supports:

- Single-voice melodies (one staff, one voice)
- Two-voice melodies (one staff, two voices — e.g., a piano part with soprano
  and bass lines)
- Microtonal content (quarter-tones, eighth-tones) is preserved through all
  transforms

## Output

MusicXML files written to the same directory as the input, with auto-incremented
suffixes (`_auto_10v_01.musicxml`, `_harm_01.musicxml`, `_chain_01.musicxml`,
etc.). Previous outputs are never overwritten.

## The Transforms

| # | Transform | Code | What it does | Parameters |
|---|-----------|------|--------------|------------|
| 1 | **Retrograde** | R | Reverses pitch order; rhythms stay in place | — |
| 2 | **Inversion** | I | Mirrors intervals around a pivot pitch | Pivot pitch (default: first note) |
| 3 | **Retrograde Inversion** | RI | Inversion followed by retrograde | Pivot pitch |
| 4 | **Pitch Compression** | Comp | Compresses intervals toward the mean pitch | Factor 0–1 (e.g. 0.5 = half the intervals) |
| 5 | **Pitch Expansion** | Exp | Expands intervals away from the mean pitch | Factor >1 (e.g. 1.5 = wider leaps) |
| 6 | **Augmentation** | Aug | Multiplies all durations by a factor | Duration multiplier (e.g. 2.0) |
| 7 | **Diminution** | Dim | Divides all durations by a factor | Duration divisor (e.g. 2.0) |
| 8 | **Transposition** | T | Shifts all pitches by N semitones | Semitones (supports 0.5 for quarter-tone) |
| 9 | **Rotation** | Rot | Rotates the pitch sequence by N positions (rhythms stay) | Number of positions |
| 10 | **Permutation** | Perm | Splits the melody into segments and reorders them | Number of segments + new order |
| 11 | **Pitch Quantization** | Q | Snaps all pitches to the nearest note in a scale | Scale (12 options) + root |

Available scales for Pitch Quantization: Major, Natural minor, Harmonic minor,
Dorian, Phrygian, Lydian, Mixolydian, Whole-tone, Octatonic (H-W),
Octatonic (W-H), Chromatic, Quarter-tone chromatic.

All pitch transforms preserve microtonal information and clamp output to the
MusicXML-valid range (C0–B9) using octave folding.

## Modes

### Auto-Generate Mode

The main compositional mode for generating extended material. You choose:

1. **Number of variants** (e.g. 10)
2. **Rhythmic direction** — augmentation, diminution, both, or none
3. **Semitone doubles** — optionally add a 12-TET quantized copy of each variant
4. **Output format** — multi-staff or sequential chain

Each variant transforms the *previous* variant (not the original), so the melody
drifts progressively further from the source. The divergence follows a
square-root intensity curve:

- **Early variants** (intensity ~0.4): gentle transforms — small rotations,
  short transpositions
- **Middle variants** (intensity ~0.7): more aggressive — larger transpositions,
  pitch expansion, permutations
- **Late variants** (intensity ~1.0): maximum divergence — scale quantization,
  wide transpositions, full expansion

Each variant step combines 1–3 transforms:
- A **structural transform** (85% chance): rotation, retrograde, inversion, RI,
  permutation, or pitch quantization
- A **transposition** (always): 2–12 semitones, growing with intensity
- A **pitch expansion** (70%+ chance): factor 1.02–1.2

**Line elongation** kicks in at higher intensities. The target length grows as
`1.0 + intensity^3 x 3.0` (up to 4x the original at full intensity). Extra
segments are generated by applying random transforms and transpositions to the
base material, creating continuously evolving extensions.

Between each variant (in chain output), a **chord passage** is inserted: a
4-note summary chord derived from the variant's pitch content, followed by
interpolation chords that morph smoothly to the next variant's harmony.

### Harmonize Mode

Designed for adding harmonic accompaniment to an existing melody while
preserving the original notation exactly. Uses raw XML manipulation (no music21
roundtrip) to avoid any destructive changes to the original score.

You choose:

1. **Rest gap threshold** for splitting the melody into fragments
2. **Chord tones** (2, 3, or 4 — default 4)
3. **Number of variants** and **rhythmic direction** (same as auto-generate)

Output structure:
- **Part 1**: Original melody (copied verbatim from input XML)
- **Part 1h**: Harmonic average — held summary chords aligned under each
  melodic fragment
- **Parts 2–N**: Auto-generated variants, each paired with its own harmony
  staff

The harmonic average derives a chord for each fragment by analyzing both
consecutive intervals and intervals from root, averaging them, and stacking
the result symmetrically around the mean pitch.

Variant melody parts clone the original XML note elements (preserving all
duration, beam, stem, and articulation data) and only replace the pitch.
Elongation notes beyond the original length are added as new measures.

### Chain Mode

Manual transform chaining. You pick transforms one at a time from the full
menu, and each is applied to the result of the previous step. After each
transform you can continue or stop. The entire chain is output as one
continuous sequence on a single staff, with chord passages between stages.

### Multi-Voice Mode

Creates multiple transformed variants as separate staves in one score file.
You manually select each transform and its source (original or any previous
variant). The original melody is always included as the first staff.

For 2-voice input files, each voice is transformed independently with the same
settings, and the output preserves voice separation.

## Chord Passages

In Auto-generate and Chain modes, each variant is separated by a chord passage:

1. **Summary chord** (1 bar) — 4-note chord derived from the pitch content of
   the variant, voiced symmetrically around the mean pitch
2. **Interpolation chords** (2 bars in 3/2) — smooth morph from the current
   summary to the next variant's summary using pitch-space interpolation
3. **Time signature reset** — returns to the original time signature

## Range and Clamping

- All output is clamped to MusicXML-valid range (C0–B9) using octave folding
- In auto-generate mode, pitches that drift beyond 12 semitones past the
  original melody's range are folded back by octave

## Duration Quantization

Output durations are quantized to a whitelist of cleanly notatable values (whole
notes down to 32nd notes, including dotted and double-dotted variants, plus
triplet values). This prevents unreadable tuplet fragments in the score.

## Known Limitations

- **Rhythmic transforms (Augmentation/Diminution) in multi-staff output** can
  produce rendering issues in some notation software. Pitch-only transforms
  work reliably in all modes.
- **Harmonize mode** requires the input MusicXML to have clean quarter-tone
  alter values (multiples of 0.5). Non-standard alter values from Dorico
  exports may need cleaning — the `_clean_alters_for_music21()` utility
  function is provided for this purpose.

## Typical Workflows

### Generate extended material from a spectral melody
1. Extract a melody from spectral data using `interactive_converter.py`
2. Run `python3 melodic_transformer.py melody.musicxml`
3. Choose **Auto-generate** mode, 8 variants, multi-staff output
4. Import the resulting `_auto_8v_01.musicxml` into Dorico

### Harmonize a melody with spectral chords
1. Run `python3 melodic_transformer.py melody.musicxml`
2. Choose **Harmonize** mode, 6 variants
3. Output contains the original melody + harmony + 6 variant/harmony pairs

### Manual exploration
1. Choose **Chain** or **Multi-voice** mode
2. Apply transforms one at a time (e.g. Inversion → Rotation → Transposition)
3. Listen to each variant and choose the next transform based on the result

## File Naming

Output files are auto-named based on the input and mode:

```
input_auto_10v_01.musicxml    # Auto mode, 10 variants, run 01
input_auto_10v_02.musicxml    # Same settings, next run
input_harm_01.musicxml        # Harmonize mode, run 01
input_chain_01.musicxml       # Chain mode, run 01
input_multi_01.musicxml       # Multi-voice mode, run 01
```

The number at the end auto-increments so previous outputs are never overwritten.
