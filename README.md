  # Spectral Tools

Three Python scripts for working with spectral analysis data in a compositional
context. They bridge the gap between spectral analysis applications (Partiels,
SPEAR) and notation software (Dorico, MuseScore), providing the kind of
spectral processing and transformation pipeline that was previously only
available through IRCAM's AudioSculpt — which is no longer maintained or
available.

## The Tools

### sdif_processor.py
**Spectral processor with kaleidoscope reflections.**
Reads SDIF files (from SPEAR or Partiels), groups partials into chords, and
applies spectral transformations — most notably a *kaleidoscope* system that
generates extended harmonic material by reflecting partials around each other.
Exports to SDIF, MusicXML, and MIDI.

### interactive_converter.py
**Partiels/SDIF to MusicXML converter.**
Takes spectral analysis data and converts it into clean, import-ready MusicXML
scores with selectable pitch quantization (semitone, quarter-tone, eighth-tone),
rhythmic grids, harmonic or melodic output modes, and optional subharmonic
foundation tone inference.

### melodic_transformer.py
**MusicXML melodic line transformer.**
Reads a melody from MusicXML and generates transformed variants using 11
serial/spectral techniques (retrograde, inversion, transposition, rotation,
permutation, pitch quantization, etc.). Includes an auto-generate mode with
progressive divergence and line elongation, and a harmonize mode that adds
spectral chord accompaniment below the melody.

## What This Replaces

These tools were built to fill the gap left by IRCAM's **AudioSculpt**, which
provided a GUI-based spectral processing environment for composers working with
spectral analysis data. AudioSculpt is no longer maintained, and its core
functionality — reading spectral data, grouping partials into chords,
transforming spectral material, and exporting to notation — has no direct
replacement.

**Partiels** is an excellent modern spectral analysis
application, but it focuses on analysis and visualization rather than
compositional transformation. These scripts extend Partiels into a full
compositional workflow.

### Getting harmonic data out of Partiels

By default, Partiels tracks a single partial (melodic analysis). To export the
multi-partial data these tools work with, use the **multiple partials export
template** in Partiels — this tracks the N loudest partials per frame
simultaneously, producing the harmonic/chordal structures that `sdif_processor.py`
and `interactive_converter.py` expect. Without this, you only get a single
melodic line per analysis.

Partiels exports as **JSON** — a folder of individual partial files (one per
tracked partial). To use it, simply drag the exported folder into the terminal
when prompted for an input path. **SPEAR** exports as **SDIF** (a single binary
file in 1TRC format). Both formats are supported as input.

| AudioSculpt feature | Replacement |
|---------------------|-------------|
| Spectral analysis display | **Partiels** (analysis + visualization) |
| Chord grouping from partials | **sdif_processor.py** (simple, stability, and transition grouping) |
| Spectral transformations (morph, stretch, shift) | **sdif_processor.py** (morph, stretch, pitch shift, amplitude scale) |
| Extended harmonic generation | **sdif_processor.py** kaleidoscope (reflection-based harmonic generation) |
| Export to notation | **interactive_converter.py** (MusicXML with microtonal quantization) |
| SDIF round-trip (analyze, transform, re-synthesize) | **sdif_processor.py** (SDIF in/out, maintains 1TRC format for SPEAR) |
| Melodic extraction and development | **interactive_converter.py** (melodic mode) + **melodic_transformer.py** |

### What's new (not in AudioSculpt)

- **Kaleidoscope reflections** — generates harmonically related chord sequences
  by using each partial as a reflection center for the others. Multiple modes
  (nested cycles, temporal evolution, interweave, morph) for different musical
  results.
- **Subharmonic foundation inference** — infers low fundamental tones from
  spectral convergence patterns, useful for generating keyboard/bass parts that
  support the spectral harmony.
- **48-EDO / 24-EDO / 12-TET notation** — direct export to MusicXML with
  microtonal accidentals that import cleanly into Dorico's tonality systems.
- **Progressive melodic transformation** — auto-generate mode creates
  continuously diverging variants with smooth line elongation, plus spectral
  chord passages connecting each variant.
- **MusicXML round-trip** — feed a notated melody back into the spectral
  processor to generate kaleidoscope harmonies from your own melodic material.

## Workflow

```
                            ┌─────────────┐
                            │   Partiels  │
                            │   or SPEAR  │
                            └──────┬──────┘
                                   │
                          SDIF / JSON export
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
                    v                             v
          ┌─────────────────┐          ┌──────────────────┐
          │ sdif_processor   │          │ interactive_     │
          │                  │          │ converter        │
          │ • chord grouping │          │                  │
          │ • kaleidoscope   │          │ • pitch quant    │
          │ • morph/stretch  │          │ • rhythm quant   │
          │ • pitch shift    │          │ • subharmonics   │
          └───┬──────┬───┬──┘          └────────┬─────────┘
              │      │   │                      │
           SDIF   MusicXML  MIDI             MusicXML
              │      │                          │
              v      │    ┌─────────────────────┘
           SPEAR     │    │
         (resynth)   │    v
                     │  ┌──────────────────────┐
                     └─>│ melodic_transformer   │
                        │                       │
                        │ • auto-generate       │
                        │ • harmonize           │
                        │ • chain / multi-voice │
                        └───────────┬───────────┘
                                    │
                                 MusicXML
                                    │
                                    v
                              ┌───────────┐
                              │   Dorico  │
                              │ MuseScore │
                              └───────────┘
```

## Dependencies

- **Python 3.8+**
- **numpy** — required by sdif_processor.py
- **music21** — required by melodic_transformer.py; optional for sdif_processor.py
  (only needed for MusicXML input) and not needed by interactive_converter.py
- **questionary** — optional for all scripts (provides arrow-key TUI menus;
  falls back to plain text prompts)
- **midiutil** — optional (only needed for MIDI export from sdif_processor.py)

```bash
pip install numpy music21 questionary midiutil
```

## Usage

All three scripts run interactively by default — just run them and follow the
prompts:

```bash
python3 sdif_processor.py
python3 interactive_converter.py
python3 melodic_transformer.py
```

Or provide an input file:

```bash
python3 sdif_processor.py my_analysis.sdif
python3 interactive_converter.py ./partiels_export/
python3 melodic_transformer.py melody.musicxml
```

`sdif_processor.py` and `interactive_converter.py` also support full CLI batch
mode — see their manuals for all flags.

## Documentation

Each script has a detailed manual:

- [MANUAL_sdif_processor.md](MANUAL_sdif_processor.md)
- [MANUAL_interactive_converter.md](MANUAL_interactive_converter.md)
- [MANUAL_melodic_transformer.md](MANUAL_melodic_transformer.md)

## License

GPL-3.0 — see [LICENSE](LICENSE) for details.
