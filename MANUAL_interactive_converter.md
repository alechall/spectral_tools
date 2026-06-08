# interactive_converter.py — Manual

Partiels JSON/SDIF to MusicXML converter with selectable pitch quantization,
rhythmic quantization, and output modes.

## Quick Start

```bash
# Fully interactive (TUI menus guide you through every option)
python3 interactive_converter.py

# Provide an input file — interactive prompts fill in the rest
python3 interactive_converter.py ./my_partials.json

# Fully non-interactive with CLI flags
python3 interactive_converter.py ./data -q quarter-tone -m harmonic -r 16th -o output.musicxml

# Use all defaults, no prompts
python3 interactive_converter.py input.json --no-interactive
```

## What It Does

Takes spectral analysis data exported from **Partiels** (as JSON) or raw SDIF
files, and converts them into notated MusicXML scores. The converter handles:

- Grouping simultaneous partials into chords or separating them into melodic
  lines
- Quantizing microtonal frequencies to semitone, quarter-tone, or eighth-tone
  resolution
- Quantizing rhythmic timing to a selectable grid
- Filtering partials by amplitude and frequency range
- Inferring subharmonic foundation tones for keyboard notation
- Producing clean MusicXML that imports directly into Dorico or MuseScore

## Dependencies

- Python 3.8+
- `questionary` (optional — provides rich TUI menus; falls back to plain
  `input()` prompts if not installed)

Install questionary:
```bash
pip install questionary
```

No other external packages are required — the converter uses only the Python
standard library for XML generation and file I/O.

## Input Formats

| Format | How to provide |
|--------|----------------|
| Single Partiels JSON file | Pass the `.json` file path |
| Directory of partial JSON files | Pass the directory path (reads all `Group 1_Partial *.json` files, or falls back to `*.json`) |
| SDIF file | Pass the `.sdif` file path |

## Output Formats

- **MusicXML** (`.musicxml`) — the primary output
- **SDIF** (`.sdif`) — optional re-export of processed spectral data

Both can be generated in a single run.

## Interactive Mode

When run without `--no-interactive`, the script walks you through a series of
menus:

1. **Input path** — file or directory
2. **Output path** — base name for output files
3. **Export format** — MusicXML, SDIF, or both
4. **Pitch quantization** — semitone / quarter-tone / eighth-tone
5. **Rhythmic quantization** — free / 16th / 32nd / 8th-triplet / 16th-triplet
6. **Output mode** — melodic (single line per partial) or harmonic (chords)
7. **Max partials per frame** — limits chord density
8. **Frame duration** — time window for grouping simultaneous events (seconds)
9. **Amplitude threshold** — filters quiet partials (0.0–1.0)
10. **Frequency range** — min/max Hz
11. **Tempo** — BPM (or auto-detect from note density)
12. **Score metadata** — title and composer
13. **Subharmonics** — infer keyboard foundation tones
14. **Semitone staff** — add a third staff showing semitone reduction

If `questionary` is installed, you get arrow-key-navigable menus with color
highlighting. Otherwise, plain text prompts.

## CLI Arguments

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `input` | — | *(prompt)* | Input JSON file or directory |
| `--output` | `-o` | *(prompt)* | Output file path |
| `--quantization` | `-q` | quarter-tone | `semitone`, `quarter-tone`, or `eighth-tone` |
| `--rhythm` | `-r` | 16th | `free`, `16th`, `32nd`, `8th-triplet`, `16th-triplet` |
| `--mode` | `-m` | harmonic | `melodic` or `harmonic` |
| `--max-partials` | — | 8 | Max simultaneous partials per frame |
| `--frame-duration` | — | 0.05 | Frame duration in seconds |
| `--min-amplitude` | — | 0.1 | Amplitude threshold (0.0–1.0) |
| `--min-frequency` | — | 80.0 | Low frequency cutoff (Hz) |
| `--max-frequency` | — | 4186.0 | High frequency cutoff (Hz, default = C8) |
| `--tempo` | — | 60 | Tempo in BPM |
| `--auto-tempo` | — | off | Infer tempo from note density (melodic mode) |
| `--title` | — | Partiels Analysis | Score title |
| `--composer` | — | *(empty)* | Composer name |
| `--subharmonics` | — | on | Enable subharmonic inference |
| `--no-subharmonics` | — | — | Disable subharmonic inference |
| `--semitone-staff` | — | off | Add a semitone-reduction staff |
| `--ensemble-only` | — | off | Output only the microtonal ensemble staff (no foundations/keyboards) |
| `--no-interactive` | — | off | Skip all prompts, use defaults |

## Key Concepts

### Pitch Quantization

- **Semitone** — standard 12-TET. Best for conventional notation or keyboard
  parts.
- **Quarter-tone** — 24-TET (Tartini-style accidentals). Good balance between
  spectral accuracy and readability.
- **Eighth-tone** — 48-TET (12.5-cent resolution). Closest to the original
  spectral frequencies; requires performers comfortable with microtonal
  notation.

### Rhythmic Quantization

- **Free** — snaps to the nearest 16th note while preserving proportional
  timing. Best for capturing the natural rhythm of spectral events.
- **16th** / **32nd** — strict grid quantization.
- **8th-triplet** / **16th-triplet** — triplet grids for compound-meter
  contexts.

The converter uses 768 divisions per quarter note internally, so triplet
durations are exact integers with no rounding drift.

### Output Modes

- **Harmonic** — all simultaneous partials are stacked as chords on a grand
  staff. Best for seeing the full spectral content at each moment.
- **Melodic** — partials are separated into individual melodic lines. Best for
  extracting playable melodies from spectral data.

### Subharmonics

When enabled, the converter infers fundamental/subharmonic tones below the
spectral partials — useful for generating keyboard foundation parts
(piano/vibraphone) that support the spectral harmony.

### Semitone Staff

Adds a third staff to the score that shows a semitone-quantized reduction of the
microtonal content. Useful when you want both the precise microtonal notation and
a "piano-friendly" approximation side by side.

## Typical Workflow

1. Export partials from **Partiels** as JSON
2. Run `python3 interactive_converter.py` and follow the prompts
3. Import the resulting `.musicxml` into Dorico / MuseScore
4. Use the melodic transformer (`melodic_transformer.py`) to develop the
   extracted melodies further

## Examples

```bash
# Quarter-tone harmonic score from a directory of partial files
python3 interactive_converter.py ./partiels_export/ -q quarter-tone -m harmonic -o score

# Eighth-tone melodic extraction with auto-tempo
python3 interactive_converter.py analysis.json -q eighth-tone -m melodic --auto-tempo -o melody

# Quick semitone reduction, no prompts
python3 interactive_converter.py input.json -q semitone --no-interactive -o quick_look
```
