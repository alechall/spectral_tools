# sdif_processor.py — Manual

SDIF spectral processor with kaleidoscope reflections, chord grouping, and
export to SDIF/MusicXML/MIDI.

## Quick Start

```bash
# Fully interactive
python3 sdif_processor.py

# Interactive with input file pre-selected
python3 sdif_processor.py my_analysis.sdif

# CLI batch mode
python3 sdif_processor.py input.sdif -g -k --kaleidoscope-mode nested_cycles \
    -o output.sdif -x output.musicxml

# Partiels JSON input
python3 sdif_processor.py ./partiels_export/

# MusicXML input — feed a melody or chord sequence into the kaleidoscope
python3 sdif_processor.py my_melody.musicxml
```

## What It Does

Reads spectral analysis data (SDIF files from SPEAR, Partiels JSON exports, or
MusicXML scores), groups partials into chords, applies spectral transformations
— most notably a **kaleidoscope** system that generates new harmonic material by
reflecting partials around each other — and exports the results to SDIF,
MusicXML, and MIDI.

The kaleidoscope is the core creative feature: it takes a small set of spectral
chords and generates extended harmonic sequences by systematically using each
partial as a reflection center, producing harmonically related but continuously
evolving material.

## Dependencies

- Python 3.8+
- `numpy`
- `music21` (optional — only needed for MusicXML input)
- `midiutil` (optional — only needed for MIDI export)

Install:
```bash
pip install numpy music21 midiutil
```

## Input Formats

| Format | How to provide |
|--------|----------------|
| SDIF file (from SPEAR) | Pass the `.sdif` file path — reads 1TRC frames |
| Single Partiels JSON file | Pass the `.json` file path |
| Directory of Partiels JSON files | Pass the directory path |
| MusicXML file | Pass the `.musicxml`, `.xml`, or `.mxl` file path — converts pitches to frequencies, reads dynamics as amplitudes. Requires `music21`. |

## Output Formats

| Format | Flag | Description |
|--------|------|-------------|
| **SDIF** | `-o` / `--output-sdif` | 1TRC format, re-importable into SPEAR. Includes attack/sustain/release envelopes for clean playback. |
| **MusicXML** | `-x` / `--output-xml` | Score with chords notated in 4/4 at treble clef. Supports eighth-tone (default) or semitone quantization. |
| **MIDI** | `--output-midi` | Single-track MIDI with per-partial pitch bend channels for microtonal accuracy. Auto-generated when SDIF output is specified. |

## Interactive Mode

When run without CLI transform flags (or with `-i`), the script enters a
step-by-step interactive mode:

1. **Input file** — prompts if not provided on command line
2. **Import summary** — shows frame count, average partials per frame, duration
3. **Chord grouping** — choose mode (simple / stability / transition), window
   size, and thresholds
4. **Transformation menu**:
   - Kaleidoscope reflections
   - Pitch shift (semitones)
   - Spectral stretch
   - Amplitude scale
   - Spectral morph (requires a second input file)
5. **Kaleidoscope options** (if selected):
   - Mode (auto / nested_cycles / temporal_evolution / sequential_evolution /
     interweave / morph)
   - Rotation duration
   - Cyclic return to original
   - Reflection mode (musical / linear)
   - Omit originals
   - Shuffle order
   - Psychoacoustic spacing
   - Scalar motion
6. **SDIF export** — path and chord sustain duration
7. **MusicXML export** — eighth-tone or semitone quantization, amplitude filter

## Chord Grouping Modes

Before applying transformations, raw spectral frames are grouped into chords:

| Mode | Description |
|------|-------------|
| **Simple** | Fixed time windows. Uses the middle frame of each window as the representative chord. |
| **Stability** | Analyzes partial persistence over time. Keeps partials present in at least 50% of frames within each window. Produces cleaner chords. |
| **Transition** | Detects chord boundaries by analyzing frequency change between consecutive frames. Best for capturing natural harmonic rhythm. |

Parameters:
- **Window size** — duration of each grouping window in seconds (simple/stability modes)
- **Frequency tolerance** — how close two frequencies must be to count as the same partial (stability mode)
- **Transition threshold** — percentage frequency change that triggers a new chord (transition mode)
- **Transition gap** — minimum time between chord boundaries (transition mode)

## Transformations

### Kaleidoscope (the main feature)

Generates new harmonic material by reflecting partials around each other. Each
partial becomes a "mirror" that the other partials are reflected through,
creating harmonically related variants.

**Modes:**

| Mode | Best for | Description |
|------|----------|-------------|
| **nested_cycles** | 3+ chords | The "true kaleidoscope." Each partial takes a turn as anchor while others are reflected around remaining partials. Produces the richest output. |
| **temporal_evolution** | 1 chord | Cycles through each partial as reflection center with crossfade envelopes. |
| **sequential_evolution** | 3+ chords | Applies full temporal evolution to each chord in sequence. |
| **interweave** | 2 chords | Alternates between two chords with reflections applied to each. |
| **morph** | 2 chords | Interpolates between two chords over 8 steps with reflections at each stage. |
| **auto** | any | Automatically selects the best mode based on chord count. |

**Reflection modes:**
- **Musical** — reflects in semitone (MIDI note) space. Produces more
  traditionally harmonic results.
- **Linear** — reflects in Hz space. Produces more spectrally pure but often
  wider-ranging results.

**Additional options:**
- **Cyclic return** — final chord returns to the original
- **Omit originals** — output contains only reflected partials, not the originals
- **Shuffle order** — randomizes which partial is used as anchor first
- **Upward rotation** — progressive Shepard-tone pitch rise across the sequence
- **Psychoacoustic spacing** — filters reflections using critical bandwidth
  theory so partials don't crowd each other
