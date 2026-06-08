#!/usr/bin/env python3
"""
Melodic Transformer — MusicXML Melodic Line Transformation Tool

Reads MusicXML files, applies serial/spectral transformations to melodic lines,
and writes the results back to MusicXML. Supports chaining multiple transformations
and outputting multiple transformed variants as separate parts in a single file.

Uses music21 for MusicXML I/O. Uses questionary for rich TUI menus (optional).
"""

import sys
import copy
import math
import random
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple, Optional

# Suppress music21 MIDI channel warnings (harmless when >16 parts)
warnings.filterwarnings("ignore", message=".*midi channels.*", module="music21")
warnings.filterwarnings("ignore", category=UserWarning, module="music21")

# Monkey-patch the instrument module to suppress "out of midi channels" exception
try:
    from music21 import instrument as _inst
    _orig_autoAssign = _inst.Instrument.autoAssignMidiChannel
    def _quiet_autoAssign(self, *args, **kwargs):
        try:
            return _orig_autoAssign(self, *args, **kwargs)
        except Exception:
            self.midiChannel = 0  # silently fall back to channel 0
    _inst.Instrument.autoAssignMidiChannel = _quiet_autoAssign
except Exception:
    pass

# ── music21 ──────────────────────────────────────────────────────────────────
try:
    import music21
    from music21 import converter, stream, note, pitch, duration, interval, scale
    from music21 import meter, key, clef, instrument, chord
except ImportError:
    print("\n  music21 is required but not installed.")
    print("  Install it with:  pip3 install music21\n")
    sys.exit(1)

# ── Optional rich TUI ────────────────────────────────────────────────────────
try:
    import questionary
    from questionary import Style

    TUI_STYLE = Style([
        ('qmark', 'fg:cyan bold'),
        ('question', 'fg:white bold'),
        ('answer', 'fg:green bold'),
        ('pointer', 'fg:cyan bold'),
        ('highlighted', 'fg:cyan bold'),
        ('selected', 'fg:green'),
        ('separator', 'fg:yellow'),
        ('instruction', 'fg:grey'),
    ])
    HAS_QUESTIONARY = True
except ImportError:
    HAS_QUESTIONARY = False


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

SCALES = {
    "Major":              [0, 2, 4, 5, 7, 9, 11],
    "Natural minor":      [0, 2, 3, 5, 7, 8, 10],
    "Harmonic minor":     [0, 2, 3, 5, 7, 8, 11],
    "Dorian":             [0, 2, 3, 5, 7, 9, 10],
    "Phrygian":           [0, 1, 3, 5, 7, 8, 10],
    "Lydian":             [0, 2, 4, 6, 7, 9, 11],
    "Mixolydian":         [0, 2, 4, 5, 7, 9, 10],
    "Whole-tone":         [0, 2, 4, 6, 8, 10],
    "Octatonic (H-W)":    [0, 1, 3, 4, 6, 7, 9, 10],
    "Octatonic (W-H)":    [0, 2, 3, 5, 6, 8, 9, 11],
    "Chromatic":          list(range(12)),
    "Quarter-tone chromatic": [x * 0.5 for x in range(24)],
}

TRANSFORM_MENU = [
    ("Retrograde",            "R"),
    ("Inversion",             "I"),
    ("Retrograde Inversion",  "RI"),
    ("Pitch Compression",     "Comp"),
    ("Pitch Expansion",       "Exp"),
    ("Augmentation",          "Aug"),
    ("Diminution",            "Dim"),
    ("Transposition",         "T"),
    ("Rotation",              "Rot"),
    ("Permutation",           "Perm"),
    ("Pitch Quantization",    "Q"),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

import re as _re


def _next_output_path(input_path: Path, mode_tag: str) -> Path:
    """Generate a sequentially numbered output filename.

    Pattern: {stem}_{mode_tag}_01.musicxml, _02, _03, ...
    Scans the parent directory for existing files matching the pattern
    and picks the next number.

    mode_tag examples: "auto_8v", "multi", "chain"
    """
    parent = input_path.parent
    stem = input_path.stem
    suffix = input_path.suffix
    # Match files like:  stem_mode_tag_01.musicxml
    pattern = _re.compile(
        _re.escape(f"{stem}_{mode_tag}_") + r"(\d+)" + _re.escape(suffix) + "$"
    )
    existing_nums = []
    for f in parent.iterdir():
        m = pattern.match(f.name)
        if m:
            existing_nums.append(int(m.group(1)))
    next_num = max(existing_nums, default=0) + 1
    out_name = f"{stem}_{mode_tag}_{next_num:02d}{suffix}"
    return parent / out_name


def show_banner():
    print(r"""
    ╔══════════════════════════════════════════════╗
    ║       ♪  Melodic Transformer  ♪              ║
    ║   MusicXML melodic-line transformation tool  ║
    ╚══════════════════════════════════════════════╝
    """)


def prompt_choice(question: str, choices: List[str]) -> str:
    if HAS_QUESTIONARY:
        return questionary.select(question, choices=choices, style=TUI_STYLE).ask()
    print(f"\n  {question}")
    for i, c in enumerate(choices, 1):
        print(f"    {i:>2}. {c}")
    while True:
        raw = input("  Choice: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print("  Invalid selection, try again.")


def prompt_text(question: str, default: str = "") -> str:
    if HAS_QUESTIONARY:
        return questionary.text(question, default=default, style=TUI_STYLE).ask()
    suffix = f" [{default}]" if default else ""
    raw = input(f"  {question}{suffix}: ").strip()
    return raw if raw else default


def prompt_float(question: str, default: float = 1.0) -> float:
    while True:
        raw = prompt_text(question, str(default))
        try:
            return float(raw)
        except ValueError:
            print("  Please enter a number.")


def prompt_int(question: str, default: int = 0) -> int:
    while True:
        raw = prompt_text(question, str(default))
        try:
            return int(raw)
        except ValueError:
            print("  Please enter an integer.")


def prompt_confirm(question: str, default: bool = True) -> bool:
    if HAS_QUESTIONARY:
        return questionary.confirm(question, default=default, style=TUI_STYLE).ask()
    suffix = " [Y/n]" if default else " [y/N]"
    raw = input(f"  {question}{suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def prompt_path(question: str) -> str:
    if HAS_QUESTIONARY:
        return questionary.path(question, style=TUI_STYLE).ask()
    return input(f"  {question}: ").strip()


# ═══════════════════════════════════════════════════════════════════════════════
# Note extraction / reconstruction
# ═══════════════════════════════════════════════════════════════════════════════

def quantize_to_semitone(notes: List) -> List:
    """Snap all pitches to the nearest semitone (12-TET). Durations unchanged."""
    result = copy.deepcopy(notes)
    for n in result:
        if is_pitched(n):
            n.pitch = pitch.Pitch(ps=round(n.pitch.ps))
    return result


def quantize_chord_to_semitone(ch: chord.Chord) -> chord.Chord:
    """Snap all chord pitches to nearest semitone."""
    new_pitches = [pitch.Pitch(ps=round(p.ps)) for p in ch.pitches]
    return chord.Chord(new_pitches, quarterLength=ch.quarterLength)


def extract_notes(part: stream.Part) -> List:
    """Extract all Notes and Rests from a Part, preserving order and offsets."""
    elements = []
    for el in part.recurse().notesAndRests:
        el_copy = copy.deepcopy(el)
        # Store the original absolute offset for reconstruction
        el_copy._original_offset = el.getOffsetInHierarchy(part)
        elements.append(el_copy)
    return elements


def extract_notes_by_voice(part: stream.Part) -> dict:
    """Extract Notes and Rests grouped by voice.

    If the Part contains stream.Voice containers, returns an OrderedDict
    mapping voice ID → note list (e.g. {"1": [...], "2": [...]}).
    If no Voice containers are found (single-voice input), returns
    {"1": flat_list} — identical to extract_notes() wrapped in a dict.
    """
    from collections import OrderedDict

    # Check whether any measure has Voice containers
    has_voices = False
    for m in part.getElementsByClass(stream.Measure):
        if m.getElementsByClass(stream.Voice):
            has_voices = True
            break

    if not has_voices:
        return OrderedDict([("1", extract_notes(part))])

    # Multi-voice: extract per voice
    voices: dict = OrderedDict()
    for m in part.getElementsByClass(stream.Measure):
        for v in m.getElementsByClass(stream.Voice):
            vid = str(v.id)
            if vid not in voices:
                voices[vid] = []
            for el in v.notesAndRests:
                el_copy = copy.deepcopy(el)
                el_copy._original_offset = el.getOffsetInHierarchy(part)
                voices[vid].append(el_copy)

    return voices


def is_grace(el) -> bool:
    """Check if a note is a grace note (zero duration)."""
    return isinstance(el, note.Note) and el.quarterLength == 0.0


def is_pitched(el) -> bool:
    return isinstance(el, note.Note)


def get_pitched_indices(notes: List, include_grace: bool = False) -> List[int]:
    """Get indices of pitched notes. By default excludes grace notes."""
    indices = []
    for i, n in enumerate(notes):
        if is_pitched(n):
            if include_grace or not is_grace(n):
                indices.append(i)
    return indices


# ── Voice-dict helpers ────────────────────────────────────────────────────
# These let the mode functions work with either a flat list (single voice)
# or an OrderedDict of per-voice note lists (multi-voice) through a
# uniform interface.

def _is_voice_dict(obj) -> bool:
    """True if obj is a per-voice dict rather than a flat note list."""
    return isinstance(obj, dict)


def _all_notes(obj) -> List:
    """Return a single flat list from either a flat list or a voice dict."""
    if _is_voice_dict(obj):
        combined = []
        for vnotes in obj.values():
            combined.extend(vnotes)
        return combined
    return obj


def _for_each_voice(obj, fn, *args, **kwargs):
    """Apply fn(notes, *args, **kwargs) to each voice, return same structure.

    If obj is a flat list, calls fn once and returns the result.
    If obj is a voice dict, calls fn per voice and returns a new dict.
    """
    if _is_voice_dict(obj):
        from collections import OrderedDict
        return OrderedDict(
            (vid, fn(vnotes, *args, **kwargs))
            for vid, vnotes in obj.items()
        )
    return fn(obj, *args, **kwargs)


def _voice_len(obj) -> int:
    """Return the note count of the first (or only) voice."""
    if _is_voice_dict(obj):
        return len(next(iter(obj.values())))
    return len(obj)


def melody_info(notes: List) -> str:
    pitched = [n for n in notes if is_pitched(n)]
    if not pitched:
        return "  (no pitched notes)"
    ps_values = [n.pitch.ps for n in pitched]
    lo = min(ps_values)
    hi = max(ps_values)
    mean = sum(ps_values) / len(ps_values)
    total_dur = float(sum(n.quarterLength for n in notes))
    lo_name = pitch.Pitch(ps=lo).nameWithOctave
    hi_name = pitch.Pitch(ps=hi).nameWithOctave
    lines = [
        f"  Notes: {len(pitched)} pitched, {len(notes) - len(pitched)} rests",
        f"  Range: {lo_name} – {hi_name}  ({hi - lo:.1f} semitones)",
        f"  Mean pitch: {pitch.Pitch(ps=mean).nameWithOctave} ({mean:.1f})",
        f"  Total duration: {total_dur:.2f} quarter-notes",
    ]
    return "\n".join(lines)


def derive_summary_chord(stage_notes: List, num_tones: int = 3) -> chord.Chord:
    """Derive a summary chord from a transformation stage.

    Combines two interval analyses:
      1. Consecutive intervals — intervals between each note and the next
      2. Intervals from root — intervals from the first note to every other note

    Averages both sets, then builds a chord by stacking the averaged intervals
    from the mean pitch of the stage. Returns a music21 Chord.
    """
    pitched = [n for n in stage_notes if is_pitched(n) and not is_grace(n)]
    if len(pitched) < 2:
        # Not enough notes — return a single-note "chord" at the mean
        ps_val = pitched[0].pitch.ps if pitched else 60.0
        return chord.Chord([pitch.Pitch(ps=ps_val)], quarterLength=2.0)

    ps_values = [n.pitch.ps for n in pitched]

    # 1. Consecutive intervals (between adjacent notes)
    consec_intervals = []
    for i in range(len(ps_values) - 1):
        consec_intervals.append(abs(ps_values[i + 1] - ps_values[i]))

    # 2. Intervals from root (first note to every other note)
    root_intervals = []
    for i in range(1, len(ps_values)):
        root_intervals.append(abs(ps_values[i] - ps_values[0]))

    # Combine both sets and find the average interval
    all_intervals = consec_intervals + root_intervals
    avg_interval = sum(all_intervals) / len(all_intervals) if all_intervals else 0

    # Use mean pitch as the chord root
    mean_ps = sum(ps_values) / len(ps_values)

    # Build chord tones by stacking the average interval symmetrically
    # around the mean, rounded to nearest quarter-tone
    chord_tones = []
    if num_tones == 2:
        chord_tones = [
            mean_ps - avg_interval / 2,
            mean_ps + avg_interval / 2,
        ]
    elif num_tones == 3:
        chord_tones = [
            mean_ps - avg_interval,
            mean_ps,
            mean_ps + avg_interval,
        ]
    elif num_tones == 4:
        chord_tones = [
            mean_ps - avg_interval * 1.5,
            mean_ps - avg_interval * 0.5,
            mean_ps + avg_interval * 0.5,
            mean_ps + avg_interval * 1.5,
        ]
    else:
        chord_tones = [mean_ps]

    # Round to nearest quarter-tone and clamp to valid range
    chord_tones = [round(ps * 2) / 2 for ps in chord_tones]
    chord_tones = [_clamp_ps(ps) for ps in chord_tones]

    chord_pitches = [pitch.Pitch(ps=ps) for ps in chord_tones]
    summary = chord.Chord(chord_pitches, quarterLength=2.0)
    return summary


def interpolate_chords(chord_a: chord.Chord, chord_b: chord.Chord,
                       num_steps: int = 6) -> List[chord.Chord]:
    """Generate interpolation chords using kaleidoscope reflection.

    Adapted from the SDIF processor's nested-cycle approach: each step
    picks one pitch of chord_a as an *anchor* (held constant) and reflects
    the remaining pitches around a center drawn from chord_b. Both the
    anchor and center selections are shuffled to prevent predictable
    ascending/descending patterns.

    The number of output tones matches chord_a (no doubling).
    All reflected pitches are octave-folded to stay near the source register.
    """
    ps_a = sorted([p.ps for p in chord_a.pitches])
    ps_b = sorted([p.ps for p in chord_b.pitches])
    n = len(ps_a)

    # Reference register for octave folding
    all_ps = ps_a + ps_b
    ref_lo = min(all_ps)
    ref_hi = max(all_ps)

    # Build shuffled sequences of anchor indices and center pitches
    # to avoid predictable stepwise motion (ascending 1-2-3-4, jump, repeat)
    anchor_order = []
    while len(anchor_order) < num_steps:
        batch = list(range(n))
        random.shuffle(batch)
        anchor_order.extend(batch)
    anchor_order = anchor_order[:num_steps]

    center_pool = []
    while len(center_pool) < num_steps:
        batch = list(ps_b)
        random.shuffle(batch)
        center_pool.extend(batch)
    center_pool = center_pool[:num_steps]

    result = []
    for step in range(num_steps):
        anchor_idx = anchor_order[step]
        center_ps = center_pool[step]

        tones = []
        for idx, ps in enumerate(ps_a):
            if idx == anchor_idx:
                # Anchor stays put
                tones.append(ps)
            else:
                # Reflect around the center from chord_b
                reflected = center_ps - (ps - center_ps)

                # Octave-fold into the reference register (±6 st margin)
                while reflected > ref_hi + 6.0:
                    reflected -= 12.0
                while reflected < ref_lo - 6.0:
                    reflected += 12.0

                tones.append(reflected)

        # Round to nearest quarter-tone and clamp
        tones = sorted(_clamp_ps(round(ps * 2) / 2) for ps in tones)

        ch = chord.Chord(
            [pitch.Pitch(ps=ps) for ps in tones],
            quarterLength=1.0
        )
        result.append(ch)

    return result


def build_chord_passage(summary_a: chord.Chord, summary_b: chord.Chord,
                         bar_ql: float,
                         original_ts: Optional[meter.TimeSignature] = None) -> List:
    """Build a 3-bar chord passage with kaleidoscope reflections:
      Bar 1:   summary_a held (in the melody's time signature)
      Bars 2-3: 6 half-note reflection chords in 3/2 time

    The 6 chords rotate through reflections of summary_a using pitches
    from summary_b as centers, producing harmonic rotation rather than
    stepwise motion.

    Returns a list of note/chord/TimeSignature elements to append to the
    sequence. The TimeSignature objects carry zero quarterLength so they
    don't affect offset arithmetic; rebuild_part inserts them at the
    appropriate offset and makeMeasures uses them for barring.
    """
    elements = []

    # Bar 1: held chord in the melody's time signature
    held = copy.deepcopy(summary_a)
    held.quarterLength = bar_ql
    elements.append(held)

    # Switch to 3/2 for the interpolation chords (3 half-notes per bar)
    ts_32 = meter.TimeSignature('3/2')
    ts_32.quarterLength = 0  # sentinel — won't contribute to offset sum
    elements.append(ts_32)

    # 6 reflection-interpolation chords as half notes
    # Two bars of 3/2 = 6 half notes = 12 quarter-note beats total
    interp_chords = interpolate_chords(summary_a, summary_b, num_steps=6)
    for ch in interp_chords:
        ch.quarterLength = 2.0  # half note
        elements.append(ch)

    # Revert to the original time signature after the chord passage
    if original_ts is not None:
        ts_back = copy.deepcopy(original_ts)
    else:
        ts_back = meter.TimeSignature('4/4')
    ts_back.quarterLength = 0
    elements.append(ts_back)

    return elements


def rebuild_part(original_part: stream.Part, new_notes: List,
                 duration_changed: bool = False) -> stream.Part:
    """Build a new Part from transformed notes, inheriting metadata from original.

    Uses the original time signature and lets music21 create proper measures.
    Notes that cross barlines are tied across the barline (standard notation).
    """
    new_part = stream.Part()

    # Copy instrument and clef from original
    for el in original_part.recurse():
        if isinstance(el, instrument.Instrument):
            new_part.insert(0, copy.deepcopy(el))
        elif isinstance(el, clef.Clef):
            new_part.insert(0, copy.deepcopy(el))

    # Copy key signature
    ks = original_part.recurse().getElementsByClass(key.KeySignature)
    if ks:
        new_part.insert(0, copy.deepcopy(ks[0]))

    # Use the original time signature
    ts_list = original_part.recurse().getElementsByClass(meter.TimeSignature)
    if ts_list:
        new_part.insert(0, copy.deepcopy(ts_list[0]))
    else:
        new_part.insert(0, meter.TimeSignature('4/4'))

    if duration_changed:
        # Durations changed — recompute offsets sequentially.
        # TimeSignature objects (from chord passages) are inserted at the
        # current offset but don't advance it — they have ql=0.
        offset = 0.0
        for n in new_notes:
            nc = copy.deepcopy(n)
            new_part.insert(offset, nc)
            offset += float(nc.quarterLength)
    else:
        # Durations unchanged — preserve original offsets
        offset = 0.0  # track running offset for any mid-stream TS changes
        for n in new_notes:
            nc = copy.deepcopy(n)
            if isinstance(n, meter.TimeSignature):
                # Mid-stream time signature change — insert at running offset
                new_part.insert(offset, nc)
            else:
                orig_offset = getattr(n, '_original_offset', None)
                if orig_offset is not None:
                    new_part.insert(orig_offset, nc)
                    offset = orig_offset + float(nc.quarterLength)
                else:
                    new_part.insert(offset, nc)
                    offset += float(nc.quarterLength)

    # Create proper measures — notes crossing barlines get split and tied
    new_part.makeMeasures(inPlace=True)

    # Post-pass: fix sub-32nd-note fragments created by barline splitting.
    # makeMeasures can split tuplet notes at barlines, producing tied fragments
    # too short for MusicXML (e.g. "2048th").  We absorb these into their
    # tied neighbour or round up to a 32nd note.
    from fractions import Fraction
    _MIN_QL = Fraction(1, 8)  # 32nd note
    for m in new_part.getElementsByClass(stream.Measure):
        all_notes = list(m.recurse().notesAndRests)
        for n_obj in all_notes:
            if isinstance(n_obj, note.Rest):
                continue
            ql_frac = Fraction(n_obj.quarterLength).limit_denominator(1000)
            if 0 < ql_frac < _MIN_QL:
                # Tiny fragment — round up to 32nd note
                n_obj.quarterLength = float(_MIN_QL)
                # Remove tie if this was a tie-end fragment so notation
                # doesn't expect a continuation that no longer makes sense
                if n_obj.tie and n_obj.tie.type == 'stop':
                    n_obj.tie = None

    return new_part


def rebuild_part_multivoice(original_part: stream.Part,
                             voice_dict: dict,
                             duration_changed: bool = False) -> stream.Part:
    """Build a Part from per-voice note lists, preserving voice separation.

    Each voice's notes are processed through rebuild_part() independently
    (getting proper makeMeasures/barline splits), then merged measure-by-
    measure into stream.Voice containers in a single output Part.

    Parameters
    ----------
    voice_dict : dict[str, List]
        Mapping of voice ID → transformed note list.
    duration_changed : bool
        Whether durations were modified (passed through to rebuild_part).
    """
    # Build a separate Part per voice using existing rebuild_part logic
    voice_parts = {}
    for vid, vnotes in voice_dict.items():
        vpart = rebuild_part(original_part, vnotes,
                             duration_changed=duration_changed)
        voice_parts[vid] = vpart

    # Create the merged output Part
    merged = stream.Part()

    # Copy metadata from original (instrument, clef, key sig)
    for el in original_part.recurse():
        if isinstance(el, instrument.Instrument):
            merged.insert(0, copy.deepcopy(el))
            break
    for el in original_part.recurse():
        if isinstance(el, clef.Clef):
            merged.insert(0, copy.deepcopy(el))
            break
    ks = original_part.recurse().getElementsByClass(key.KeySignature)
    if ks:
        merged.insert(0, copy.deepcopy(ks[0]))

    # Determine the maximum number of measures across all voices
    voice_measures = {}
    max_measures = 0
    for vid, vpart in voice_parts.items():
        measures = list(vpart.getElementsByClass(stream.Measure))
        voice_measures[vid] = measures
        max_measures = max(max_measures, len(measures))

    # Determine bar length for filling missing-voice measures with rests
    ts_list = original_part.recurse().getElementsByClass(meter.TimeSignature)
    fill_bar_ql = float(ts_list[0].barDuration.quarterLength) if ts_list else 4.0

    # Merge measure by measure
    for mi in range(max_measures):
        out_measure = stream.Measure(number=mi + 1)

        # Copy time signature from first voice's measure (if present)
        first_vid = next(iter(voice_measures))
        if mi < len(voice_measures[first_vid]):
            ref_m = voice_measures[first_vid][mi]
            for ts in ref_m.getElementsByClass(meter.TimeSignature):
                out_measure.insert(0, copy.deepcopy(ts))
                fill_bar_ql = float(ts.barDuration.quarterLength)

        for vid in voice_dict:
            measures = voice_measures.get(vid, [])
            v = stream.Voice()
            v.id = vid

            if mi < len(measures):
                src_m = measures[mi]
                for el in src_m.notesAndRests:
                    el_copy = copy.deepcopy(el)
                    v.insert(el.offset, el_copy)
            else:
                # Voice ran out of measures — fill with a whole-measure rest
                # so makeNotation's makeTies can find this voice in every measure
                r = note.Rest()
                r.quarterLength = fill_bar_ql
                v.insert(0, r)

            out_measure.insert(0, v)

        merged.append(out_measure)

    return merged


def write_musicxml(score: stream.Score, out_path: Path):
    """Write a Score to MusicXML, then post-process to add <voice> elements.

    music21's export pipeline (makeRests/makeNotation) strips Voice containers,
    so the output XML lacks <voice> tags. Dorico requires these to display notes
    beyond the first measure. This function patches the XML after writing.

    Runs makeNotation on a deep copy, then strips any unexportable tuplet
    encodings (2048th, 512th, etc.) that music21 creates when splitting
    tuplet notes across beaming groups, before writing to disk.
    """
    from fractions import Fraction

    score_out = copy.deepcopy(score)
    score_out.makeNotation(inPlace=True)

    # Post-makeNotation cleanup.
    #
    # makeNotation produces correct measure totals but may create:
    #   - Bad tuplet types (2048th, 512th, etc.) from splitting tuplet notes
    #   - Complex or typeless durations inside Voice containers
    #   - Zero-duration non-grace artifacts
    #
    # We fix only these specific issues — we do NOT re-quantize all durations,
    # because makeNotation's barline splits produce valid (if non-standard)
    # durations like 0.0625 (64th) that have proper MusicXML types.
    _BAD_TYPES = frozenset(('2048th', '1024th', '512th', '256th', '128th'))

    for p in score_out.parts:
        for m in p.getElementsByClass(stream.Measure):
            # Split complex durations first
            m.splitAtDurations()
            for v in m.getElementsByClass(stream.Voice):
                v.splitAtDurations()

            # Collect containers (measure itself + any Voice containers)
            containers = [m]
            containers.extend(m.getElementsByClass(stream.Voice))

            for container in containers:
                to_remove = []
                for n_obj in list(container.notesAndRests):
                    if not hasattr(n_obj, 'duration'):
                        continue
                    ql = float(n_obj.duration.quarterLength)
                    d = n_obj.duration

                    # Remove zero-dur non-grace artifacts
                    if ql <= 0 and not d.isGrace:
                        to_remove.append(n_obj)
                        continue

                    # Fix bad tuplet types (sub-32nd from tuplet splitting)
                    if d.tuplets:
                        bad = any(
                            getattr(t.durationNormal, 'type', None) in _BAD_TYPES
                            for t in d.tuplets if t.durationNormal
                        )
                        if bad:
                            if ql < 0.125:
                                to_remove.append(n_obj)
                            else:
                                n_obj.quarterLength = float(_quantize_duration(ql))
                            continue

                    # Fix complex or typeless durations
                    if (ql > 0
                            and (not d.type
                                 or d.type == 'complex'
                                 or d.type == 'zero')):
                        n_obj.quarterLength = float(_quantize_duration(ql))

                for n_obj in to_remove:
                    try:
                        container.remove(n_obj)
                    except Exception:
                        pass

    score_out.write("musicxml", fp=str(out_path), makeNotation=False)

    tree = ET.parse(str(out_path))
    root = tree.getroot()

    # Handle possible namespace
    tag = root.tag
    ns = ""
    if tag.startswith("{"):
        ns = tag.split("}")[0] + "}"

    # Strip <accidental> elements — Dorico infers accidentals from <alter>
    # after respelling with the appropriate tonality system (e.g. 24-EDO).
    # music21's default accidental names (sharp, flat, etc.) conflict with
    # custom tonality systems and produce wrong symbols.
    for note_el in root.iter(f"{ns}note"):
        for acc_el in note_el.findall(f"{ns}accidental"):
            note_el.remove(acc_el)

    for note_el in root.iter(f"{ns}note"):
        if note_el.find(f"{ns}voice") is None:
            # MusicXML schema order: pitch/rest, duration, tie, voice, type, ...
            # Insert <voice>1</voice> after <tie> (or after <duration> if no tie)
            voice_el = ET.SubElement(note_el, f"{ns}voice")
            voice_el.text = "1"

            # Find the correct insertion point
            tie_els = note_el.findall(f"{ns}tie")
            dur_el = note_el.find(f"{ns}duration")
            type_el = note_el.find(f"{ns}type")

            note_el.remove(voice_el)
            if tie_els:
                # Insert after the last <tie> element
                last_tie_idx = max(list(note_el).index(t) for t in tie_els)
                note_el.insert(last_tie_idx + 1, voice_el)
            elif dur_el is not None:
                dur_idx = list(note_el).index(dur_el)
                note_el.insert(dur_idx + 1, voice_el)
            elif type_el is not None:
                type_idx = list(note_el).index(type_el)
                note_el.insert(type_idx, voice_el)
            else:
                # Grace note or bare note — just append
                note_el.append(voice_el)

    # Write back, preserving the DOCTYPE that music21 originally included
    xml_bytes = ET.tostring(root, encoding="unicode")
    with open(str(out_path), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        f.write('<!DOCTYPE score-partwise  PUBLIC '
                '"-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
                '"http://www.musicxml.org/dtds/partwise.dtd">\n')
        f.write(xml_bytes)


# ═══════════════════════════════════════════════════════════════════════════════
# Transformations
# ═══════════════════════════════════════════════════════════════════════════════

def apply_retrograde(notes: List) -> List:
    """Reverse pitch order; rhythms stay in place."""
    result = copy.deepcopy(notes)
    pitched_idx = get_pitched_indices(result)
    pitches = [result[i].pitch for i in pitched_idx]
    pitches.reverse()
    for i, pi in enumerate(pitched_idx):
        result[pi].pitch = pitches[i]
    return result


def apply_inversion(notes: List, pivot_ps: Optional[float] = None) -> List:
    """Mirror pitches around a pivot (in pitch-space, preserving microtones)."""
    result = copy.deepcopy(notes)
    pitched_idx = get_pitched_indices(result)
    if not pitched_idx:
        return result
    if pivot_ps is None:
        pivot_ps = result[pitched_idx[0]].pitch.ps
    for i in pitched_idx:
        orig_ps = result[i].pitch.ps
        new_ps = 2 * pivot_ps - orig_ps
        result[i].pitch = pitch.Pitch(ps=new_ps)
    return result


def apply_retrograde_inversion(notes: List, pivot_ps: Optional[float] = None) -> List:
    """Inversion followed by retrograde."""
    return apply_retrograde(apply_inversion(notes, pivot_ps))


# MusicXML valid range: octave 0 (C0, ps=12) to octave 9 (B9, ps=131)
_MIN_PS = 12.0   # C0
_MAX_PS = 131.0  # B9


def _clamp_ps(ps: float) -> float:
    """Clamp a single pitch-space value into MusicXML range by octave folding."""
    while ps > _MAX_PS:
        ps -= 12.0
    while ps < _MIN_PS:
        ps += 12.0
    return ps


def _clamp_pitches(notes: List) -> List:
    """Clamp all pitched notes into the MusicXML-valid range (C0–B9).

    Notes outside the range are folded back by octave until they fit.
    """
    for n in notes:
        if is_pitched(n) and not is_grace(n):
            ps = n.pitch.ps
            clamped = _clamp_ps(ps)
            if clamped != ps:
                n.pitch = pitch.Pitch(ps=clamped)
    return notes


def _fold_to_range(notes: List, ref_lo: float, ref_hi: float,
                    max_extra: float = 12.0) -> List:
    """Fold pitches back into a sane register by octave transposition.

    Adapted from the SDIF processor's octave-folding approach. Pitches that
    stray more than *max_extra* semitones beyond the reference range
    [ref_lo, ref_hi] are folded by octave until they fit.
    """
    lo = ref_lo - max_extra
    hi = ref_hi + max_extra
    for n in notes:
        if is_pitched(n) and not is_grace(n):
            ps = n.pitch.ps
            while ps > hi:
                ps -= 12.0
            while ps < lo:
                ps += 12.0
            if ps != n.pitch.ps:
                n.pitch = pitch.Pitch(ps=ps)
    return notes


def apply_pitch_compression(notes: List, factor: float) -> List:
    """Compress intervals toward mean pitch. factor < 1 compresses, > 1 expands."""
    result = copy.deepcopy(notes)
    pitched_idx = get_pitched_indices(result)
    if not pitched_idx:
        return result
    mean_ps = sum(result[i].pitch.ps for i in pitched_idx) / len(pitched_idx)
    for i in pitched_idx:
        orig = result[i].pitch.ps
        new_ps = mean_ps + (orig - mean_ps) * factor
        # Round to nearest quarter-tone (0.5 semitone)
        new_ps = round(new_ps * 2) / 2
        result[i].pitch = pitch.Pitch(ps=new_ps)
    return result


def _quantize_duration(ql: float):
    """Snap a quarterLength to the nearest clean notatable duration.

    Uses a whitelist of standard durations that produce proper notation
    in music21 and Dorico: simple subdivisions (1/4, 1/2, 1, 2, etc.),
    dotted values (3/8, 3/4, 3/2, etc.), and triplets (1/3, 1/6, 2/3).
    Returns a Fraction so music21 generates clean tuplet brackets.
    """
    from fractions import Fraction
    if ql <= 0:
        return ql
    # All standard notatable durations up to a breve (8 quarter-notes).
    # Includes simple, dotted, triplet (3:2), and quintuplet (5:4) values.
    # Septuplet values (7:4, 7:8) are excluded — they produce nonsensical
    # compound tuplets (e.g. 56:29) when music21 fills in rests around them.
    _CLEAN_DURATIONS = [
        Fraction(1, 8),   # 32nd note
        Fraction(1, 6),   # triplet sixteenth
        Fraction(1, 5),   # quintuplet sixteenth
        Fraction(3, 16),  # dotted 32nd
        Fraction(1, 4),   # sixteenth
        Fraction(1, 3),   # triplet eighth
        Fraction(2, 5),   # quintuplet eighth
        Fraction(3, 8),   # dotted sixteenth
        Fraction(1, 2),   # eighth
        Fraction(2, 3),   # triplet quarter
        Fraction(3, 4),   # dotted eighth
        Fraction(4, 5),   # quintuplet quarter
        Fraction(1, 1),   # quarter
        Fraction(4, 3),   # triplet half
        Fraction(3, 2),   # dotted quarter
        Fraction(8, 5),   # quintuplet half
        Fraction(2, 1),   # half
        Fraction(8, 3),   # triplet whole
        Fraction(3, 1),   # dotted half
        Fraction(4, 1),   # whole
        Fraction(6, 1),   # dotted whole
        Fraction(8, 1),   # breve
    ]
    ql_frac = Fraction(ql).limit_denominator(1000)
    best = min(_CLEAN_DURATIONS, key=lambda d: abs(ql_frac - d))
    return max(best, Fraction(1, 8))  # floor = 32nd note


def apply_augmentation(notes: List, factor: float) -> List:
    """Multiply all durations by factor. Grace notes (dur=0) are unchanged.
    Durations are quantized to the nearest clean notatable value.

    When the factor would shrink the shortest note below a triplet sixteenth
    (ql = 1/6), the factor is automatically clamped so the result stays
    playable. This prevents compounding diminutions from creating
    unperformable passages.
    """
    from fractions import Fraction
    result = copy.deepcopy(notes)

    # Find shortest non-grace note
    min_ql = None
    for n in result:
        ql = float(n.quarterLength)
        if ql > 0:
            if min_ql is None or ql < min_ql:
                min_ql = ql

    # Cap the factor so the shortest note never goes below triplet 16th
    _PLAYABLE_FLOOR = Fraction(1, 6)  # triplet sixteenth
    if min_ql is not None and factor < 1.0:
        # factor < 1 means shrinking (diminution path)
        min_result = min_ql * factor
        if min_result < float(_PLAYABLE_FLOOR):
            factor = float(_PLAYABLE_FLOOR) / min_ql
            # Don't let the cap push factor above 1.0 (that would be augmentation)
            factor = min(factor, 1.0)

    for n in result:
        if n.quarterLength > 0:
            n.quarterLength = _quantize_duration(float(n.quarterLength) * factor)
    return result


def apply_diminution(notes: List, factor: float) -> List:
    """Divide all durations by factor."""
    return apply_augmentation(notes, 1.0 / factor)


def apply_transposition(notes: List, semitones: float) -> List:
    """Shift all pitches by N semitones (supports quarter-tones, e.g. 0.5)."""
    result = copy.deepcopy(notes)
    for i in get_pitched_indices(result):
        result[i].pitch = pitch.Pitch(ps=result[i].pitch.ps + semitones)
    return result


def apply_rotation(notes: List, positions: int) -> List:
    """Rotate pitch sequence by N positions; rhythms stay in place."""
    result = copy.deepcopy(notes)
    pitched_idx = get_pitched_indices(result)
    if not pitched_idx:
        return result
    pitches = [result[i].pitch for i in pitched_idx]
    n = len(pitches)
    positions = positions % n
    rotated = pitches[positions:] + pitches[:positions]
    for i, pi in enumerate(pitched_idx):
        result[pi].pitch = rotated[i]
    return result


def apply_permutation(notes: List, num_segments: int, order: List[int]) -> List:
    """Divide notes into segments and reorder them."""
    result = copy.deepcopy(notes)
    seg_size = len(result) // num_segments
    segments = []
    for i in range(num_segments):
        start = i * seg_size
        end = start + seg_size if i < num_segments - 1 else len(result)
        segments.append(result[start:end])

    reordered = []
    for idx in order:
        reordered.extend(segments[idx])
    return reordered


def apply_pitch_quantization(notes: List, scale_pcs: List[float],
                              root: int = 0) -> List:
    """Snap pitches to the nearest pitch class in a given scale.

    scale_pcs: list of pitch classes (0–11, supports 0.5 increments for quarter-tones)
    root: transposition of the scale root (0 = C)
    """
    result = copy.deepcopy(notes)
    # Build the full set of target pitch classes, transposed to root
    targets = sorted([(pc + root) % 12 for pc in scale_pcs])

    for i in get_pitched_indices(result):
        ps = result[i].pitch.ps
        pc = ps % 12  # pitch class as float
        octave_base = ps - pc

        # Find nearest target pitch class
        best_pc = min(targets, key=lambda t: min(abs(pc - t), 12 - abs(pc - t)))
        # Handle wrapping
        diff1 = best_pc - pc
        diff2 = (best_pc - pc + 12) % 12
        diff3 = (best_pc - pc - 12) % 12
        diffs = [diff1, diff2, diff3]
        best_diff = min(diffs, key=abs)

        result[i].pitch = pitch.Pitch(ps=ps + best_diff)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Interactive menu
# ═══════════════════════════════════════════════════════════════════════════════

def select_part(score: stream.Score) -> Tuple[stream.Part, int]:
    """Let user select a part if multiple exist."""
    parts = list(score.parts)
    if len(parts) == 1:
        print(f"  Using part: {parts[0].partName or 'Part 1'}")
        return parts[0], 0

    choices = []
    for i, p in enumerate(parts):
        name = p.partName or f"Part {i + 1}"
        note_count = len(list(p.recurse().notes))
        choices.append(f"{name} ({note_count} notes)")

    selected = prompt_choice("Select a part:", choices)
    idx = choices.index(selected)
    return parts[idx], idx


def collect_transform_params(name: str) -> dict:
    """Collect parameters for a given transformation."""
    params = {}

    if name == "Inversion":
        custom = prompt_confirm("Use first note as pivot?", default=True)
        if not custom:
            params["pivot_ps"] = prompt_float("Pivot pitch (MIDI, e.g. 60 = C4)", 60.0)

    elif name == "Retrograde Inversion":
        custom = prompt_confirm("Use first note as pivot?", default=True)
        if not custom:
            params["pivot_ps"] = prompt_float("Pivot pitch (MIDI, e.g. 60 = C4)", 60.0)

    elif name == "Pitch Compression":
        params["factor"] = prompt_float("Compression factor (0–1, e.g. 0.5)", 0.5)

    elif name == "Pitch Expansion":
        params["factor"] = prompt_float("Expansion factor (>1, e.g. 1.5)", 1.5)

    elif name == "Augmentation":
        params["factor"] = prompt_float("Duration multiplier (e.g. 2.0)", 2.0)

    elif name == "Diminution":
        params["factor"] = prompt_float("Duration divisor (e.g. 2.0)", 2.0)

    elif name == "Transposition":
        params["semitones"] = prompt_float(
            "Semitones (use 0.5 for quarter-tone, negative for down)", 0)

    elif name == "Rotation":
        params["positions"] = prompt_int("Rotate by N positions", 1)

    elif name == "Permutation":
        params["num_segments"] = prompt_int("Number of segments", 3)
        n = params["num_segments"]
        print(f"  Enter new order as space-separated indices (0–{n-1}):")
        while True:
            raw = input(f"  Order [e.g. {' '.join(str(i) for i in reversed(range(n)))}]: ").strip()
            try:
                order = [int(x) for x in raw.split()]
                if sorted(order) == list(range(n)):
                    params["order"] = order
                    break
                print(f"  Must be a permutation of 0–{n-1}")
            except ValueError:
                print("  Invalid input.")

    elif name == "Pitch Quantization":
        scale_names = list(SCALES.keys())
        chosen = prompt_choice("Target scale:", scale_names)
        params["scale_pcs"] = SCALES[chosen]
        params["root"] = prompt_int("Scale root (0=C, 1=C#, 2=D, ...)", 0)
        params["scale_name"] = chosen

    return params


def apply_transform(name: str, notes: List, params: dict) -> Tuple[List, str, bool]:
    """Apply a named transformation. Returns (new_notes, label, duration_changed).

    All pitch-affecting transforms are clamped to MusicXML-valid range (C0–B9).
    """
    result, label, dur = notes, "?", False

    if name == "Retrograde":
        result, label, dur = apply_retrograde(notes), "R", False

    elif name == "Inversion":
        pivot = params.get("pivot_ps")
        result, label, dur = apply_inversion(notes, pivot), "I", False

    elif name == "Retrograde Inversion":
        pivot = params.get("pivot_ps")
        result, label, dur = apply_retrograde_inversion(notes, pivot), "RI", False

    elif name == "Pitch Compression":
        f = params["factor"]
        result, label, dur = apply_pitch_compression(notes, f), f"Comp{f:.2g}", False

    elif name == "Pitch Expansion":
        f = params["factor"]
        result, label, dur = apply_pitch_compression(notes, f), f"Exp{f:.2g}", False

    elif name == "Augmentation":
        f = params["factor"]
        result, label, dur = apply_augmentation(notes, f), f"Aug{f:.2g}", True

    elif name == "Diminution":
        f = params["factor"]
        result, label, dur = apply_diminution(notes, f), f"Dim{f:.2g}", True

    elif name == "Transposition":
        s = params["semitones"]
        sign = "+" if s >= 0 else ""
        s_label = f"{s:g}" if s != int(s) else str(int(s))
        result, label, dur = apply_transposition(notes, s), f"T{sign}{s_label}", False

    elif name == "Rotation":
        p = params["positions"]
        result, label, dur = apply_rotation(notes, p), f"Rot{p}", False

    elif name == "Permutation":
        result, label, dur = apply_permutation(notes, params["num_segments"], params["order"]), "Perm", False

    elif name == "Pitch Quantization":
        qlabel = params.get("scale_name", "Q")
        short = qlabel.split()[0][:4]
        result, label, dur = apply_pitch_quantization(
            notes, params["scale_pcs"], params.get("root", 0)
        ), f"Q{short}", False

    # Clamp all pitches to valid MusicXML range
    _clamp_pitches(result)
    return result, label, dur


def _build_notes_with_chord(vnotes: List, bar_ql: float,
                             dur_changed: bool,
                             next_summary: Optional[chord.Chord] = None,
                             original_ts: Optional[meter.TimeSignature] = None,
                             snap_to_semitone: bool = False) -> Tuple[List, chord.Chord]:
    """Append pad rest + chord passage to a note list.

    If next_summary is provided, a 3-bar passage is added: 1 bar held chord +
    6 reflection-interpolation chords across 2 bars of 3/2. Otherwise, just
    holds the summary chord for 2 bars.

    If *snap_to_semitone* is True, the summary chord and all interpolation
    chords are quantized to 12-TET (nearest semitone). Use this for the
    semitone-quantized doubles so the chord passage matches the melody.
    """
    notes_with_chord = [copy.deepcopy(n) for n in vnotes]
    total_ql = float(sum(n.quarterLength for n in vnotes))
    remainder = total_ql % bar_ql
    if remainder > 0.001:
        pad_rest = note.Rest()
        pad_rest.quarterLength = bar_ql - remainder
        notes_with_chord.append(pad_rest)

    summary = derive_summary_chord(vnotes, num_tones=4)
    summary.quarterLength = bar_ql

    if snap_to_semitone:
        summary = quantize_chord_to_semitone(summary)

    if next_summary is not None:
        # 3-bar passage: held chord + 6 reflection-interpolation chords in 3/2
        passage = build_chord_passage(summary, next_summary, bar_ql,
                                       original_ts=original_ts)
        if snap_to_semitone:
            # Quantize every chord in the passage to 12-TET
            for el in passage:
                if isinstance(el, chord.Chord):
                    for p_obj in el.pitches:
                        p_obj.ps = round(p_obj.ps)
        notes_with_chord.extend(passage)
    else:
        # Just the held chord for 2 bars
        notes_with_chord.append(copy.deepcopy(summary))
        held2 = copy.deepcopy(summary)
        held2.quarterLength = bar_ql
        notes_with_chord.append(held2)

    return notes_with_chord, summary


def run_multi_voice_mode(score: stream.Score, source_part: stream.Part,
                          part_idx: int, notes, input_path: Path):
    """Generate multiple transformed variants as separate parts in one output file.

    *notes* can be a flat list or a voice dict. Transforms are applied per-voice.
    """
    multivoice = _is_voice_dict(notes)
    flat_notes = _all_notes(notes)

    print("\n  ─── Multi-Voice Mode ───")
    print("  Create multiple transformations as separate parts/staves.")
    print("  The original melody will be included as the first part.\n")

    include_semitone = prompt_confirm(
        "Include semitone-quantized (12-TET) doubles for each voice?", default=True)

    variants = []  # List of (label, notes_or_dict, duration_changed)
    variants.append(("Original", notes, False))

    while True:
        print(f"\n  Variants so far: {len(variants)}")
        for i, (label, _, _) in enumerate(variants):
            print(f"    {i + 1}. {label}")

        transform_names = [t[0] for t in TRANSFORM_MENU]
        transform_names.append("── Done, write output ──")
        chosen = prompt_choice("Add a transformation variant:", transform_names)

        if chosen.startswith("──"):
            break

        params = collect_transform_params(chosen)
        # Always transform from the original notes, not from a previous variant
        source = prompt_choice(
            "Transform from which variant?",
            [v[0] for v in variants]
        )
        source_idx = next(i for i, v in enumerate(variants) if v[0] == source)
        source_data = variants[source_idx][1]

        # Apply transform per-voice or flat
        if multivoice and _is_voice_dict(source_data):
            new_data = {}
            for vid, vnotes_v in source_data.items():
                new_data[vid], label, dur_changed = apply_transform(
                    chosen, vnotes_v, params)
        else:
            new_data, label, dur_changed = apply_transform(
                chosen, source_data, params)

        full_label = f"{variants[source_idx][0]}→{label}" if source_idx > 0 else label
        variants.append((full_label, new_data, dur_changed))

        print(f"\n  Added variant: {full_label}")
        print(melody_info(_all_notes(new_data)))

    # Build output score — each variant gets its notes + summary chord appended.
    # If semitone doubles are enabled, a quantized staff appears below each variant.
    out_score = stream.Score()

    # Get beats per measure from original time signature
    ts_list = source_part.recurse().getElementsByClass(meter.TimeSignature)
    orig_ts = copy.deepcopy(ts_list[0]) if ts_list else meter.TimeSignature('4/4')
    bar_ql = float(orig_ts.barDuration.quarterLength)

    # Pre-compute all summary chords so we can interpolate between them
    summaries = [derive_summary_chord(_all_notes(v[1]), num_tones=4)
                 for v in variants]

    part_num = 0
    for i, (label, vdata, dur_changed) in enumerate(variants):
        # Get the next summary chord for interpolation
        next_summary = summaries[i + 1] if i + 1 < len(summaries) else summaries[0]

        if multivoice and _is_voice_dict(vdata):
            # Build per-voice note lists with chord passage
            voice_with_chord = {}
            for vid, vn in vdata.items():
                nwc, _ = _build_notes_with_chord(
                    vn, bar_ql, dur_changed, next_summary=next_summary,
                    original_ts=orig_ts)
                voice_with_chord[vid] = nwc
            part_num += 1
            new_part = rebuild_part_multivoice(
                source_part, voice_with_chord, dur_changed)
        else:
            flat_v = _all_notes(vdata) if _is_voice_dict(vdata) else vdata
            notes_with_chord, _ = _build_notes_with_chord(
                flat_v, bar_ql, dur_changed, next_summary=next_summary,
                original_ts=orig_ts)
            part_num += 1
            new_part = rebuild_part(source_part, notes_with_chord, dur_changed)

        new_part.partName = label
        new_part.id = f"P{part_num}"
        out_score.insert(0, new_part)

        # Semitone-quantized double (always single-voice — voices merged)
        if include_semitone:
            flat_v = _all_notes(vdata)
            qt_notes = quantize_to_semitone(flat_v)
            qt_next = quantize_chord_to_semitone(next_summary)
            qt_notes_with_chord, _ = _build_notes_with_chord(
                qt_notes, bar_ql, dur_changed, next_summary=qt_next,
                original_ts=orig_ts, snap_to_semitone=True)
            part_num += 1
            qt_part = rebuild_part(source_part, qt_notes_with_chord, dur_changed)
            qt_part.partName = f"{label} (12-TET)"
            qt_part.id = f"P{part_num}"
            out_score.insert(0, qt_part)

    # Write output
    out_path = _next_output_path(input_path, "multi")
    write_musicxml(out_score, out_path)
    print(f"\n  ✓ Written multi-voice output to:\n    {out_path}")


def _append_stage_to_sequence(all_notes: List, running_ql: float,
                               stage_notes: List, bar_ql: float,
                               is_first: bool,
                               next_summary: Optional[chord.Chord] = None,
                               original_ts: Optional[meter.TimeSignature] = None,
                               snap_to_semitone: bool = False) -> float:
    """Append a stage's notes + pad + chord passage to a sequential note list.
    Returns the updated running_ql."""
    if not is_first:
        sep = note.Rest()
        sep.quarterLength = 1.0
        all_notes.append(sep)
        running_ql += 1.0

    for n in stage_notes:
        all_notes.append(copy.deepcopy(n))
        running_ql += float(n.quarterLength)

    # Pad to barline
    remainder = running_ql % bar_ql
    if remainder > 0.001:
        pad = bar_ql - remainder
        pad_rest = note.Rest()
        pad_rest.quarterLength = pad
        all_notes.append(pad_rest)
        running_ql += pad

    # Chord passage with interpolation (switches to 3/2 for 6 half-note chords)
    summary = derive_summary_chord(stage_notes, num_tones=4)
    summary.quarterLength = bar_ql

    if snap_to_semitone:
        summary = quantize_chord_to_semitone(summary)

    if next_summary is not None:
        passage = build_chord_passage(summary, next_summary, bar_ql,
                                       original_ts=original_ts)
        if snap_to_semitone:
            for el in passage:
                if isinstance(el, chord.Chord):
                    for p_obj in el.pitches:
                        p_obj.ps = round(p_obj.ps)
        all_notes.extend(passage)
        running_ql += sum(float(e.quarterLength) for e in passage)
    else:
        # No next chord — just hold for 2 bars
        all_notes.append(copy.deepcopy(summary))
        held2 = copy.deepcopy(summary)
        held2.quarterLength = bar_ql
        all_notes.append(held2)
        running_ql += bar_ql * 2

    return running_ql


def run_chain_mode(score: stream.Score, source_part: stream.Part,
                    part_idx: int, notes, input_path: Path):
    """Chain transformations on a single melodic line.

    Each intermediate step is laid out sequentially on one staff.
    Format per stage: microtonal line → chord | quantized line → chord

    For multi-voice input, transforms are applied per-voice and the output
    preserves voice separation via rebuild_part_multivoice().
    """
    multivoice = _is_voice_dict(notes)
    flat_notes = _all_notes(notes)

    print("\n  ─── Chain Mode ───")
    print("  Apply transformations sequentially. Each step laid out on one staff.\n")
    if multivoice:
        print(f"  (multi-voice input: {len(notes)} voices — transforms applied per-voice)\n")
    print(melody_info(flat_notes))

    include_semitone = prompt_confirm(
        "Include semitone-quantized (12-TET) doubles for each stage?", default=True)

    # Track all stages: (label, notes_or_dict, duration_changed_cumulative)
    stages = [("Original", notes, False)]
    current = notes
    labels = []
    any_dur_changed = False

    while True:
        transform_names = [t[0] for t in TRANSFORM_MENU]
        transform_names.append("── Done, write output ──")
        chosen = prompt_choice("\nApply transformation:", transform_names)

        if chosen.startswith("──"):
            break

        params = collect_transform_params(chosen)
        if multivoice and _is_voice_dict(current):
            new_current = {}
            for vid, vnotes_v in current.items():
                new_current[vid], label, dur_changed = apply_transform(
                    chosen, vnotes_v, params)
            current = new_current
        else:
            current, label, dur_changed = apply_transform(chosen, current, params)
        labels.append(label)
        any_dur_changed = any_dur_changed or dur_changed

        cumulative_label = " → ".join(labels)
        stages.append((cumulative_label, current, any_dur_changed))

        print(f"\n  Applied: {label}")
        print(melody_info(_all_notes(current)))

    if len(stages) <= 1:
        print("  No transformations applied.")
        return

    # Build output: for each stage, microtonal line → chord passage,
    # then (optionally) quantized line → quantized chord passage.
    ts_list = source_part.recurse().getElementsByClass(meter.TimeSignature)
    orig_ts = copy.deepcopy(ts_list[0]) if ts_list else meter.TimeSignature('4/4')
    bar_ql = float(orig_ts.barDuration.quarterLength)

    # Pre-compute all summary chords for interpolation
    summaries = [derive_summary_chord(_all_notes(s[1]), num_tones=4)
                 for s in stages]

    if multivoice:
        # Build per-voice sequential note lists in parallel
        voice_ids = list(notes.keys())
        voice_all = {vid: [] for vid in voice_ids}
        voice_ql  = {vid: 0.0 for vid in voice_ids}
        is_first = True

        for i, (stage_label, stage_data, dur_changed) in enumerate(stages):
            next_sum = summaries[i + 1] if i + 1 < len(summaries) else summaries[0]
            for vid in voice_ids:
                if _is_voice_dict(stage_data):
                    v_stage = stage_data[vid]
                else:
                    v_stage = stage_data
                voice_ql[vid] = _append_stage_to_sequence(
                    voice_all[vid], voice_ql[vid], v_stage, bar_ql, is_first,
                    next_summary=next_sum, original_ts=orig_ts)
            is_first = False

            if include_semitone:
                qt_next = quantize_chord_to_semitone(next_sum)
                for vid in voice_ids:
                    if _is_voice_dict(stage_data):
                        v_stage = stage_data[vid]
                    else:
                        v_stage = stage_data
                    qt_notes = quantize_to_semitone(v_stage)
                    voice_ql[vid] = _append_stage_to_sequence(
                        voice_all[vid], voice_ql[vid], qt_notes, bar_ql, False,
                        next_summary=qt_next, original_ts=orig_ts,
                        snap_to_semitone=True)

        from collections import OrderedDict
        voice_dict_out = OrderedDict((vid, voice_all[vid]) for vid in voice_ids)
        new_part = rebuild_part_multivoice(
            source_part, voice_dict_out, duration_changed=True)
    else:
        all_notes_seq = []
        running_ql = 0.0
        is_first = True

        for i, (stage_label, stage_data, dur_changed) in enumerate(stages):
            flat_stage = _all_notes(stage_data)
            next_sum = summaries[i + 1] if i + 1 < len(summaries) else summaries[0]

            # Microtonal line + chord passage
            running_ql = _append_stage_to_sequence(
                all_notes_seq, running_ql, flat_stage, bar_ql, is_first,
                next_summary=next_sum, original_ts=orig_ts)
            is_first = False

            # Quantized double + quantized chord passage
            if include_semitone:
                qt_notes = quantize_to_semitone(flat_stage)
                qt_next = quantize_chord_to_semitone(next_sum)
                running_ql = _append_stage_to_sequence(
                    all_notes_seq, running_ql, qt_notes, bar_ql, False,
                    next_summary=qt_next, original_ts=orig_ts,
                    snap_to_semitone=True)

        new_part = rebuild_part(source_part, all_notes_seq, duration_changed=True)

    stage_labels = [s[0] for s in stages]
    new_part.partName = " | ".join(stage_labels)

    out_score = stream.Score()
    out_score.insert(0, new_part)

    out_path = _next_output_path(input_path, "chain")
    write_musicxml(out_score, out_path)

    print(f"\n  ✓ Written {len(stages)} stages sequentially to:")
    print(f"    {out_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# Auto-generate mode — progressive divergence
# ═══════════════════════════════════════════════════════════════════════════════

# Duration factors use simple ratios that produce standard notation:
#   2x, 1.5x (dotted), 4/3 (triplet), 0.5x, 2/3 (triplet), 0.75 (dotted)
# These yield clean duplets, triplets, and dotted values — no irrational tuplets.
_CLEAN_AUG_FACTORS = [1.5, 2.0, 4/3]        # dotted, double, triplet-stretch
_CLEAN_DIM_FACTORS = [1.5, 2.0, 3/2, 4/3]   # → 0.67x, 0.5x, 0.67x, 0.75x


def _pick_secondary_transform(intensity: float, num_pitched: int) -> Tuple[str, dict]:
    """Pick a secondary pitch transform (rotation, retrograde, permutation, etc.).

    No compression — only operations that rearrange or disrupt pitch order.
    All structural transforms (RI, retrograde, inversion) are available from
    the start. Aggressiveness of parametric transforms scales with intensity.
    """
    pool = []

    # Rotation — positions scale with intensity
    max_rot = max(1, int(max(intensity, 0.3) * num_pitched * 0.8))
    rot_pos = random.randint(1, max(1, max_rot))
    pool.append(("Rotation", {"positions": rot_pos}))

    # Structural transforms available from the start
    pool.append(("Retrograde", {}))
    pool.append(("Inversion", {}))
    pool.append(("Retrograde Inversion", {}))

    if intensity > 0.3:
        n_seg = random.choice([3, 4, 5])
        order = list(range(n_seg))
        random.shuffle(order)
        while order == list(range(n_seg)):
            random.shuffle(order)
        pool.append(("Permutation", {"num_segments": n_seg, "order": order}))

    if intensity > 0.5:
        scale_name = random.choice(["Dorian", "Phrygian", "Lydian", "Whole-tone",
                                     "Octatonic (H-W)", "Octatonic (W-H)",
                                     "Harmonic minor", "Mixolydian"])
        root = random.randint(0, 11)
        pool.append(("Pitch Quantization", {
            "scale_pcs": SCALES[scale_name], "root": root,
            "scale_name": scale_name}))

    return random.choice(pool)


def _pick_duration_transform(direction: str, intensity: float) -> Optional[Tuple[str, dict]]:
    """Pick aug or dim based on user's chosen direction. Returns None if 'none'.

    Uses only clean factors (1.5, 2.0) to avoid tuplet chaos.
    Only triggers occasionally — not every step gets a rhythm change.
    """
    if direction == "none":
        return None

    # Probability of applying a rhythm change increases with intensity
    rhythm_chance = 0.15 + intensity * 0.35  # 15% → 50%
    if random.random() > rhythm_chance:
        return None

    if direction == "augmentation":
        factor = random.choice(_CLEAN_AUG_FACTORS)
        return ("Augmentation", {"factor": factor})
    elif direction == "diminution":
        factor = random.choice(_CLEAN_DIM_FACTORS)
        return ("Diminution", {"factor": factor})
    else:
        # "both" — pick one at random
        if random.random() > 0.5:
            return ("Augmentation", {"factor": random.choice(_CLEAN_AUG_FACTORS)})
        else:
            return ("Diminution", {"factor": random.choice(_CLEAN_DIM_FACTORS)})


def run_auto_mode(score: stream.Score, source_part: stream.Part,
                   part_idx: int, notes, input_path: Path):
    """Auto-generate N variants with progressive divergence.

    Each variant transforms the previous one (not the original), so the melody
    drifts further from the source with each step. Early steps use gentle
    transforms, later steps are more aggressive. Each step applies 1-2 combined
    transforms (e.g. rotation + transposition).

    *notes* can be a flat list (single voice) or an OrderedDict of per-voice
    note lists (multi-voice). Transforms are applied independently to each
    voice; the output preserves voice separation.
    """
    multivoice = _is_voice_dict(notes)
    flat_notes = _all_notes(notes)  # flat version for info/chord derivation

    print("\n  ─── Auto-Generate Mode ───")
    print("  Progressive divergence: each variant builds on the last,")
    print("  drifting further from the original melody.\n")
    if multivoice:
        print(f"  (multi-voice input: {len(notes)} voices)\n")
    print(melody_info(flat_notes))

    num_variants = prompt_int("\n  How many variants to generate?", 10)
    if num_variants < 1:
        print("  Need at least 1 variant.")
        return

    rhythm_dir = prompt_choice("Rhythmic direction:", [
        "Augmentation — durations tend to stretch",
        "Diminution — durations tend to compress",
        "Both — mix of aug and dim",
        "None — pitch changes only, keep original rhythms",
    ])
    if rhythm_dir.startswith("Aug"):
        rhythm_dir = "augmentation"
    elif rhythm_dir.startswith("Dim"):
        rhythm_dir = "diminution"
    elif rhythm_dir.startswith("Both"):
        rhythm_dir = "both"
    else:
        rhythm_dir = "none"

    include_semitone = prompt_confirm(
        "Include semitone-quantized (12-TET) doubles?", default=True)

    output_mode = prompt_choice("Output format:", [
        "Multi-voice — each variant on a separate staff",
        "Chain — all variants sequentially on one staff",
    ])

    num_pitched = len(get_pitched_indices(flat_notes))

    # Capture original melody's register for octave-folding later
    orig_pitched = [n for n in flat_notes if is_pitched(n) and not is_grace(n)]
    if orig_pitched:
        orig_ps = [n.pitch.ps for n in orig_pitched]
        _ref_lo = min(orig_ps)
        _ref_hi = max(orig_ps)
    else:
        _ref_lo, _ref_hi = 60.0, 72.0

    print(f"\n  Generating {num_variants} variants (progressive divergence)...\n")

    # Build variants — each one transforms the previous variant's *base* material.
    # We track both 'current_base' (the melody without elongation, which feeds
    # into the next variant) and the full elongated version (which goes into output).
    #
    # For multi-voice: notes/current_base/working are all voice dicts;
    # the flat version is derived when needed for chord summaries.
    variants = [("Original", notes, False)]
    current_base = notes          # base material for progressive transforms
    orig_length = _voice_len(notes)  # original note count — elongation is relative
    cumulative_labels = []
    any_dur_changed = False

    for i in range(num_variants):
        # Square-root intensity curve: changes are strong early, then level off.
        # Variant 1/8 → 0.38,  2/8 → 0.53,  4/8 → 0.76,  7/8 → 0.94,  8/8 → 1.0
        intensity = math.sqrt(i / max(1, num_variants - 1))

        steps = []

        # ── Structural transform first (RI, I, R, Rot) — the backbone ──
        # Always apply one on early variants to get moving quickly.
        # Probability stays high throughout.
        if random.random() < 0.85:
            sec = _pick_secondary_transform(intensity, num_pitched)
            steps.append(sec)

        # ── Transposition: every step gets one, range grows with intensity ──
        max_semi = 2 + int(intensity * 10)  # 2..12 semitones
        semi = random.choice([-1, 1]) * random.randint(1, max_semi)
        if intensity > 0.4 and random.random() < 0.3:
            semi += random.choice([-0.5, 0.5])
        steps.append(("Transposition", {"semitones": semi}))

        # ── Expansion: most steps get one, factor grows gently with intensity ──
        if random.random() < 0.7 + intensity * 0.25:
            exp_factor = 1.02 + intensity * 0.18  # 1.02 → 1.2
            steps.append(("Pitch Expansion", {"factor": exp_factor}))

        # ── Maybe add a rhythm change ──
        dur_step = _pick_duration_transform(rhythm_dir, intensity)
        if dur_step:
            steps.append(dur_step)

        # Apply all steps to the current base (previous variant's non-elongated notes).
        # For multi-voice: each transform is applied independently per voice.
        working = current_base
        step_labels = []
        step_dur = False
        for name, params in steps:
            if multivoice:
                new_working = {}
                for vid, vnotes in working.items():
                    w, label, dur = apply_transform(name, vnotes, params)
                    new_working[vid] = w
                    step_dur = step_dur or dur
                working = new_working
                step_labels.append(label)  # label is same for all voices
            else:
                working, label, dur = apply_transform(name, working, params)
                step_labels.append(label)
                step_dur = step_dur or dur

        # Fold pitches back toward the original register
        _for_each_voice(working,
                        lambda w: _fold_to_range(w, _ref_lo, _ref_hi, max_extra=12.0))

        # Save the base (non-elongated) version for the NEXT variant to build on.
        # This prevents length from compounding: each variant transforms original-
        # length material, then elongation only applies to the OUTPUT.
        current_base = copy.deepcopy(working)

        # ── Smooth line elongation ──
        # Target length grows continuously with intensity, from 1× at the
        # start to up to 4× at maximum intensity.  The cubic ramp keeps
        # early variants at original length and elongates smoothly:
        #
        # The target multiplier is:  1.0  +  intensity³ × 3.0
        #   intensity 0.00 → 1.0×   (no elongation)
        #   intensity 0.38 → 1.16×  (below threshold, no elongation)
        #   intensity 0.53 → 1.46×  (first slight elongation)
        #   intensity 0.65 → 1.84×
        #   intensity 0.76 → 2.30×
        #   intensity 0.85 → 2.81×
        #   intensity 1.00 → 4.0×
        #
        # We only start elongating once the target exceeds 1.3× to avoid
        # tiny tail-fragments on early variants.
        target_mult = 1.0 + (intensity ** 3) * 3.0
        base_len = _voice_len(working)
        target_len = int(base_len * target_mult)

        if target_len > int(base_len * 1.3):
            remaining = target_len - base_len
            while remaining > 0:
                seg_transform = _pick_secondary_transform(intensity, num_pitched)
                if multivoice:
                    # Elongate each voice with the same transform
                    seg_dict = {}
                    for vid, vbase in current_base.items():
                        seg_notes, seg_label, seg_dur = apply_transform(
                            seg_transform[0], vbase, seg_transform[1])
                        seg_semi = random.choice([-1, 1]) * random.randint(
                            1, max(1, int(intensity * 6)))
                        seg_notes = apply_transposition(seg_notes, seg_semi)
                        _clamp_pitches(seg_notes)
                        _fold_to_range(seg_notes, _ref_lo, _ref_hi, max_extra=12.0)
                        if len(seg_notes) > remaining:
                            seg_notes = seg_notes[:remaining]
                        seg_dict[vid] = seg_notes
                    # Concatenate per-voice
                    for vid in working:
                        working[vid] = working[vid] + seg_dict[vid]
                    step_labels.append(f"×{seg_label}")
                    step_dur = True
                    remaining -= len(seg_dict[next(iter(seg_dict))])
                else:
                    # Transform the BASE (not the growing concatenation)
                    seg_notes, seg_label, seg_dur = apply_transform(
                        seg_transform[0], current_base, seg_transform[1])
                    # Transpose each extra segment for variety
                    seg_semi = random.choice([-1, 1]) * random.randint(
                        1, max(1, int(intensity * 6)))
                    seg_notes = apply_transposition(seg_notes, seg_semi)
                    _clamp_pitches(seg_notes)
                    _fold_to_range(seg_notes, _ref_lo, _ref_hi, max_extra=12.0)

                    # Trim the segment if it would overshoot the target length
                    if len(seg_notes) > remaining:
                        seg_notes = seg_notes[:remaining]

                    working = working + seg_notes  # concatenate (list addition)
                    step_labels.append(f"×{seg_label}")
                    step_dur = True  # offsets must be recomputed
                    remaining -= len(seg_notes)

        any_dur_changed = any_dur_changed or step_dur
        step_label = "+".join(step_labels)
        cumulative_labels.append(step_label)

        # Always treat as duration_changed in auto mode — original offsets
        # are meaningless after progressive transforms (rotation, permutation, etc.)
        variants.append((step_label, working, True))
        print(f"    {i + 1:>2}. {step_label}  (intensity {intensity:.0%})")

    # Get bar length and original time signature
    ts_list = source_part.recurse().getElementsByClass(meter.TimeSignature)
    orig_ts = copy.deepcopy(ts_list[0]) if ts_list else meter.TimeSignature('4/4')
    bar_ql = float(orig_ts.barDuration.quarterLength)

    if output_mode.startswith("Multi"):
        out_score = stream.Score()
        summaries = [derive_summary_chord(_all_notes(v[1]), num_tones=4)
                     for v in variants]

        part_num = 0
        for i, (label, vnotes, dur_changed) in enumerate(variants):
            next_summary = summaries[i + 1] if i + 1 < len(summaries) else summaries[0]
            flat_v = _all_notes(vnotes)

            if multivoice:
                # Build per-voice note lists with chord passage appended
                voice_with_chord = {}
                for vid, vn in vnotes.items():
                    nwc, _ = _build_notes_with_chord(
                        vn, bar_ql, dur_changed, next_summary=next_summary,
                        original_ts=orig_ts)
                    voice_with_chord[vid] = nwc
                part_num += 1
                new_part = rebuild_part_multivoice(
                    source_part, voice_with_chord, dur_changed)
            else:
                notes_with_chord, _ = _build_notes_with_chord(
                    vnotes, bar_ql, dur_changed, next_summary=next_summary,
                    original_ts=orig_ts)
                part_num += 1
                new_part = rebuild_part(source_part, notes_with_chord, dur_changed)

            new_part.partName = label
            new_part.id = f"P{part_num}"
            out_score.insert(0, new_part)

            if include_semitone:
                qt_flat = quantize_to_semitone(flat_v)
                qt_next = quantize_chord_to_semitone(next_summary)
                qt_notes_with_chord, _ = _build_notes_with_chord(
                    qt_flat, bar_ql, dur_changed, next_summary=qt_next,
                    original_ts=orig_ts, snap_to_semitone=True)
                part_num += 1
                qt_part = rebuild_part(source_part, qt_notes_with_chord, dur_changed)
                qt_part.partName = f"{label} (12-TET)"
                qt_part.id = f"P{part_num}"
                out_score.insert(0, qt_part)

    else:
        out_score = stream.Score()
        summaries = [derive_summary_chord(_all_notes(v[1]), num_tones=4)
                     for v in variants]

        if multivoice:
            # Build per-voice sequential note lists in parallel
            voice_ids = list(notes.keys())
            voice_all = {vid: [] for vid in voice_ids}
            voice_ql  = {vid: 0.0 for vid in voice_ids}
            is_first = True

            for i, (label, stage_notes, dur_changed) in enumerate(variants):
                next_sum = summaries[i + 1] if i + 1 < len(summaries) else summaries[0]
                for vid in voice_ids:
                    if _is_voice_dict(stage_notes):
                        v_stage = stage_notes[vid]
                    else:
                        v_stage = stage_notes
                    voice_ql[vid] = _append_stage_to_sequence(
                        voice_all[vid], voice_ql[vid], v_stage, bar_ql, is_first,
                        next_summary=next_sum, original_ts=orig_ts)
                is_first = False

                if include_semitone:
                    qt_next = quantize_chord_to_semitone(next_sum)
                    for vid in voice_ids:
                        if _is_voice_dict(stage_notes):
                            v_stage = stage_notes[vid]
                        else:
                            v_stage = stage_notes
                        qt_notes = quantize_to_semitone(v_stage)
                        voice_ql[vid] = _append_stage_to_sequence(
                            voice_all[vid], voice_ql[vid], qt_notes, bar_ql, False,
                            next_summary=qt_next, original_ts=orig_ts,
                            snap_to_semitone=True)

            from collections import OrderedDict
            voice_dict_out = OrderedDict((vid, voice_all[vid]) for vid in voice_ids)
            new_part = rebuild_part_multivoice(
                source_part, voice_dict_out, duration_changed=True)
        else:
            all_notes = []
            running_ql = 0.0
            is_first = True

            for i, (label, stage_notes, dur_changed) in enumerate(variants):
                flat_stage = _all_notes(stage_notes)
                next_sum = summaries[i + 1] if i + 1 < len(summaries) else summaries[0]
                running_ql = _append_stage_to_sequence(
                    all_notes, running_ql, flat_stage, bar_ql, is_first,
                    next_summary=next_sum, original_ts=orig_ts)
                is_first = False

                if include_semitone:
                    qt_notes = quantize_to_semitone(flat_stage)
                    qt_next = quantize_chord_to_semitone(next_sum)
                    running_ql = _append_stage_to_sequence(
                        all_notes, running_ql, qt_notes, bar_ql, False,
                        next_summary=qt_next, original_ts=orig_ts,
                        snap_to_semitone=True)

            new_part = rebuild_part(source_part, all_notes, duration_changed=True)

        new_part.partName = f"Auto ({num_variants} variants)"
        out_score.insert(0, new_part)

    out_path = _next_output_path(input_path, f"auto_{num_variants}v")
    write_musicxml(out_score, out_path)
    print(f"\n  ✓ Written {len(variants)} variants ({num_variants} + original) to:")
    print(f"    {out_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# Harmonize Mode
# ═══════════════════════════════════════════════════════════════════════════════

def _split_into_fragments(notes: List, part: stream.Part,
                           min_rest_gap: float = 1.0) -> List[List]:
    """Split a note list into fragments separated by rest gaps.

    Walks through the note list and accumulates rest durations.  When the
    accumulated rest exceeds *min_rest_gap* quarter-note beats, the current
    fragment is closed and a new one begins.

    Returns a list of fragment note-lists (rests are discarded — only pitched
    notes and grace notes are kept in each fragment).
    """
    fragments: List[List] = []
    current: List = []
    rest_accum = 0.0

    for el in notes:
        if el.isRest:
            rest_accum += float(el.quarterLength)
        else:
            if rest_accum >= min_rest_gap and current:
                fragments.append(current)
                current = []
            rest_accum = 0.0
            current.append(el)
    if current:
        fragments.append(current)

    return fragments


def _fragment_offset_range(frag: List, part: stream.Part):
    """Return (start_offset, end_offset) of a fragment in the Part."""
    first = frag[0]
    last = frag[-1]
    start = getattr(first, '_original_offset', None)
    if start is None:
        start = first.getOffsetInHierarchy(part)
    end = getattr(last, '_original_offset', None)
    if end is None:
        end = last.getOffsetInHierarchy(part)
    end += float(last.quarterLength)
    return start, end


def _clean_alters_for_music21(input_path: Path) -> Path:
    """Create a cleaned copy of a MusicXML file with alter values snapped to
    nearest quarter-tone (0.5 steps) for music21 compatibility.

    Dorico exports non-standard alter values (-0.29, 0.29, 0.872, 1.453)
    that music21 cannot parse. This snaps them to the nearest 0.5.
    Returns the path to the cleaned file.
    """
    tree = ET.parse(str(input_path))
    root = tree.getroot()

    # Handle possible namespace
    tag = root.tag
    ns = ""
    if tag.startswith("{"):
        ns = tag.split("}")[0] + "}"

    changed = 0
    for alter_el in root.iter(f"{ns}alter"):
        try:
            val = float(alter_el.text)
            snapped = round(val * 2) / 2  # nearest 0.5
            if abs(val - snapped) > 0.01:
                alter_el.text = str(snapped)
                changed += 1
        except (ValueError, TypeError):
            pass

    clean_path = input_path.parent / (input_path.stem + "_clean" + input_path.suffix)
    tree.write(str(clean_path), xml_declaration=True, encoding="UTF-8")
    if changed:
        print(f"  Cleaned {changed} non-standard alter values for analysis.")
    return clean_path


def _apply_pitch_to_xml_note(xml_note, m21_note):
    """Replace pitch in an XML note element with a music21 note's pitch.

    Clones the XML element, preserves all structure (duration, type, dot,
    beam, stem, staff, voice), and only swaps the <pitch> element.
    Removes any <accidental> elements (Dorico infers from <alter>).
    """
    new_note = copy.deepcopy(xml_note)
    old_pitch = new_note.find('pitch')
    if old_pitch is None:
        return new_note  # rest — return as-is

    new_pitch = ET.Element('pitch')
    ET.SubElement(new_pitch, 'step').text = m21_note.pitch.step
    alter = m21_note.pitch.accidental.alter if m21_note.pitch.accidental else 0
    if alter != 0:
        if alter == int(alter):
            ET.SubElement(new_pitch, 'alter').text = str(int(alter))
        else:
            ET.SubElement(new_pitch, 'alter').text = str(round(alter, 3))
    ET.SubElement(new_pitch, 'octave').text = str(m21_note.pitch.octave)

    pitch_idx = list(new_note).index(old_pitch)
    new_note.remove(old_pitch)
    new_note.insert(pitch_idx, new_pitch)

    for acc in new_note.findall('accidental'):
        new_note.remove(acc)
    return new_note


def _build_xml_chord_note(m21_pitch_obj, duration_divisions, note_type, is_chord=False):
    """Build a MusicXML <note> element for a chord tone.

    Args:
        m21_pitch_obj: music21 pitch.Pitch object
        duration_divisions: integer duration in divisions (e.g. 4 = quarter note at div=4)
        note_type: MusicXML type string (e.g. 'whole', 'half', 'quarter')
        is_chord: if True, adds <chord/> tag (for 2nd+ notes in a chord)
    """
    note_el = ET.Element('note')
    if is_chord:
        ET.SubElement(note_el, 'chord')

    pitch_el = ET.SubElement(note_el, 'pitch')
    ET.SubElement(pitch_el, 'step').text = m21_pitch_obj.step
    alter = m21_pitch_obj.accidental.alter if m21_pitch_obj.accidental else 0
    if alter != 0:
        if alter == int(alter):
            ET.SubElement(pitch_el, 'alter').text = str(int(alter))
        else:
            ET.SubElement(pitch_el, 'alter').text = str(round(alter, 3))
    ET.SubElement(pitch_el, 'octave').text = str(m21_pitch_obj.octave)

    ET.SubElement(note_el, 'duration').text = str(duration_divisions)
    ET.SubElement(note_el, 'voice').text = '1'
    ET.SubElement(note_el, 'type').text = note_type
    ET.SubElement(note_el, 'staff').text = '1'
    return note_el


def _build_xml_rest(duration_divisions, note_type):
    """Build a MusicXML <note> element for a rest."""
    note_el = ET.Element('note')
    ET.SubElement(note_el, 'rest')
    ET.SubElement(note_el, 'duration').text = str(duration_divisions)
    ET.SubElement(note_el, 'voice').text = '1'
    ET.SubElement(note_el, 'type').text = note_type
    ET.SubElement(note_el, 'staff').text = '1'
    return note_el


def _ql_to_dur_and_type(ql, divisions=4):
    """Convert a quarterLength to (duration_in_divisions, note_type) pairs.

    Returns a list of (dur, type) tuples to handle values that need
    multiple tied notes. For simple values returns a single tuple.
    divisions is the MusicXML divisions-per-quarter value.
    """
    dur = int(round(ql * divisions))
    # Map duration in divisions to MusicXML type (assuming divisions=4)
    type_map = {
        1: '16th', 2: 'eighth', 3: 'eighth',  # dotted eighth
        4: 'quarter', 6: 'quarter',  # dotted quarter
        8: 'half', 12: 'half',  # dotted half
        16: 'whole',
    }
    # Find best fit
    if dur in type_map:
        return [(dur, type_map[dur])]
    # For odd values, use the largest fitting duration + remainder
    result = []
    remaining = dur
    for d in [16, 12, 8, 6, 4, 3, 2, 1]:
        while remaining >= d and d in type_map:
            result.append((d, type_map[d]))
            remaining -= d
    if not result:
        result = [(dur, 'quarter')]  # fallback
    return result


def _collect_xml_notes(part_el):
    """Collect all <note> elements from a part, in measure order,
    along with their measure number. Returns list of (measure_num, note_el).

    Skips <forward>, <backup>, <direction>, <attributes> etc.
    Only collects actual <note> elements (pitched and rests).
    """
    results = []
    for measure in part_el.findall('measure'):
        mnum = measure.get('number', '0')
        for note_el in measure.findall('note'):
            results.append((mnum, note_el))
    return results


def run_harmonize_mode(score: stream.Score, source_part: stream.Part,
                        part_idx: int, notes, input_path: Path):
    """Harmonize mode: original melody + harmonic-average chords + auto-generated variants.

    Uses raw XML manipulation to preserve the original MusicXML structure.
    music21 is used ONLY for analysis (fragment detection, chord derivation,
    pitch transforms). Output is written directly with ElementTree.

    Builds:
      Part 1:  Original melody (byte-for-byte from input)
      Part 1h: Harmonic average chords under original
      Parts 2..N: Auto-generated progressive variants (melody + harmony pair each)
    """
    flat_notes = _all_notes(notes) if _is_voice_dict(notes) else notes

    print("\n  ─── Harmonize Mode (Raw XML) ───")
    print("  Original melody + harmonic average + auto-generated variants.")
    print("  Output uses raw XML to preserve original notation.\n")
    print(melody_info(flat_notes))

    # --- Fragment detection ---
    gap = prompt_float("Minimum rest gap to split fragments (quarter-notes)", 1.0)
    fragments = _split_into_fragments(flat_notes, source_part, min_rest_gap=gap)
    print(f"\n  Detected {len(fragments)} fragments:")
    for i, frag in enumerate(fragments):
        pitched = [n for n in frag if is_pitched(n)]
        dur = sum(float(n.quarterLength) for n in frag)
        print(f"    {i+1:2d}. {len(pitched):3d} pitched notes, {dur:5.1f} ql")

    num_tones = prompt_int("Summary chord tones (2, 3, or 4)", 4)
    if num_tones not in (2, 3, 4):
        num_tones = 4

    # --- Auto-generate parameters ---
    num_variants = prompt_int("\n  How many variants to generate?", 6)
    if num_variants < 1:
        print("  Need at least 1 variant.")
        return

    rhythm_dir = prompt_choice("Rhythmic direction:", [
        "Augmentation — durations tend to stretch",
        "Diminution — durations tend to compress",
        "Both — mix of aug and dim",
        "None — pitch changes only, keep original rhythms",
    ])
    if rhythm_dir.startswith("Aug"):
        rhythm_dir = "augmentation"
    elif rhythm_dir.startswith("Dim"):
        rhythm_dir = "diminution"
    elif rhythm_dir.startswith("Both"):
        rhythm_dir = "both"
    else:
        rhythm_dir = "none"

    # --- Derive fragment chords and offset ranges for original ---
    frag_chord_data = []  # (start_offset, end_offset, chord) per fragment
    for frag in fragments:
        start, end = _fragment_offset_range(frag, source_part)
        summary = derive_summary_chord(frag, num_tones=num_tones)
        frag_chord_data.append((start, end, summary))

    # --- Reference range for octave folding ---
    num_pitched = len(get_pitched_indices(flat_notes))
    orig_pitched = [n for n in flat_notes if is_pitched(n) and not is_grace(n)]
    if orig_pitched:
        orig_ps = [n.pitch.ps for n in orig_pitched]
        ref_lo = min(orig_ps)
        ref_hi = max(orig_ps)
    else:
        ref_lo, ref_hi = 60.0, 72.0

    # --- Generate variants with progressive divergence ---
    print(f"\n  Generating {num_variants} variants (progressive divergence)...\n")

    variants = []  # (label, transformed_notes_list)
    current_base = copy.deepcopy(flat_notes)

    for i in range(num_variants):
        intensity = math.sqrt(i / max(1, num_variants - 1))

        steps = []

        # Structural transform (85% chance)
        if random.random() < 0.85:
            sec = _pick_secondary_transform(intensity, num_pitched)
            steps.append(sec)

        # Transposition: every step, range grows with intensity
        max_semi = 2 + int(intensity * 10)
        semi = random.choice([-1, 1]) * random.randint(1, max_semi)
        if intensity > 0.4 and random.random() < 0.3:
            semi += random.choice([-0.5, 0.5])
        steps.append(("Transposition", {"semitones": semi}))

        # Expansion (70%+ chance)
        if random.random() < 0.7 + intensity * 0.25:
            exp_factor = 1.02 + intensity * 0.18
            steps.append(("Pitch Expansion", {"factor": exp_factor}))

        # Maybe add a rhythm change
        dur_step = _pick_duration_transform(rhythm_dir, intensity)
        if dur_step:
            steps.append(dur_step)

        # Apply all steps
        working = current_base
        step_labels = []
        for name, params in steps:
            working, label, dur = apply_transform(name, working, params)
            step_labels.append(label)

        # Fold pitches back toward original register
        _fold_to_range(working, ref_lo, ref_hi, max_extra=12.0)
        _clamp_pitches(working)

        # Save base for next variant
        current_base = copy.deepcopy(working)

        # --- Smooth line elongation ---
        target_mult = 1.0 + (intensity ** 3) * 3.0
        base_len = _voice_len(working)
        target_len = int(base_len * target_mult)

        if target_len > int(base_len * 1.3):
            remaining = target_len - base_len
            while remaining > 0:
                seg_transform = _pick_secondary_transform(intensity, num_pitched)
                seg_notes, seg_label, seg_dur = apply_transform(
                    seg_transform[0], current_base, seg_transform[1])
                seg_semi = random.choice([-1, 1]) * random.randint(
                    1, max(1, int(intensity * 6)))
                seg_notes = apply_transposition(seg_notes, seg_semi)
                _clamp_pitches(seg_notes)
                _fold_to_range(seg_notes, ref_lo, ref_hi, max_extra=12.0)
                if len(seg_notes) > remaining:
                    seg_notes = seg_notes[:remaining]
                working = working + seg_notes
                step_labels.append(f"x{seg_label}")
                remaining -= len(seg_notes)

        step_label = "+".join(step_labels)
        variants.append((step_label, working))
        print(f"    {i + 1:>2}. {step_label}  (intensity {intensity:.0%})")

    # ═══════════════════════════════════════════════════════════════════════
    # Build output XML using raw ElementTree (no music21 roundtrip)
    # ═══════════════════════════════════════════════════════════════════════

    print("\n  Building output XML...")

    tree = ET.parse(str(input_path))
    root = tree.getroot()

    # --- Locate the original part and part-list ---
    part_list = root.find('part-list')
    orig_part_el = root.find('part')  # first (only) part
    orig_part_id = orig_part_el.get('id', 'P1')

    # Collect original XML notes for cloning into variant parts
    orig_xml_notes = []
    for measure in orig_part_el.findall('measure'):
        for note_el in measure.findall('note'):
            # Skip non-pitched (rests) for cloning purposes — we track all
            orig_xml_notes.append(note_el)

    # Build a parallel list: for each original XML note, the corresponding
    # music21 note (from flat_notes). We need to align them.
    # flat_notes includes both pitched and rests in order.
    # orig_xml_notes includes both pitched and rests in order.
    # They should be 1:1 aligned.

    # --- Get time signature from XML ---
    divisions = 4  # from the input file
    bar_ql = 4.0   # 4/4 time
    first_attrs = orig_part_el.find('.//attributes')
    if first_attrs is not None:
        div_el = first_attrs.find('divisions')
        if div_el is not None:
            divisions = int(div_el.text)
        time_el = first_attrs.find('time')
        if time_el is not None:
            beats = int(time_el.find('beats').text)
            beat_type = int(time_el.find('beat-type').text)
            bar_ql = beats * (4.0 / beat_type)

    # === Part 1: Original melody (verbatim — already in the tree) ===
    # Rename it
    for sp in part_list.findall('score-part'):
        if sp.get('id') == orig_part_id:
            pn = sp.find('part-name')
            if pn is not None:
                pn.text = 'Original'
            break

    # === Part 1h: Harmonic average for original ===
    _add_harmony_part_xml(root, part_list, 'P1h', 'Original Harmony',
                          frag_chord_data, bar_ql, divisions, orig_part_el)

    # === Variant parts: each gets a melody + harmony pair ===
    for vi, (label, vnotes) in enumerate(variants):
        mel_id = f'P{vi + 2}'
        har_id = f'P{vi + 2}h'

        # Build variant melody part by cloning original XML notes and
        # swapping pitches with the transformed music21 notes.
        _add_variant_melody_part_xml(
            root, part_list, mel_id, label,
            orig_part_el, orig_xml_notes, flat_notes, vnotes, divisions)

        # Build variant harmony part
        # Re-derive chords from the variant's notes, using same fragment structure
        var_frag_chords = _derive_variant_frag_chords(
            vnotes, flat_notes, frag_chord_data, source_part, num_tones)
        _add_harmony_part_xml(root, part_list, har_id, f'{label} Harm',
                              var_frag_chords, bar_ql, divisions, orig_part_el)

    # --- Write output ---
    out_path = _next_output_path(input_path, "harm")
    tree.write(str(out_path), xml_declaration=True, encoding="UTF-8")

    # Post-process: strip <accidental> elements and ensure <voice> tags
    _postprocess_xml(out_path)

    total_parts = 2 + num_variants * 2  # original + orig_harm + N*(mel+harm)
    print(f"\n  Written harmonize output ({total_parts} parts) to:\n    {out_path}")


def _postprocess_xml(out_path: Path):
    """Post-process output XML: strip <accidental> elements, ensure <voice> tags."""
    tree = ET.parse(str(out_path))
    root = tree.getroot()

    ns = ""
    tag = root.tag
    if tag.startswith("{"):
        ns = tag.split("}")[0] + "}"

    # Strip <accidental> elements
    for note_el in root.iter(f"{ns}note"):
        for acc_el in note_el.findall(f"{ns}accidental"):
            note_el.remove(acc_el)

    tree.write(str(out_path), xml_declaration=True, encoding="UTF-8")


def _add_harmony_part_xml(root, part_list, part_id, part_name,
                           frag_chord_data, bar_ql, divisions, ref_part_el):
    """Add a harmony part to the XML tree with held chords aligned to fragments.

    frag_chord_data: list of (start_ql, end_ql, music21_chord) tuples.
    Each chord is held for the fragment's duration; gaps become rests.
    Packs notes into measures of bar_ql quarter-lengths.
    """
    # Add to part-list
    sp = ET.SubElement(part_list, 'score-part', id=part_id)
    pn = ET.SubElement(sp, 'part-name')
    pn.text = part_name

    # Create part element
    part_el = ET.SubElement(root, 'part', id=part_id)

    # Build a flat list of events: (offset_ql, duration_ql, chord_or_rest)
    events = []
    prev_end = 0.0
    for start, end, chord_obj in frag_chord_data:
        # Gap rest
        gap = start - prev_end
        if gap > 0.01:
            events.append(('rest', prev_end, gap))
        # Chord
        events.append(('chord', start, end - start, chord_obj))
        prev_end = end

    # Trailing rest — get total duration from reference part
    total_dur = 0.0
    for m in ref_part_el.findall('measure'):
        for n in m.findall('note'):
            dur_el = n.find('duration')
            if dur_el is not None and n.find('chord') is None:
                total_dur += int(dur_el.text) / divisions
    if prev_end < total_dur - 0.01:
        events.append(('rest', prev_end, total_dur - prev_end))

    # Now pack events into measures
    measure_num = 1
    current_offset = 0.0  # offset within current measure

    measure_el = ET.SubElement(part_el, 'measure', number=str(measure_num))
    # First measure gets attributes
    attrs = ET.SubElement(measure_el, 'attributes')
    ET.SubElement(attrs, 'divisions').text = str(divisions)
    # Copy time signature from reference
    ref_time = ref_part_el.find('.//time')
    if ref_time is not None:
        attrs.append(copy.deepcopy(ref_time))
    ref_clef = ref_part_el.find('.//clef')
    if ref_clef is not None:
        attrs.append(copy.deepcopy(ref_clef))

    # Add mf dynamic on first chord
    added_dynamic = False

    for event in events:
        if event[0] == 'rest':
            _, offset, dur = event
            remaining = dur
            while remaining > 0.01:
                space_in_bar = bar_ql - current_offset
                chunk = min(remaining, space_in_bar)
                if chunk > 0.01:
                    dur_divs = int(round(chunk * divisions))
                    parts = _ql_to_dur_and_type(chunk, divisions)
                    for d, t in parts:
                        measure_el.append(_build_xml_rest(d, t))
                    current_offset += chunk
                remaining -= chunk
                if current_offset >= bar_ql - 0.01:
                    current_offset = 0.0
                    measure_num += 1
                    measure_el = ET.SubElement(part_el, 'measure',
                                               number=str(measure_num))
        else:
            _, offset, dur, chord_obj = event
            remaining = dur
            pitches = chord_obj.pitches
            is_first_chunk = True
            while remaining > 0.01:
                space_in_bar = bar_ql - current_offset
                chunk = min(remaining, space_in_bar)
                if chunk > 0.01:
                    dur_divs = int(round(chunk * divisions))
                    parts = _ql_to_dur_and_type(chunk, divisions)
                    for d, t in parts:
                        for pi, p in enumerate(pitches):
                            n = _build_xml_chord_note(p, d, t, is_chord=(pi > 0))
                            measure_el.append(n)
                    # Add dynamic on first chord
                    if not added_dynamic and is_first_chunk:
                        direction = ET.SubElement(measure_el, 'direction',
                                                   placement='below')
                        dt = ET.SubElement(direction, 'direction-type')
                        dynamics = ET.SubElement(dt, 'dynamics')
                        ET.SubElement(dynamics, 'mf')
                        added_dynamic = True
                    is_first_chunk = False
                    current_offset += chunk
                remaining -= chunk
                if current_offset >= bar_ql - 0.01:
                    current_offset = 0.0
                    measure_num += 1
                    measure_el = ET.SubElement(part_el, 'measure',
                                               number=str(measure_num))


def _add_variant_melody_part_xml(root, part_list, part_id, part_name,
                                  ref_part_el, orig_xml_notes, orig_m21_notes,
                                  variant_m21_notes, divisions):
    """Add a variant melody part by cloning original XML note structure and
    replacing pitches with the variant's transformed pitches.

    For notes within the original length: clone the original XML note and
    swap its pitch with the variant's music21 note pitch.
    For elongation notes (beyond original length): create new XML notes
    with quarter-note duration and the variant's pitch.
    """
    # Add to part-list
    sp = ET.SubElement(part_list, 'score-part', id=part_id)
    pn = ET.SubElement(sp, 'part-name')
    pn.text = part_name

    # Create part element
    part_el = ET.SubElement(root, 'part', id=part_id)

    # Get original measure structure
    orig_measures = ref_part_el.findall('measure')
    bar_ql = 4.0  # 4/4 assumed
    first_attrs = ref_part_el.find('.//attributes')
    if first_attrs is not None:
        time_el = first_attrs.find('time')
        if time_el is not None:
            beats = int(time_el.find('beats').text)
            beat_type = int(time_el.find('beat-type').text)
            bar_ql = beats * (4.0 / beat_type)

    # Index into variant notes
    var_idx = 0
    num_orig = len(orig_xml_notes)
    num_var = len(variant_m21_notes)

    # Phase 1: Clone original measures, swapping pitches
    xml_note_idx = 0
    for mi, orig_meas in enumerate(orig_measures):
        meas_el = ET.SubElement(part_el, 'measure',
                                 number=orig_meas.get('number', str(mi + 1)))

        for child in orig_meas:
            if child.tag == 'note':
                if var_idx < num_var:
                    var_note = variant_m21_notes[var_idx]
                    if is_pitched(var_note):
                        # Clone and swap pitch
                        new_note = _apply_pitch_to_xml_note(child, var_note)
                        meas_el.append(new_note)
                    else:
                        # Rest in variant — clone original as rest
                        new_note = copy.deepcopy(child)
                        # Convert to rest if original was pitched
                        p = new_note.find('pitch')
                        if p is not None:
                            new_note.remove(p)
                            ET.SubElement(new_note, 'rest')
                        meas_el.append(new_note)
                    var_idx += 1
                else:
                    # No more variant notes — skip
                    pass
                xml_note_idx += 1
            elif child.tag in ('attributes', 'direction', 'print',
                               'barline', 'sound'):
                meas_el.append(copy.deepcopy(child))
            elif child.tag in ('backup', 'forward'):
                # Only include if we're still writing notes
                if var_idx <= num_var:
                    meas_el.append(copy.deepcopy(child))

    # Phase 2: Elongation — add new measures for extra notes
    if var_idx < num_var:
        current_offset = 0.0
        measure_num = len(orig_measures) + 1
        meas_el = ET.SubElement(part_el, 'measure', number=str(measure_num))
        # Add attributes for new measures
        attrs = ET.SubElement(meas_el, 'attributes')
        ET.SubElement(attrs, 'divisions').text = str(divisions)

        while var_idx < num_var:
            var_note = variant_m21_notes[var_idx]
            ql = float(var_note.quarterLength) if hasattr(var_note, 'quarterLength') else 1.0
            if ql <= 0:
                ql = 1.0  # fallback for grace notes etc.

            remaining = ql
            while remaining > 0.01:
                space_in_bar = bar_ql - current_offset
                chunk = min(remaining, space_in_bar)
                if chunk > 0.01:
                    dur_divs = int(round(chunk * divisions))
                    parts = _ql_to_dur_and_type(chunk, divisions)
                    for d, t in parts:
                        if is_pitched(var_note):
                            n = _build_xml_chord_note(var_note.pitch, d, t)
                            meas_el.append(n)
                        else:
                            meas_el.append(_build_xml_rest(d, t))
                    current_offset += chunk
                remaining -= chunk
                if current_offset >= bar_ql - 0.01:
                    current_offset = 0.0
                    measure_num += 1
                    meas_el = ET.SubElement(part_el, 'measure',
                                             number=str(measure_num))

            var_idx += 1


def _derive_variant_frag_chords(variant_notes, orig_notes, orig_frag_data,
                                 source_part, num_tones):
    """Derive fragment-aligned chords for a variant by segmenting the variant
    notes according to the original fragment boundaries.

    For the base-length portion (same note count as original), segment the
    variant notes proportionally to original fragment durations.
    For elongation notes (beyond original length), split into chunks and
    derive separate chords.

    Returns list of (start_ql, end_ql, chord) tuples.
    """
    orig_total_pitched = len([n for n in orig_notes if is_pitched(n) and not is_grace(n)])
    var_pitched = [n for n in variant_notes if is_pitched(n) and not is_grace(n)]

    # Split into base portion (same count as original) and elongation
    base_pitched = var_pitched[:orig_total_pitched]
    extra_pitched = var_pitched[orig_total_pitched:]

    result = []
    var_offset = 0
    total_orig_dur = sum(e - s for s, e, _ in orig_frag_data)

    # Distribute base notes across original fragment boundaries
    for fi, (start, end, _) in enumerate(orig_frag_data):
        frag_dur = end - start
        frag_proportion = frag_dur / total_orig_dur if total_orig_dur > 0 else 1.0
        frag_note_count = max(1, int(round(len(base_pitched) * frag_proportion)))

        # Don't exceed remaining base notes
        frag_note_count = min(frag_note_count, len(base_pitched) - var_offset)
        if frag_note_count <= 0:
            continue

        frag_notes = base_pitched[var_offset:var_offset + frag_note_count]
        var_offset += frag_note_count

        chord_obj = derive_summary_chord(frag_notes, num_tones=num_tones)
        result.append((start, end, chord_obj))

    # Elongation segments — split extra notes into chunks, one chord per chunk
    if extra_pitched:
        last_end = orig_frag_data[-1][1] if orig_frag_data else 0.0
        # Split elongation into chunks of ~orig_total_pitched notes each
        chunk_size = max(10, orig_total_pitched // 2)
        running_offset = last_end
        for ci in range(0, len(extra_pitched), chunk_size):
            chunk = extra_pitched[ci:ci + chunk_size]
            if not chunk:
                break
            chunk_dur = sum(float(n.quarterLength) for n in chunk)
            if chunk_dur < 0.5:
                chunk_dur = float(len(chunk))  # fallback: 1 ql per note
            chord_obj = derive_summary_chord(chunk, num_tones=num_tones)
            result.append((running_offset, running_offset + chunk_dur, chord_obj))
            running_offset += chunk_dur

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    show_banner()

    # Get input file
    file_path = prompt_path("MusicXML input file")
    if not file_path:
        print("  No file provided. Exiting.")
        return

    # Clean up path: strip whitespace, quotes, and shell-style backslash escapes
    file_path = file_path.strip().strip("'\"")
    file_path = file_path.replace("\\ ", " ").replace("\\'", "'").replace('\\"', '"')
    input_path = Path(file_path).expanduser().resolve()
    if not input_path.exists():
        print(f"  File not found: {input_path}")
        return

    print(f"\n  Parsing: {input_path.name} ...")
    try:
        score = converter.parse(str(input_path))
    except Exception as e:
        print(f"  Failed to parse: {e}")
        return

    if not isinstance(score, stream.Score):
        # Wrap single part in a score
        s = stream.Score()
        s.insert(0, score)
        score = s

    print(f"  Found {len(score.parts)} part(s).\n")

    # Select part
    source_part, part_idx = select_part(score)

    # Extract notes — detect multi-voice input automatically
    voice_dict = extract_notes_by_voice(source_part)
    if len(voice_dict) > 1:
        print(f"\n  Detected {len(voice_dict)} voices — transforms will be applied per-voice.")
        notes = voice_dict  # pass the dict through to mode functions
        flat_notes = _all_notes(voice_dict)
    else:
        # Single voice — unwrap to flat list (no behavior change)
        notes = next(iter(voice_dict.values()))
        flat_notes = notes

    if not flat_notes:
        print("  No notes found in selected part.")
        return

    print(f"\n  Source melody:")
    print(melody_info(flat_notes))

    # Choose mode
    mode = prompt_choice("Mode:", [
        "Chain — apply transformations sequentially to one line",
        "Multi-voice — create multiple variants as separate parts",
        "Auto-generate — create N diverse variants automatically",
        "Harmonize — melody + harmonic average + transformed variants",
    ])

    if mode.startswith("Chain"):
        run_chain_mode(score, source_part, part_idx, notes, input_path)
    elif mode.startswith("Multi"):
        run_multi_voice_mode(score, source_part, part_idx, notes, input_path)
    elif mode.startswith("Harmonize"):
        run_harmonize_mode(score, source_part, part_idx, notes, input_path)
    else:
        run_auto_mode(score, source_part, part_idx, notes, input_path)

    print("\n  Done!\n")


if __name__ == "__main__":
    main()