- **Scalar motion** — sorts reflected chords by spectral centroid for an
  ascending scalar effect; deduplicates similar chords
- **Max partials per chord** — limits chord density with intelligent selection
  (preserves octave equivalents, uses voice-leading optimization)

### Pitch Shift

Transposes all frequencies by a number of semitones (positive or negative).

### Spectral Stretch

Stretches or compresses the spacing between partials relative to a reference
point (lowest, mean, or center frequency). A stretch factor > 1 widens the
intervals; < 1 compresses them.

### Amplitude Scale

Multiplies all amplitudes by a constant factor.

### Spectral Morph

Interpolates between two spectral datasets. Requires a second input file.
Partials are matched by frequency proximity, then frequency, amplitude, and
phase are linearly interpolated by the morph factor (0.0 = 100% source 1,
1.0 = 100% source 2).

## CLI Arguments

### Input / Output
| Flag | Short | Description |
|------|-------|-------------|
| `input` | — | Input file (SDIF, JSON, or MusicXML), prompts if omitted |
| `--output-sdif` | `-o` | Output SDIF file path |
| `--output-xml` | `-x` | Output MusicXML file path |
| `--output-midi` | — | Output MIDI file path (auto-generated from SDIF name if not set) |
| `--interactive` | `-i` | Force interactive mode |

### Chord Grouping
| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--group-chords` | `-g` | off | Enable chord grouping |
| `--chord-mode` | — | simple | `simple`, `stability`, or `transition` |
| `--window-size` | `-w` | 0.1 | Grouping window in seconds |
| `--freq-tolerance` | — | 0.05 | Frequency tolerance (stability mode) |
| `--transition-threshold` | — | 0.05 | Change threshold (transition mode) |
| `--transition-gap` | — | 0.1 | Min gap between chords (transition mode) |
| `--chord-hold` | — | 0.5 | Chord sustain duration in SDIF output |

### Transforms
| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--pitch-shift` | `-p` | 0.0 | Semitones to shift |
| `--morph-with` | `-m` | — | Second SDIF file for morphing |
| `--morph-factor` | `-f` | 0.5 | Morph interpolation (0.0–1.0) |
| `--amplitude-scale` | `-a` | 1.0 | Amplitude multiplier |
| `--spectral-stretch` | `-s` | 1.0 | Interval stretch factor |
| `--stretch-reference` | — | lowest | `lowest`, `mean`, or `center` |
| `--min-amplitude` | — | 0.0001 | Amplitude filter threshold |
| `--semitone-quantize` | — | off | Semitone (vs eighth-tone) in MusicXML |

### Kaleidoscope
| Flag | Default | Description |
|------|---------|-------------|
| `--kaleidoscope` / `-k` | off | Enable kaleidoscope |
| `--kaleidoscope-mode` | auto | `auto`, `nested_cycles`, `temporal_evolution`, `sequential_evolution`, `interweave`, `morph`, `simple` |
| `--kaleidoscope-rotation-duration` | 0.5 | Seconds per rotation state |
| `--kaleidoscope-cyclic-return` | on | Return to original chord |
| `--kaleidoscope-no-cyclic-return` | — | Disable cyclic return |
| `--kaleidoscope-reflection-mode` | musical | `musical` or `linear` |
| `--kaleidoscope-omit-originals` | off | Only output reflections |
| `--kaleidoscope-shuffle-order` | off | Randomize anchor order |
| `--kaleidoscope-smooth-transitions` | on | Smooth morph transitions |
| `--kaleidoscope-sharp-cuts` | — | Sharp gaps between states |
| `--kaleidoscope-upward-rotation` | off | Shepard-tone rise |
| `--kaleidoscope-rise-semitones` | 24.0 | Rise amount (semitones) |
| `--kaleidoscope-fade-low` | 50.0 | Low fade frequency (Hz) |
| `--kaleidoscope-fade-high` | 2500.0 | High fade frequency (Hz) |
| `--psychoacoustic-spacing` | off | Critical bandwidth filtering |
| `--scalar-motion` | off | Sort by centroid for scalar effect |

## Typical Workflows

### Basic: Spectral analysis to notation
```bash
python3 sdif_processor.py analysis.sdif -g --chord-mode stability -x chords.musicxml
```

### Kaleidoscope: Generate extended harmonic material
```bash
python3 sdif_processor.py analysis.sdif -g -k \
    --kaleidoscope-mode nested_cycles \
    --psychoacoustic-spacing \
    -o kaleidoscope.sdif -x kaleidoscope.musicxml
```

### Morph between two analyses
```bash
python3 sdif_processor.py source.sdif -m target.sdif -f 0.5 -o morphed.sdif
```

### MusicXML input: Kaleidoscope from a melody
```bash
python3 sdif_processor.py melody.musicxml -g -k \
    --kaleidoscope-mode nested_cycles \
    -o kaleidoscope.sdif -x kaleidoscope_chords.musicxml
```

### Pitch-shifted MIDI output
```bash
python3 sdif_processor.py analysis.sdif -p 7 -o shifted.sdif --output-midi shifted.mid
```

## Pipeline with Other Tools

1. Analyze a sound in **SPEAR** or **Partiels** and export SDIF/JSON
2. Run `sdif_processor.py` to group chords and apply kaleidoscope transforms
3. Export to MusicXML for notation, SDIF for re-synthesis, and/or MIDI for
   playback
4. Feed the MusicXML melodies into `melodic_transformer.py` for further
   development
5. Use `interactive_converter.py` if you need more control over pitch/rhythm
   quantization from raw Partiels data
6. **Round-trip**: feed a transformed melody back into `sdif_processor.py` as
   MusicXML input to generate new kaleidoscope harmonies from your own melodic
   material
