#!/usr/bin/env python3
"""
Interactive Partiels -> MusicXML Converter
Unified converter with selectable pitch quantization (semitone / quarter-tone / eighth-tone),
rhythmic quantization, and output mode (melodic / harmonic).

Uses questionary for rich TUI menus, with fallback to input() prompts.
"""

import json
import sys
import math
import glob
import struct
import argparse
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

# Optional rich TUI
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


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PitchInfo:
    """Pitch information for a single note."""
    step: str               # C, D, E, F, G, A, B
    alter: float            # MusicXML <alter> value (supports 0.25 increments)
    octave: int
    remaining_cents: int    # leftover after quantization
    accidental_text: str    # MusicXML <accidental> element text (or "" for natural)


@dataclass
class PartialEvent:
    """A single partial event at a point in time."""
    time: float
    frequency: float
    amplitude: float
    duration: float = 0.1
    _tie_type: str = None  # Per-partial tie: None, "start", "stop", "stop-start"


@dataclass
class TimeFrame:
    """A group of simultaneous partials at a quantized time point."""
    time: float
    partials: List[PartialEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Accidental mapping
# ---------------------------------------------------------------------------

# Quarter-tone accidentals (Tartini-style) — used for quarter-tone quantization
ACCIDENTAL_MAP_QUARTER: Dict[float, str] = {
    -2.0:  "double-flat",
    -1.5:  "three-quarters-flat",
    -1.0:  "flat",
    -0.5:  "quarter-flat",
     0.0:  "",
     0.5:  "quarter-sharp",
     1.0:  "sharp",
     1.5:  "three-quarters-sharp",
     2.0:  "double-sharp",
}

# Eighth-tone accidentals (Stein-Zimmermann / Gould arrow notation)
# Arrow accidentals: -down = lower by ~eighth-tone, -up = raise by ~eighth-tone
ACCIDENTAL_MAP_EIGHTH: Dict[float, str] = {
    -2.0:  "double-flat",
    -1.75: "flat-flat-down",       # double-flat lowered by eighth-tone
    -1.5:  "three-quarters-flat",  # standard three-quarters-flat
    -1.25: "flat-down",            # flat lowered by eighth-tone
    -1.0:  "flat",                 # standard flat
    -0.75: "flat-up",              # flat raised by eighth-tone
    -0.5:  "quarter-flat",         # standard quarter-flat
    -0.25: "natural-down",         # natural lowered by eighth-tone
     0.0:  "",                     # natural
     0.25: "natural-up",           # natural raised by eighth-tone
     0.5:  "quarter-sharp",        # standard quarter-sharp
     0.75: "sharp-down",           # sharp lowered by eighth-tone
     1.0:  "sharp",                # standard sharp
     1.25: "sharp-up",             # sharp raised by eighth-tone
     1.5:  "three-quarters-sharp", # standard three-quarters-sharp
     1.75: "double-sharp-down",    # double-sharp lowered by eighth-tone
     2.0:  "double-sharp",         # standard double-sharp
}

# Semitone mode uses standard accidentals only
ACCIDENTAL_MAP_SEMITONE: Dict[float, str] = {
    -2.0:  "double-flat",
    -1.0:  "flat",
     0.0:  "",
     1.0:  "sharp",
     2.0:  "double-sharp",
}

# Note name / base-alteration lookup (same as existing scripts)
NOTE_NAMES =       ['C', 'C', 'D', 'D', 'E', 'F', 'F', 'G', 'G', 'A', 'A', 'B']
BASE_ALTERATIONS = [ 0,   1,   0,   1,   0,   0,   1,   0,   1,   0,   1,   0 ]


# ---------------------------------------------------------------------------
# Unified converter
# ---------------------------------------------------------------------------

class UnifiedMusicXMLConverter:
    """Converts Partiels data to MusicXML with configurable quantization and modes."""

    PITCH_RESOLUTIONS = {
        "semitone":      1.0,
        "quarter-tone":  0.5,
        "eighth-tone":   0.25,
    }

    RHYTHM_GRIDS = {
        "free":           None,
        "16th":           0.25,   # fraction of a beat
        "32nd":           0.125,
        "8th-triplet":    1/3,
        "16th-triplet":   1/6,
    }

    def __init__(self,
                 title: str = "Partiels Analysis",
                 composer: str = "",
                 tempo: int = 60,
                 quantization: str = "quarter-tone",
                 mode: str = "harmonic",
                 rhythm: str = "16th",
                 max_partials: int = 8,
                 frame_duration: float = 0.25,
                 min_amplitude: float = 0.05,
                 min_frequency: float = 80.0,
                 max_frequency: float = 4186.0,
                 use_subharmonics: bool = True,
                 auto_tempo: bool = False,
                 include_semitone_staff: bool = False,
                 ensemble_only: bool = False):
        self.title = title
        self.composer = composer
        self.tempo = tempo
        self.quantization = quantization
        self.mode = mode
        self.rhythm = rhythm
        self.max_partials = max_partials
        self.frame_duration = frame_duration
        self.min_amplitude = min_amplitude
        self.min_frequency = min_frequency
        self.max_frequency = max_frequency  # C8 = top of piano
        self.use_subharmonics = use_subharmonics
        self.auto_tempo = auto_tempo
        self.include_semitone_staff = include_semitone_staff
        self.ensemble_only = ensemble_only
        self.disable_ties = False  # Set True for SDIF input (discrete chords)

        # Dynamics calibration (set from data by _calibrate_dynamics)
        self._amp_min = 0.001
        self._amp_max = 1.0
        self._vel_min = 20    # ppp
        self._vel_max = 120   # fff
        self._vel_center = 70 # mf fallback

        # 768 divisions per quarter note — evenly divisible by 2, 3, 4, 6, 8, 12,
        # 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 768.
        # This ensures triplet durations are exact integers and sum correctly.
        self.divisions = 768
        self._pitch_resolution = self.PITCH_RESOLUTIONS[quantization]
        self._grid_size = self._compute_grid_size()

    # -- grid helpers -------------------------------------------------------

    def _compute_grid_size(self) -> int:
        """Grid size in divisions."""
        beat_frac = self.RHYTHM_GRIDS.get(self.rhythm)
        if beat_frac is None:
            # Free mode: snap to 16th-note grid for readable notation
            # while still capturing proportional timing differences.
            return self.divisions // 4  # 192 divs = 16th note
        return max(1, round(self.divisions * beat_frac))

    def _quantize_divs(self, raw_divs: int) -> int:
        """Snap a division count to the nearest grid point."""
        g = self._grid_size
        return round(raw_divs / g) * g

    def time_to_divisions(self, seconds: float) -> int:
        """Convert seconds to raw divisions."""
        return int(seconds * self.divisions * (self.tempo / 60.0))

    def amplitude_to_velocity(self, amplitude: float) -> int:
        """Convert amplitude to MIDI velocity (1-127).

        Uses the data's own amplitude range (set by ``_calibrate_dynamics``)
        to map logarithmically into a compressed velocity range (pp–ff).
        """
        if amplitude <= 0 or self._amp_max <= 0:
            return 1
        # Log-scale the amplitude relative to the data range
        # so quiet and loud partials spread across the velocity range.
        log_min = math.log(max(self._amp_min, 1e-10))
        log_max = math.log(self._amp_max)
        if log_max <= log_min:
            return self._vel_center
        normalized = (math.log(max(amplitude, 1e-10)) - log_min) / (log_max - log_min)
        normalized = max(0.0, min(1.0, normalized))
        # Map to a compressed velocity range (default 40–110)
        vel = self._vel_min + normalized * (self._vel_max - self._vel_min)
        return int(round(vel))

    def _calibrate_dynamics(self, frames: List):
        """Set amplitude range from actual data for velocity scaling."""
        all_amps = [p.amplitude for f in frames for p in f.partials
                    if p.amplitude > 0]
        if all_amps:
            self._amp_min = min(all_amps)
            self._amp_max = max(all_amps)
        else:
            self._amp_min = 0.001
            self._amp_max = 1.0

    @staticmethod
    def _velocity_to_dynamic(velocity: int) -> str:
        """Map velocity to a dynamic marking name."""
        if velocity < 32:
            return 'pp'
        elif velocity < 52:
            return 'p'
        elif velocity < 72:
            return 'mp'
        elif velocity < 92:
            return 'mf'
        elif velocity < 107:
            return 'f'
        else:
            return 'ff'

    def _add_direction_dynamic(self, measure: Element, dynamic_label: str):
        """Add a <direction> element with a dynamic marking."""
        direction = SubElement(measure, 'direction', placement="below")
        dt = SubElement(direction, 'direction-type')
        dynamics = SubElement(dt, 'dynamics')
        SubElement(dynamics, dynamic_label)
        sound = SubElement(direction, 'sound')
        # Map label to approximate MIDI velocity for playback
        vel_map = {'pp': 32, 'p': 48, 'mp': 64, 'mf': 80, 'f': 96, 'ff': 112}
        sound.set('dynamics', str(vel_map.get(dynamic_label, 80)))

    # -- tempo inference ----------------------------------------------------

    def _infer_tempo(self, frames: List) -> int:
        """Infer a good tempo from note density in melodic mode.

        Strategy: find a tempo where the average note duration maps to a
        readable note value (ideally 8th or 16th notes).  We examine the
        median note duration in seconds and pick a tempo from a set of
        standard values (40–208 BPM) that maps that duration closest to
        an 8th note.

        Returns an integer BPM, or self.tempo if inference is not useful.
        """
        if not frames or self.mode != "melodic":
            return self.tempo

        # Collect note durations (only notes with real durations)
        durations = []
        for f in frames:
            if f.partials and f.partials[0].duration > 0:
                durations.append(f.partials[0].duration)

        if len(durations) < 3:
            return self.tempo

        import statistics
        # Use the median of the upper 75% of durations to avoid
        # ornamental/transient events skewing the tempo down.
        durations.sort()
        upper = durations[len(durations) // 4:]  # skip shortest 25%
        median_dur = statistics.median(upper) if upper else statistics.median(durations)
        if median_dur <= 0:
            return self.tempo

        # Standard tempi to consider
        candidate_tempi = [40, 48, 52, 56, 60, 66, 72, 80, 88, 96,
                           104, 112, 120, 132, 144, 152, 160, 168,
                           176, 184, 192, 200, 208]

        # For each candidate tempo, check how well the median note maps
        # to a standard rhythmic value (whole through 32nd note).
        # Note durations in seconds at tempo T:
        #   whole=240/T, half=120/T, quarter=60/T, 8th=30/T,
        #   16th=15/T, 32nd=7.5/T
        best_tempo = self.tempo
        best_score = float('inf')
        for t in candidate_tempi:
            targets = [240.0/t, 120.0/t, 60.0/t, 30.0/t, 15.0/t, 7.5/t]
            score = min(abs(median_dur - d) / d for d in targets)
            if score < best_score:
                best_score = score
                best_tempo = t

        # Only use inferred tempo if the fit is reasonable (< 40% deviation)
        if best_score < 0.40:
            print(f"  Tempo inference: median note = {median_dur*1000:.0f}ms → q={best_tempo}")
            return best_tempo
        else:
            print(f"  Tempo inference: no clean fit (median={median_dur*1000:.0f}ms), "
                  f"using q={self.tempo}")
            return self.tempo

    # -- pitch --------------------------------------------------------------

    def freq_to_pitch(self, frequency: float, quantization: str = None) -> PitchInfo:
        """Convert frequency to PitchInfo using the given or current quantization mode.

        Args:
            frequency: Hz value to convert.
            quantization: Override quantization mode ("semitone", "quarter-tone",
                          "eighth-tone"). If None, uses self.quantization.
        """
        if frequency <= 0:
            return PitchInfo("C", 0.0, 4, 0, "")

        quant = quantization or self.quantization
        midi_float = 69 + 12 * math.log2(frequency / 440.0)
        res = self.PITCH_RESOLUTIONS[quant]

        quantized = round(midi_float / res) * res
        remaining_cents = int((midi_float - quantized) * 100)

        base_midi = int(quantized)
        fractional = quantized - base_midi  # 0, 0.25, 0.5, or 0.75

        note_idx = base_midi % 12
        octave = (base_midi // 12) - 1

        step = NOTE_NAMES[note_idx]
        total_alter = BASE_ALTERATIONS[note_idx] + fractional

        # Respell to avoid extreme accidentals (double-sharp-down, etc.).
        # If total_alter > 1.5, move to the next note name and reduce alter.
        # If total_alter < -0.5, move to the previous note name and increase.
        # Step intervals in semitones: C-D=2, D-E=2, E-F=1, F-G=2, G-A=2, A-B=2, B-C=1
        STEP_UP = {'C': ('D', 2), 'D': ('E', 2), 'E': ('F', 1),
                   'F': ('G', 2), 'G': ('A', 2), 'A': ('B', 2), 'B': ('C', 1)}
        STEP_DOWN = {'D': ('C', 2), 'E': ('D', 2), 'F': ('E', 1),
                     'G': ('F', 2), 'A': ('G', 2), 'B': ('A', 2), 'C': ('B', 1)}

        if total_alter > 1.5 and step in STEP_UP:
            new_step, interval = STEP_UP[step]
            total_alter -= interval
            if step == 'B' and new_step == 'C':
                octave += 1
            step = new_step

        elif total_alter < -0.5 and step in STEP_DOWN:
            new_step, interval = STEP_DOWN[step]
            total_alter += interval
            if step == 'C' and new_step == 'B':
                octave -= 1
            step = new_step

        # Look up accidental text using the appropriate map for the quantization
        if quant == "eighth-tone":
            acc_map = ACCIDENTAL_MAP_EIGHTH
            alter_key = round(total_alter * 4) / 4.0
        elif quant == "quarter-tone":
            acc_map = ACCIDENTAL_MAP_QUARTER
            alter_key = round(total_alter * 2) / 2.0
        else:
            acc_map = ACCIDENTAL_MAP_SEMITONE
            alter_key = round(total_alter)
        alter_key = max(-2.0, min(2.0, alter_key))
        accidental_text = acc_map.get(alter_key, "")

        return PitchInfo(
            step=step,
            alter=total_alter,
            octave=octave,
            remaining_cents=remaining_cents,
            accidental_text=accidental_text,
        )

    def _deduplicate_chord(self, partials: List) -> List:
        """Remove partials that quantize to pitches too close together.

        When two partials in a chord land within 50 cents (one quarter-tone),
        keep the louder one.  This prevents perceptual fusion where multiple
        partials collapse into beating/roughness rather than distinct pitches.
        """
        if len(partials) <= 1:
            return partials

        # Compute quantized pitch info for each partial
        pitched = []
        for p in partials:
            pi = self.freq_to_pitch(p.frequency)
            # Convert to a comparable cent value from C0
            midi_approx = 12 * (pi.octave + 1) + {'C':0,'D':2,'E':4,'F':5,
                'G':7,'A':9,'B':11}.get(pi.step, 0) + pi.alter
            cents = midi_approx * 100
            pitched.append((cents, p))

        # Sort by cents, then by amplitude descending (keep louder)
        pitched.sort(key=lambda x: (x[0], -x[1].amplitude))

        kept = [pitched[0]]
        for cents, p in pitched[1:]:
            prev_cents = kept[-1][0]
            if abs(cents - prev_cents) >= 50:  # minimum quarter-tone spacing
                kept.append((cents, p))
            # else: skip this partial (too close to previous, and it's quieter)

        # Return in original amplitude order (loudest first)
        result = [p for _, p in kept]
        result.sort(key=lambda p: p.amplitude, reverse=True)
        return result

    def _same_pitch(self, partials_a: List, partials_b: List,
                    threshold: float = 0.03) -> bool:
        """Return True if two partial lists are similar enough to merge
        into a single sustained event (no re-attack).

        Every partial must be within *threshold* (default 3%) for the
        chords to be considered identical.  Partially-similar chords
        (some partials stable, others changed) are NOT merged here —
        per-partial ties handle those in harmonic mode.

        In melodic mode only the loudest partial is compared.
        """
        if not partials_a or not partials_b:
            return False

        if self.mode == "melodic":
            fa = partials_a[0].frequency
            fb = partials_b[0].frequency
            if fa <= 0 or fb <= 0:
                return False
            ratio = abs(fa - fb) / max(fa, fb)
            return ratio < threshold
        else:
            # Harmonic mode: ALL partials must be within threshold
            top_a = sorted(partials_a[:self.max_partials],
                           key=lambda p: p.frequency)
            top_b = sorted(partials_b[:self.max_partials],
                           key=lambda p: p.frequency)
            if len(top_a) != len(top_b):
                return False
            for pa, pb in zip(top_a, top_b):
                if pa.frequency <= 0 or pb.frequency <= 0:
                    return False
                ratio = abs(pa.frequency - pb.frequency) / max(pa.frequency, pb.frequency)
                if ratio >= threshold:
                    return False
            return True

    # -- input loading ------------------------------------------------------

    def load_from_single_json(self, filepath: str) -> List[TimeFrame]:
        """Load from a single Partiels JSON file (raw export or parsed analysis)."""
        with open(filepath, 'r') as f:
            data = json.load(f)

        events: List[PartialEvent] = []

        if 'results' in data:
            # Try PartielsParser first (handles spectral/multi-value data)
            try:
                sys.path.insert(0, str(Path(filepath).parent))
                from partiels_parser import PartielsParser
                parser = PartielsParser()
                parser.load_json(filepath)
                for p in parser.partials:
                    if self.min_frequency <= p.frequency <= self.max_frequency and (
                        self.mode == "harmonic" or p.amplitude >= self.min_amplitude):
                        events.append(PartialEvent(
                            time=p.time,
                            frequency=p.frequency,
                            amplitude=p.amplitude,
                            duration=self.frame_duration,
                        ))
            except ImportError:
                pass

            # If parser yielded nothing, parse results directly
            # (handles single-value partial tracking format with 'value' key)
            if not events:
                # First pass: collect all valid events and find max amplitude
                raw_events = []
                max_amp = 0.0
                for channel in data['results']:
                    for frame in channel:
                        t = frame.get('time', 0.0)
                        val = frame.get('value')
                        if val and self.min_frequency <= val <= self.max_frequency:
                            amp = frame['extra'][0] if 'extra' in frame and frame['extra'] else 0.5
                            if amp is not None and amp > max_amp:
                                max_amp = amp
                            raw_events.append((t, val, amp if amp is not None else 0.5,
                                             frame.get('duration', self.frame_duration)))

                # Second pass: apply adaptive amplitude filtering in melodic mode
                # (partial tracking data uses different amplitude scales than
                # monophonic pitch trackers — use 10% of max as threshold,
                # matching load_from_directory behavior)
                if self.mode == "melodic" and max_amp > 0:
                    effective_min_amp = max_amp * 0.10
                    print(f"  Amplitude filter (adaptive): threshold={effective_min_amp:.6f} "
                          f"(10% of max {max_amp:.6f})")
                else:
                    effective_min_amp = 0.0  # no filtering in harmonic mode

                for t, freq, amp, dur in raw_events:
                    if amp >= effective_min_amp:
                        events.append(PartialEvent(
                            time=t, frequency=freq, amplitude=amp, duration=dur,
                        ))

        elif 'chords' in data:
            for chord in data['chords']:
                freqs = chord.get('frequencies', [])
                start = chord.get('start_time', 0)
                dur = chord.get('duration', 0.5)
                for freq in freqs:
                    if freq > 0:
                        events.append(PartialEvent(
                            time=start, frequency=freq, amplitude=0.5, duration=dur,
                        ))
        else:
            print("Warning: Unknown JSON format — no 'results' or 'chords' key found.")

        return self._events_to_frames(events)

    def load_from_directory(self, directory: str) -> List[TimeFrame]:
        """Load from a directory of individual partial JSON files.

        Partiels exports each tracked partial as a separate JSON file
        (e.g. 'Group 1_Partial 1.json').  This method loads all of them
        and merges them into a single timeline so they appear as
        simultaneous partials in each time frame.
        """
        dir_path = Path(directory)

        # Use Path.glob to avoid issues with special characters in directory names
        partial_files = sorted(dir_path.glob("Group 1_Partial *.json"))

        if not partial_files:
            # Try broader pattern
            partial_files = sorted(dir_path.glob("*.json"))

        if not partial_files:
            print(f"  Warning: no JSON files found in {directory}")
            print(f"  Expected files like 'Group 1_Partial 1.json' or any *.json")
            return []

        # First pass: collect all events and detect amplitude scale.
        # Partial tracking exports (e.g. Chord/Inharmonic tracking) use
        # a different amplitude scale (often 0.001–0.05) than monophonic
        # pitch trackers like Crepe (0–1 confidence).  We detect this
        # and adapt the filter threshold automatically.
        raw_events: List[Tuple[float, float, float, float]] = []  # (time, freq, amp, dur)
        max_amp = 0.0

        for filepath in partial_files:
            with open(str(filepath), 'r') as f:
                data = json.load(f)

            if 'results' in data and data['results']:
                for result in data['results'][0]:
                    freq = result.get('value')
                    dur = result.get('duration', self.frame_duration)
                    if freq is None or freq < self.min_frequency or freq > self.max_frequency:
                        continue
                    if dur <= 0:
                        continue  # skip empty placeholder entries
                    amp = result['extra'][0] if 'extra' in result and result['extra'] else 0.5
                    if amp is not None and amp > max_amp:
                        max_amp = amp
                    raw_events.append((result['time'], freq, amp if amp is not None else 0.5, dur))

        # In harmonic mode, keep ALL partials and let max_partials limit
        # how many appear per chord.  Global amplitude filtering would
        # erase most partials from quieter chords since amplitudes vary
        # enormously across chords.
        # In melodic mode, apply adaptive amplitude filtering to discard
        # low-confidence noise — use 10% of max as threshold.
        events: List[PartialEvent] = []
        if self.mode == "melodic" and max_amp > 0:
            effective_min_amp = max_amp * 0.10
            print(f"  Amplitude filter (melodic): threshold={effective_min_amp:.6f} "
                  f"(10% of max {max_amp:.6f})")
            for time, freq, amp, dur in raw_events:
                if amp >= effective_min_amp:
                    events.append(PartialEvent(
                        time=time, frequency=freq,
                        amplitude=amp, duration=dur,
                    ))
        else:
            for time, freq, amp, dur in raw_events:
                events.append(PartialEvent(
                    time=time, frequency=freq,
                    amplitude=amp, duration=dur,
                ))

        print(f"  Loaded {len(events)} events from {len(partial_files)} partial files")

        # For partial tracking data, group by exact onset time rather
        # than quantizing to a fixed frame_duration grid.  All partials
        # belonging to the same chord share an identical onset time, and
        # each event already carries its own duration — so we preserve
        # both the number of chords and their exact timing.
        if not events:
            return []

        time_groups: Dict[float, List[PartialEvent]] = defaultdict(list)
        for e in events:
            time_groups[e.time].append(e)

        frames = []
        for t in sorted(time_groups.keys()):
            partials = sorted(time_groups[t],
                              key=lambda p: p.amplitude, reverse=True)
            frames.append(TimeFrame(time=t, partials=partials))

        print(f"  Chords: {len(frames)}")
        return frames

    def load_from_sdif(self, filepath: str) -> List[TimeFrame]:
        """Load from a 1TRC SDIF file (from SPEAR, Partiels, or sdif_processor).

        Reads the binary SDIF format, extracting frequency/amplitude data
        from each 1TRC frame and converting to TimeFrame objects.
        SDIF frames represent discrete chord events, so ties are disabled.
        """
        self.disable_ties = True
        path = Path(filepath)
        with open(path, 'rb') as f:
            file_data = f.read()

        # Verify SDIF header
        if not file_data.startswith(b'SDIF'):
            print(f"  Error: not a valid SDIF file (missing SDIF header)")
            return []

        # Find all 1TRC frames
        events: List[PartialEvent] = []
        pos = 0
        frame_count = 0

        while pos < len(file_data):
            next_pos = file_data.find(b'1TRC', pos)
            if next_pos == -1:
                break

            if next_pos + 8 > len(file_data):
                break

            try:
                frame_size = struct.unpack('>I', file_data[next_pos+4:next_pos+8])[0]

                # Sanity check frame size
                if 32 <= frame_size <= 100000:
                    # Check for matrix signature at expected position
                    matrix_sig_pos = next_pos + 24
                    if matrix_sig_pos + 4 <= len(file_data):
                        matrix_sig = file_data[matrix_sig_pos:matrix_sig_pos+4]
                        if matrix_sig == b'1TRC':
                            frame_events = self._parse_1trc_frame(file_data, next_pos)
                            if frame_events:
                                events.extend(frame_events)
                                frame_count += 1
                            pos = next_pos + frame_size
                            if pos % 8 != 0:
                                pos += (8 - pos % 8)
                            continue
            except Exception:
                pass

            pos = next_pos + 4

        if not events:
            print(f"  Error: no valid 1TRC frames found in SDIF file")
            return []

        print(f"  Loaded {len(events)} partial events from {frame_count} SDIF frames")

        # Apply frequency filtering
        filtered = []
        max_amp = max((e.amplitude for e in events), default=0)
        for e in events:
            if e.frequency < self.min_frequency or e.frequency > self.max_frequency:
                continue
            filtered.append(e)

        # Apply amplitude filtering in melodic mode
        if self.mode == "melodic" and max_amp > 0:
            effective_min_amp = max_amp * 0.10
            print(f"  Amplitude filter (melodic): threshold={effective_min_amp:.6f} "
                  f"(10% of max {max_amp:.6f})")
            filtered = [e for e in filtered if e.amplitude >= effective_min_amp]

        # Group by time into TimeFrames
        time_groups: Dict[float, List[PartialEvent]] = defaultdict(list)
        for e in filtered:
            t = round(e.time, 6)
            time_groups[t].append(e)

        frames = []
        sorted_times = sorted(time_groups.keys())
        for i, t in enumerate(sorted_times):
            partials = sorted(time_groups[t],
                              key=lambda p: p.amplitude, reverse=True)
            if i < len(sorted_times) - 1:
                dur = sorted_times[i + 1] - t
            else:
                dur = partials[0].duration if partials else 0.1
            for p in partials:
                p.duration = dur
            frames.append(TimeFrame(time=t, partials=partials))

        print(f"  Frames: {len(frames)} (after filtering)")
        return frames

    def _parse_1trc_frame(self, data: bytes, offset: int) -> List[PartialEvent]:
        """Parse a single 1TRC frame from byte data, returning PartialEvents."""
        try:
            pos = offset + 4  # skip frame signature
            frame_size = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4
            time = struct.unpack('>d', data[pos:pos+8])[0]
            pos += 8

            # Skip special NVT frames (time = -inf)
            if time < -1e30:
                return []

            stream_id = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4
            num_matrices = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4

            # Matrix header
            matrix_sig = data[pos:pos+4]
            pos += 4
            if matrix_sig != b'1TRC':
                return []
            data_type = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4
            num_rows = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4
            num_cols = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4

            events = []
            for _ in range(num_rows):
                if num_cols >= 4 and pos + num_cols * 4 <= len(data):
                    index = struct.unpack('>f', data[pos:pos+4])[0]
                    pos += 4
                    freq = struct.unpack('>f', data[pos:pos+4])[0]
                    pos += 4
                    amp = struct.unpack('>f', data[pos:pos+4])[0]
                    pos += 4
                    phase = struct.unpack('>f', data[pos:pos+4])[0]
                    pos += 4
                    pos += (num_cols - 4) * 4

                    if freq > 0 and amp > 0:
                        events.append(PartialEvent(
                            time=time, frequency=freq,
                            amplitude=amp, duration=0.1,
                        ))
                else:
                    pos += num_cols * 4

            return events

        except Exception:
            return []

    def _events_to_frames(self, events: List[PartialEvent]) -> List[TimeFrame]:
        """Group events into TimeFrames quantized by frame_duration."""
        if not events:
            return []

        time_groups: Dict[float, List[PartialEvent]] = defaultdict(list)
        for e in events:
            key = round(e.time / self.frame_duration) * self.frame_duration
            time_groups[key].append(e)

        frames = []
        for t in sorted(time_groups.keys()):
            partials = sorted(time_groups[t], key=lambda p: p.amplitude, reverse=True)
            frames.append(TimeFrame(time=t, partials=partials))

        # Octave-correction pass for melodic mode.
        # Monophonic pitch trackers (Crepe, etc.) sometimes make octave
        # errors — the reported frequency jumps by 2× or ½× for a few
        # frames.  We detect these by comparing the top partial's
        # frequency to a local median window and folding back if it's
        # close to an octave multiple.
        if self.mode == "melodic" and len(frames) >= 3:
            import statistics
            top_freqs = [f.partials[0].frequency if f.partials else 0
                         for f in frames]
            window = 5  # median window half-size
            corrected = list(top_freqs)
            for i in range(len(top_freqs)):
                if top_freqs[i] <= 0:
                    continue
                lo = max(0, i - window)
                hi = min(len(top_freqs), i + window + 1)
                neighbours = [f for f in top_freqs[lo:hi] if f > 0]
                if len(neighbours) < 2:
                    continue
                med = statistics.median(neighbours)
                ratio = top_freqs[i] / med
                # If roughly an octave up, halve it
                if 1.8 < ratio < 2.2:
                    corrected[i] = top_freqs[i] / 2.0
                # If roughly an octave down, double it
                elif 0.45 < ratio < 0.55:
                    corrected[i] = top_freqs[i] * 2.0

            # Apply corrections back to the frame partials
            for i, frame in enumerate(frames):
                if frame.partials and corrected[i] != top_freqs[i]:
                    p = frame.partials[0]
                    frame.partials[0] = PartialEvent(
                        time=p.time, frequency=corrected[i],
                        amplitude=p.amplitude, duration=p.duration
                    )

        # Melodic merge pass: collapse consecutive frames at the same pitch
        # into a single longer note.  Monophonic pitch trackers emit one
        # frame per analysis hop (~5-10ms), so a sustained pitch produces
        # dozens of identical-frequency frames.  We merge them into one
        # event whose duration spans from the first frame to the last.
        if self.mode == "melodic" and len(frames) >= 2:
            threshold = 0.03  # 3% frequency tolerance
            merged_frames = [frames[0]]
            for frame in frames[1:]:
                if not frame.partials:
                    merged_frames.append(frame)
                    continue
                prev = merged_frames[-1]
                if not prev.partials:
                    merged_frames.append(frame)
                    continue

                fa = prev.partials[0].frequency
                fb = frame.partials[0].frequency
                if fa > 0 and fb > 0:
                    ratio = abs(fa - fb) / max(fa, fb)
                else:
                    ratio = 1.0

                if ratio < threshold:
                    # Same pitch — extend the previous frame's duration
                    # Keep the louder partial's frequency and amplitude
                    pp = prev.partials[0]
                    fp = frame.partials[0]
                    new_dur = (frame.time - prev.time) + self.frame_duration
                    # Use amplitude-weighted average frequency
                    total_amp = pp.amplitude + fp.amplitude
                    if total_amp > 0:
                        avg_freq = (pp.frequency * pp.amplitude + fp.frequency * fp.amplitude) / total_amp
                    else:
                        avg_freq = pp.frequency
                    best_amp = max(pp.amplitude, fp.amplitude)
                    prev.partials[0] = PartialEvent(
                        time=pp.time, frequency=avg_freq,
                        amplitude=best_amp, duration=new_dur,
                    )
                else:
                    # Different pitch — finalize previous, start new note
                    # If previous duration is still 0, set it to the gap
                    if prev.partials[0].duration <= 0:
                        gap = frame.time - prev.time
                        pp = prev.partials[0]
                        prev.partials[0] = PartialEvent(
                            time=pp.time, frequency=pp.frequency,
                            amplitude=pp.amplitude, duration=max(gap, self.frame_duration),
                        )
                    merged_frames.append(frame)

            # Fix duration of the last frame if still 0
            last = merged_frames[-1]
            if last.partials and last.partials[0].duration <= 0:
                lp = last.partials[0]
                last.partials[0] = PartialEvent(
                    time=lp.time, frequency=lp.frequency,
                    amplitude=lp.amplitude, duration=self.frame_duration,
                )

            print(f"  Melodic merge: {len(frames)} frames → {len(merged_frames)} notes")
            frames = merged_frames

        return frames

    # -- MusicXML building --------------------------------------------------

    def _create_header(self) -> Element:
        root = Element('score-partwise', version="3.1")

        work = SubElement(root, 'work')
        wt = SubElement(work, 'work-title')
        wt.text = self.title

        ident = SubElement(root, 'identification')
        if self.composer:
            creator = SubElement(ident, 'creator', type="composer")
            creator.text = self.composer
        enc = SubElement(ident, 'encoding')
        sw = SubElement(enc, 'software')
        sw.text = "Partiels Interactive Converter"
        from datetime import date
        ed = SubElement(enc, 'encoding-date')
        ed.text = date.today().isoformat()

        return root

    def _create_part_list(self, root: Element):
        pl = SubElement(root, 'part-list')
        sp = SubElement(pl, 'score-part', id="P1")
        pn = SubElement(sp, 'part-name')
        pn.text = "Partials"

    def _create_attributes(self, measure: Element):
        attr = SubElement(measure, 'attributes')

        d = SubElement(attr, 'divisions')
        d.text = str(self.divisions)

        key = SubElement(attr, 'key')
        fifths = SubElement(key, 'fifths')
        fifths.text = "0"

        time_el = SubElement(attr, 'time')
        beats = SubElement(time_el, 'beats')
        beats.text = "4"
        bt = SubElement(time_el, 'beat-type')
        bt.text = "4"

        clef = SubElement(attr, 'clef')
        sign = SubElement(clef, 'sign')
        sign.text = "G"
        line = SubElement(clef, 'line')
        line.text = "2"

    def _add_tempo(self, measure: Element):
        direction = SubElement(measure, 'direction', placement="above")
        dt = SubElement(direction, 'direction-type')
        metro = SubElement(dt, 'metronome')
        bu = SubElement(metro, 'beat-unit')
        bu.text = "quarter"
        pm = SubElement(metro, 'per-minute')
        pm.text = str(self.tempo)
        sound = SubElement(direction, 'sound', tempo=str(self.tempo))

    def _standard_durations(self) -> List[Tuple[str, int, bool]]:
        """Build a table of all valid (type_name, duration_divs, is_triplet) entries.

        Includes straight, dotted, and triplet values, sorted descending
        by duration.  Dotted values use "dotted-" prefix in the type name
        so they can be identified for the <dot/> element.
        """
        straight = [
            ("whole",   self.divisions * 4),
            ("half",    self.divisions * 2),
            ("quarter", self.divisions),
            ("eighth",  self.divisions // 2),
            ("16th",    self.divisions // 4),
            ("32nd",    self.divisions // 8),
            ("64th",    self.divisions // 16),
        ]

        # Dotted values: 1.5× the straight duration
        dotted = [(f"dotted-{name}", dur + dur // 2) for name, dur in straight]

        triplet = [(name, round(dur * 2 / 3)) for name, dur in straight]

        # Combine: straight + dotted (not triplet) + triplet entries
        all_vals = [(n, d, False) for n, d in straight] + \
                   [(n, d, False) for n, d in dotted] + \
                   [(n, d, True) for n, d in triplet]

        # Sort descending by duration, prefer straight over dotted over triplet
        # For same duration: straight=0, dotted=1, triplet=2
        def sort_key(x):
            name, dur, is_trip = x
            if is_trip:
                prio = 2
            elif name.startswith("dotted-"):
                prio = 1
            else:
                prio = 0
            return (-dur, prio)
        all_vals.sort(key=sort_key)
        return all_vals

    def _duration_to_type(self, duration_divs: int) -> Tuple[str, bool]:
        """Map duration in divisions to MusicXML note type and whether it's a triplet.

        Returns (type_name, is_triplet).

        Checks against both straight and triplet durations. Exact match first,
        then falls back to the largest standard value ≤ the actual duration.
        """
        table = self._standard_durations()

        # Exact match (within ±1 for rounding)
        for name, ref, is_trip in table:
            if abs(duration_divs - ref) <= 1:
                return name, is_trip

        # Fallback: largest value that fits
        for name, ref, is_trip in table:
            if ref <= duration_divs:
                return name, is_trip

        return "64th", False

    def _create_note(self, measure: Element, pitch: PitchInfo,
                     duration_divs: int, velocity: int = 64,
                     is_chord: bool = False,
                     tuplet_start: bool = False,
                     tuplet_stop: bool = False,
                     tie_type: str = None,
                     voice: int = None,
                     stem: str = None,
                     quantization: str = None):
        """Create a <note> element.

        tie_type: None (no tie), "start" (begin tie), "stop" (end tie),
                  "stop-start" (middle of tied chain).
        voice: MusicXML voice number (1, 2, etc.) or None for default.
        stem: "up" or "down" or None for default.
        quantization: Override quantization for accidental/bend decisions.
        """
        quant = quantization or self.quantization
        note = SubElement(measure, 'note')

        if is_chord:
            SubElement(note, 'chord')

        # Pitch
        pitch_el = SubElement(note, 'pitch')
        step_el = SubElement(pitch_el, 'step')
        step_el.text = pitch.step

        if pitch.alter != 0:
            alter_el = SubElement(pitch_el, 'alter')
            alter_el.text = str(pitch.alter)

        oct_el = SubElement(pitch_el, 'octave')
        oct_el.text = str(pitch.octave)

        # Duration
        dur_el = SubElement(note, 'duration')
        dur_el.text = str(duration_divs)

        # Tie elements (after <duration>, before <voice> per MusicXML schema)
        if tie_type in ("start", "stop-start"):
            SubElement(note, 'tie', type="start")
        if tie_type in ("stop", "stop-start"):
            SubElement(note, 'tie', type="stop")

        # Voice
        if voice is not None:
            voice_el = SubElement(note, 'voice')
            voice_el.text = str(voice)

        # Type
        type_name, is_triplet = self._duration_to_type(duration_divs)
        is_dotted = type_name.startswith("dotted-")
        if is_dotted:
            type_name = type_name[7:]  # strip "dotted-" prefix
        type_el = SubElement(note, 'type')
        type_el.text = type_name

        # Dot element for dotted note values
        if is_dotted:
            SubElement(note, 'dot')

        # Triplet time-modification
        if is_triplet:
            tm = SubElement(note, 'time-modification')
            an = SubElement(tm, 'actual-notes')
            an.text = "3"
            nn = SubElement(tm, 'normal-notes')
            nn.text = "2"

        # Accidental (for quarter-tone and eighth-tone modes)
        if pitch.accidental_text and quant != "semitone":
            acc = SubElement(note, 'accidental')
            acc.text = pitch.accidental_text

        # Stem direction
        if stem is not None:
            stem_el = SubElement(note, 'stem')
            stem_el.text = stem

        # Notations
        notations_added = False

        # Tuplet start/stop (required by MuseScore to properly group triplets)
        if tuplet_start or tuplet_stop:
            notations = SubElement(note, 'notations')
            notations_added = True
            if tuplet_start:
                SubElement(notations, 'tuplet', type="start")
            if tuplet_stop:
                SubElement(notations, 'tuplet', type="stop")

        # Tied notation (visual arc)
        if tie_type:
            if not notations_added:
                notations = SubElement(note, 'notations')
                notations_added = True
            if tie_type in ("start", "stop-start"):
                SubElement(notations, 'tied', type="start")
            if tie_type in ("stop", "stop-start"):
                SubElement(notations, 'tied', type="stop")

        # Semitone mode: encode significant cents deviation as bend
        if quant == "semitone" and abs(pitch.remaining_cents) >= 25:
            if not notations_added:
                notations = SubElement(note, 'notations')
                notations_added = True
            technical = SubElement(notations, 'technical')
            bend = SubElement(technical, 'bend')
            ba = SubElement(bend, 'bend-alter')
            ba.text = str(pitch.remaining_cents / 100.0)

        return note

    def _create_grace_note(self, measure: Element, pitch: PitchInfo,
                           velocity: int = 64, quantization: str = None):
        """Create a grace note (acciaccatura — slashed eighth, no duration).

        Grace notes take no metric time.  MusicXML represents them with
        a <grace> element and zero <duration>.
        """
        quant = quantization or self.quantization
        note = SubElement(measure, 'note')
        SubElement(note, 'grace', slash="yes")

        pitch_el = SubElement(note, 'pitch')
        step_el = SubElement(pitch_el, 'step')
        step_el.text = pitch.step
        if pitch.alter != 0:
            alter_el = SubElement(pitch_el, 'alter')
            alter_el.text = str(pitch.alter)
        oct_el = SubElement(pitch_el, 'octave')
        oct_el.text = str(pitch.octave)

        type_el = SubElement(note, 'type')
        type_el.text = "eighth"

        if pitch.accidental_text and quant != "semitone":
            acc = SubElement(note, 'accidental')
            acc.text = pitch.accidental_text

        return note

    def _split_into_standard(self, duration_divs: int,
                             straight_only: bool = False) -> List[int]:
        """Split a duration into a list of standard note-value durations.

        Uses greedy decomposition, preferring straight values over triplets
        (since straight values have cleaner subdivisions). The sum of
        returned values always equals duration_divs exactly.

        If straight_only is True, only straight (non-triplet) values are used
        and any sub-smallest remainder is absorbed into the last piece.
        """
        if duration_divs <= 0:
            return []

        # Build two lists: straight values and all values (descending)
        table = self._standard_durations()
        straight_durs = sorted(set(d for _, d, trip in table if d > 0 and not trip), reverse=True)
        all_durs = sorted(set(d for _, d, _ in table if d > 0), reverse=True)

        smallest_straight = straight_durs[-1] if straight_durs else 1
        smallest = all_durs[-1] if all_durs else 1

        if duration_divs <= smallest:
            return [duration_divs]

        # Try straight-only decomposition first
        parts = []
        remaining = duration_divs
        while remaining >= smallest_straight:
            placed = False
            for d in straight_durs:
                if d <= remaining:
                    parts.append(d)
                    remaining -= d
                    placed = True
                    break
            if not placed:
                break

        if remaining == 0:
            return parts

        # If straight_only, absorb remainder into last part
        if straight_only:
            if remaining > 0 and parts:
                parts[-1] += remaining
            elif remaining > 0:
                parts.append(remaining)
            return parts

        # Straight didn't work cleanly — try mixed decomposition.
        # Use straight values greedily, then check if the remainder is
        # itself a standard (triplet) value.  This handles cases like
        # 80 = 48 (straight 64th) + 32 (triplet 64th).
        all_standard_set = set(all_durs)

        # Try each straight prefix and see if remainder is standard
        best = None
        for i in range(len(parts), 0, -1):
            prefix = parts[:i]
            rem = duration_divs - sum(prefix)
            if rem == 0:
                best = prefix
                break
            if rem in all_standard_set:
                best = prefix + [rem]
                break
            # Try decomposing remainder with all values
            sub_parts = []
            sub_rem = rem
            while sub_rem >= smallest:
                placed = False
                for d in all_durs:
                    if d <= sub_rem:
                        sub_parts.append(d)
                        sub_rem -= d
                        placed = True
                        break
                if not placed:
                    break
            if sub_rem == 0:
                best = prefix + sub_parts
                break

        if best is not None:
            return best

        # Fallback: greedy with all values
        parts = []
        remaining = duration_divs
        while remaining >= smallest:
            placed = False
            for d in all_durs:
                if d <= remaining:
                    parts.append(d)
                    remaining -= d
                    placed = True
                    break
            if not placed:
                break

        # Absorb any sub-smallest remainder
        if remaining > 0 and parts:
            parts[-1] += remaining
        elif remaining > 0:
            parts.append(remaining)

        return parts

    def _create_rest(self, measure: Element, duration_divs: int,
                     tuplet_start: bool = False, tuplet_stop: bool = False,
                     voice: int = None):
        """Create a single rest element. Rests are pre-split at the event level."""
        note = SubElement(measure, 'note')
        SubElement(note, 'rest')

        dur = SubElement(note, 'duration')
        dur.text = str(duration_divs)

        if voice is not None:
            voice_el = SubElement(note, 'voice')
            voice_el.text = str(voice)

        type_name, is_triplet = self._duration_to_type(duration_divs)
        is_dotted = type_name.startswith("dotted-")
        if is_dotted:
            type_name = type_name[7:]
        t = SubElement(note, 'type')
        t.text = type_name

        if is_dotted:
            SubElement(note, 'dot')

        if is_triplet:
            tm = SubElement(note, 'time-modification')
            an = SubElement(tm, 'actual-notes')
            an.text = "3"
            nn = SubElement(tm, 'normal-notes')
            nn.text = "2"

        if tuplet_start or tuplet_stop:
            notations = SubElement(note, 'notations')
            if tuplet_start:
                SubElement(notations, 'tuplet', type="start")
            if tuplet_stop:
                SubElement(notations, 'tuplet', type="stop")

    # -- score assembly -----------------------------------------------------

    def _prepare_frame_notes(self, frames: List[TimeFrame]) -> List:
        """Pre-compute quantized onsets/durations, merge, and annotate ties.

        Returns a list of (onset_divs, (dur_divs, partials)) tuples ready
        for measure building.
        """
        # Pre-compute frame onset and duration in quantized divisions
        raw_frame_notes = []
        for frame in frames:
            onset_divs = self._quantize_divs(self.time_to_divisions(frame.time))
            # Grace note sentinel: duration == -1.0
            raw_dur = frame.partials[0].duration if frame.partials else self.frame_duration
            if raw_dur < 0:
                # Grace note — zero metric duration, tagged for special handling
                dur_divs = 0
            else:
                dur_divs = self._quantize_divs(self.time_to_divisions(raw_dur))
                dur_divs = max(dur_divs, self._grid_size)  # minimum 1 grid unit
            raw_frame_notes.append((onset_divs, dur_divs, frame))

        # Separate grace notes from regular notes before merging.
        # Grace notes (dur_divs == 0) are kept in order and not merged.
        grace_notes = []  # (onset_divs, partials)
        regular_raw = []
        for onset, dur, frame in raw_frame_notes:
            if dur == 0:
                grace_notes.append((onset, list(frame.partials)))
            else:
                regular_raw.append((onset, dur, frame))

        # Merge regular frames that quantize to the same onset
        merged: Dict[int, Tuple[int, List[PartialEvent]]] = {}
        for onset, dur, frame in regular_raw:
            if onset in merged:
                existing_dur, existing_partials = merged[onset]
                combined = existing_partials + frame.partials
                combined.sort(key=lambda p: p.amplitude, reverse=True)
                merged[onset] = (max(existing_dur, dur), combined)
            else:
                merged[onset] = (dur, list(frame.partials))

        frame_notes = sorted(merged.items())

        # Merge consecutive frames where ALL partials are within threshold
        if len(frame_notes) > 1:
            merged_notes: List[Tuple[int, Tuple[int, List]]] = [frame_notes[0]]
            for onset, (dur, partials) in frame_notes[1:]:
                prev_onset, (prev_dur, prev_partials) = merged_notes[-1]
                prev_end = prev_onset + prev_dur
                gap = onset - prev_end
                contiguous = (gap <= self._grid_size)
                if contiguous and self._same_pitch(prev_partials, partials):
                    new_dur = (onset + dur) - prev_onset
                    merged_notes[-1] = (prev_onset, (new_dur, prev_partials))
                else:
                    merged_notes.append((onset, (dur, partials)))
            frame_notes = merged_notes

        # Adjust durations to standard note values.
        # In melodic mode, use only straight + dotted values (no triplets) so
        # every note is a single clean note value — never a tied mess like
        # "16th tied to triplet 32nd".
        # In harmonic mode, allow all standard values including triplets.
        table = self._standard_durations()
        if self.mode == "melodic":
            # Straight + dotted only (no triplets)
            candidate_durs = sorted(
                set(d for _, d, trip in table if d > 0 and not trip),
                reverse=True
            )
        else:
            candidate_durs = sorted(
                set(d for _, d, _ in table if d > 0),
                reverse=True
            )

        for i in range(len(frame_notes) - 1):
            onset_cur = frame_notes[i][0]
            onset_next = frame_notes[i + 1][0]
            dur_cur = frame_notes[i][1][0]
            gap_to_next = onset_next - onset_cur
            if gap_to_next > 0:
                # Find the best standard duration that fits in the gap
                best_dur = None
                for sd in candidate_durs:
                    if sd <= gap_to_next:
                        best_dur = sd
                        break
                if best_dur is None:
                    best_dur = candidate_durs[-1] if candidate_durs else dur_cur
                partials = frame_notes[i][1][1]
                frame_notes[i] = (onset_cur, (best_dur, partials))

        # Per-partial tie annotation for harmonic mode
        # Skip when disable_ties is set (e.g. SDIF input = discrete chords)
        if self.mode == "harmonic" and len(frame_notes) > 1 and not self.disable_ties:
            threshold = 0.03
            for i in range(len(frame_notes)):
                _, (_, partials_cur) = frame_notes[i]
                cur = partials_cur[:self.max_partials]

                if i > 0:
                    prev_onset, (prev_dur, partials_prev) = frame_notes[i - 1]
                    prev = partials_prev[:self.max_partials]
                    cur_onset = frame_notes[i][0]
                    prev_end = prev_onset + prev_dur
                    gap = cur_onset - prev_end
                    contiguous_with_prev = (gap <= self._grid_size)
                else:
                    prev = []
                    contiguous_with_prev = False

                if i < len(frame_notes) - 1:
                    next_onset_val = frame_notes[i + 1][0]
                    _, (_, partials_next) = frame_notes[i + 1]
                    nxt = partials_next[:self.max_partials]
                    cur_onset = frame_notes[i][0]
                    cur_dur = frame_notes[i][1][0]
                    cur_end = cur_onset + cur_dur
                    gap_next = next_onset_val - cur_end
                    contiguous_with_next = (gap_next <= self._grid_size)
                else:
                    nxt = []
                    contiguous_with_next = False

                prev_sorted = sorted(prev, key=lambda p: p.frequency) if prev else []
                next_sorted = sorted(nxt, key=lambda p: p.frequency) if nxt else []

                for p in cur:
                    matches_prev = False
                    matches_next = False

                    if contiguous_with_prev and prev_sorted:
                        for pp in prev_sorted:
                            if pp.frequency > 0 and p.frequency > 0:
                                ratio = abs(p.frequency - pp.frequency) / max(p.frequency, pp.frequency)
                                if ratio < threshold:
                                    matches_prev = True
                                    break

                    if contiguous_with_next and next_sorted:
                        for pn in next_sorted:
                            if pn.frequency > 0 and p.frequency > 0:
                                ratio = abs(p.frequency - pn.frequency) / max(p.frequency, pn.frequency)
                                if ratio < threshold:
                                    matches_next = True
                                    break

                    if matches_prev and matches_next:
                        p._tie_type = "stop-start"
                    elif matches_prev:
                        p._tie_type = "stop"
                    elif matches_next:
                        p._tie_type = "start"
                    else:
                        p._tie_type = None

        return frame_notes, grace_notes

    def _build_part_measures(self, part: Element, frames: List[TimeFrame],
                             frame_notes: List, num_measures: int,
                             grace_notes: List = None,
                             quantization: str = None,
                             include_coda: bool = True) -> int:
        """Build all measures for a single MusicXML part.

        Args:
            part: The <part> XML element to populate.
            frames: Original TimeFrame list (for metadata).
            frame_notes: Pre-computed (onset, (dur, partials)) from _prepare_frame_notes.
            num_measures: Number of measures in the main body.
            quantization: Override quantization for pitch conversion.
            include_coda: Whether to append the coda section.

        Returns:
            Number of coda measures added.
        """
        quant = quantization or self.quantization
        measure_duration_divs = self.divisions * 4
        current_dynamic = None

        for m_num in range(1, num_measures + 1):
            measure = SubElement(part, 'measure', number=str(m_num))

            if m_num == 1:
                self._create_attributes(measure)
                self._add_tempo(measure)

            m_start = (m_num - 1) * measure_duration_divs
            m_end = m_num * measure_duration_divs
            cursor = 0

            measure_events = []

            # Collect grace notes for this measure (sorted by onset)
            measure_graces = []
            if grace_notes:
                for g_onset, g_partials in grace_notes:
                    if m_start <= g_onset < m_end:
                        measure_graces.append((g_onset - m_start, g_partials))
            grace_idx = 0

            for onset, (dur, partials) in frame_notes:
                if onset < m_start or onset >= m_end:
                    continue

                relative_onset = onset - m_start
                if relative_onset < cursor:
                    continue

                if relative_onset > cursor:
                    rest_dur = relative_onset - cursor
                    measure_events.append(('rest', rest_dur, None))
                    cursor = relative_onset

                # Insert any grace notes that fall at or just before this onset
                while grace_idx < len(measure_graces):
                    g_rel, g_parts = measure_graces[grace_idx]
                    if g_rel <= relative_onset:
                        measure_events.append(('grace', 0, g_parts))
                        grace_idx += 1
                    else:
                        break

                space_left = measure_duration_divs - cursor
                if space_left <= 0:
                    break

                note_dur = min(dur, space_left)
                measure_events.append(('note', note_dur, partials))
                cursor += note_dur

            remaining = measure_duration_divs - cursor
            if remaining > 0:
                measure_events.append(('rest', remaining, None))

            # Pre-split into standard note values
            expanded_events = []
            for kind, dur, parts in measure_events:
                if kind == 'grace':
                    expanded_events.append(('grace', 0, parts, None))
                    continue
                sub_durs = self._split_into_standard(
                    dur, straight_only=(self.mode == "melodic"))
                if kind == 'rest':
                    for sub_dur in sub_durs:
                        expanded_events.append(('rest', sub_dur, None, None))
                else:
                    if len(sub_durs) == 1:
                        expanded_events.append((kind, sub_durs[0], parts, None))
                    else:
                        for si, sub_dur in enumerate(sub_durs):
                            if si == 0:
                                tie = "start"
                            elif si == len(sub_durs) - 1:
                                tie = "stop"
                            else:
                                tie = "stop-start"
                            expanded_events.append((kind, sub_dur, parts, tie))
            measure_events = expanded_events

            # Absorb tiny trailing rests
            min_rest = self.divisions // 4
            if (len(measure_events) >= 2
                    and measure_events[-1][0] == 'rest'
                    and measure_events[-1][1] < min_rest):
                tiny_rest_dur = measure_events[-1][1]
                measure_events.pop()
                for j in range(len(measure_events) - 1, -1, -1):
                    if measure_events[j][0] == 'note':
                        k, d, p, t = measure_events[j]
                        measure_events[j] = (k, d + tiny_rest_dur, p, t)
                        break

            # Triplet flags
            event_triplet_flags = []
            for kind, dur, _, _tie in measure_events:
                _, is_trip = self._duration_to_type(dur)
                event_triplet_flags.append(is_trip)

            triplet_indices = [i for i, flag in enumerate(event_triplet_flags) if flag]
            tuplet_starts = [False] * len(measure_events)
            tuplet_stops = [False] * len(measure_events)

            for g in range(0, len(triplet_indices), 3):
                group = triplet_indices[g:g+3]
                tuplet_starts[group[0]] = True
                tuplet_stops[group[-1]] = True

            # Check for per-partial ties
            has_per_partial_ties = False
            if self.mode == "harmonic":
                for _, _, parts, t_type in measure_events:
                    if parts and t_type is None:
                        for p in (parts[:self.max_partials] if parts else []):
                            if hasattr(p, '_tie_type') and p._tie_type is not None:
                                has_per_partial_ties = True
                                break
                    if has_per_partial_ties:
                        break

            use_two_voices = (self.mode == "harmonic" and has_per_partial_ties)

            if use_two_voices:
                v1_events = []
                v2_events = []

                cursor_pos = 0
                for idx, (kind, dur, partials, tie_type) in enumerate(measure_events):
                    ts = tuplet_starts[idx]
                    te = tuplet_stops[idx]

                    if kind == 'rest':
                        v1_events.append(('rest', cursor_pos, dur, None, None, ts, te))
                        v2_events.append(('rest', cursor_pos, dur, None, None, ts, te))
                    else:
                        partials_to_use = partials[:self.max_partials] if partials else []
                        if self.mode == "harmonic":
                            partials_to_use = self._deduplicate_chord(partials_to_use)
                        v1_partials = []
                        v2_partials = []
                        for p in partials_to_use:
                            if tie_type is not None:
                                v1_partials.append((p, tie_type))
                            elif hasattr(p, '_tie_type') and p._tie_type is not None:
                                v1_partials.append((p, p._tie_type))
                            else:
                                v2_partials.append((p, None))

                        if v1_partials:
                            v1_events.append(('note', cursor_pos, dur, v1_partials, None, ts, te))
                        else:
                            v1_events.append(('rest', cursor_pos, dur, None, None, ts, te))

                        if v2_partials:
                            v2_events.append(('note', cursor_pos, dur, v2_partials, None, ts, te))
                        else:
                            v2_events.append(('rest', cursor_pos, dur, None, None, ts, te))

                    cursor_pos += dur

                # Emit voice 1
                for ev_kind, ev_onset, ev_dur, ev_partials, _, ts, te in v1_events:
                    if ev_kind == 'rest':
                        self._create_rest(measure, ev_dur, tuplet_start=ts,
                                          tuplet_stop=te, voice=1)
                    else:
                        for ci, (p, pt) in enumerate(ev_partials):
                            pitch = self.freq_to_pitch(p.frequency, quantization=quant)
                            vel = self.amplitude_to_velocity(p.amplitude)
                            self._create_note(measure, pitch, ev_dur, vel,
                                              is_chord=(ci > 0),
                                              tuplet_start=(ts and ci == 0),
                                              tuplet_stop=(te and ci == 0),
                                              tie_type=pt, voice=1, stem="up",
                                              quantization=quant)

                # Emit voice 2
                backup = SubElement(measure, 'backup')
                backup_dur = SubElement(backup, 'duration')
                backup_dur.text = str(measure_duration_divs)

                for ev_kind, ev_onset, ev_dur, ev_partials, _, ts, te in v2_events:
                    if ev_kind == 'rest':
                        self._create_rest(measure, ev_dur, tuplet_start=ts,
                                          tuplet_stop=te, voice=2)
                    else:
                        if ev_partials:
                            avg_amp = sum(p.amplitude for p, _ in ev_partials) / len(ev_partials)
                            event_vel = self.amplitude_to_velocity(avg_amp)
                            dyn_label = self._velocity_to_dynamic(event_vel)
                            if dyn_label != current_dynamic:
                                self._add_direction_dynamic(measure, dyn_label)
                                current_dynamic = dyn_label

                        for ci, (p, pt) in enumerate(ev_partials):
                            pitch = self.freq_to_pitch(p.frequency, quantization=quant)
                            vel = self.amplitude_to_velocity(p.amplitude)
                            self._create_note(measure, pitch, ev_dur, vel,
                                              is_chord=(ci > 0),
                                              tuplet_start=(ts and ci == 0),
                                              tuplet_stop=(te and ci == 0),
                                              tie_type=pt, voice=2, stem="down",
                                              quantization=quant)
            else:
                # Single-voice emission
                for idx, (kind, dur, partials, tie_type) in enumerate(measure_events):
                    ts = tuplet_starts[idx]
                    te = tuplet_stops[idx]

                    if kind == 'grace':
                        # Grace note — zero duration, emitted before next note
                        if partials:
                            best = partials[0]
                            pitch = self.freq_to_pitch(best.frequency, quantization=quant)
                            vel = self.amplitude_to_velocity(best.amplitude)
                            self._create_grace_note(measure, pitch, vel,
                                                    quantization=quant)
                        continue

                    if kind == 'rest':
                        self._create_rest(measure, dur, tuplet_start=ts, tuplet_stop=te)
                    else:
                        partials_to_use = partials[:self.max_partials]
                        if self.mode == "harmonic":
                            partials_to_use = self._deduplicate_chord(partials_to_use)

                        if partials_to_use:
                            avg_amp = sum(p.amplitude for p in partials_to_use) / len(partials_to_use)
                            event_vel = self.amplitude_to_velocity(avg_amp)
                        else:
                            event_vel = self._vel_center

                        dyn_label = self._velocity_to_dynamic(event_vel)
                        if dyn_label != current_dynamic:
                            self._add_direction_dynamic(measure, dyn_label)
                            current_dynamic = dyn_label

                        if self.mode == "melodic":
                            if partials_to_use:
                                best = partials_to_use[0]
                                pitch = self.freq_to_pitch(best.frequency, quantization=quant)
                                self._create_note(measure, pitch, dur, event_vel,
                                                  tuplet_start=ts, tuplet_stop=te,
                                                  tie_type=tie_type,
                                                  quantization=quant)
                        else:
                            for ci, p in enumerate(partials_to_use):
                                pitch = self.freq_to_pitch(p.frequency, quantization=quant)
                                vel = self.amplitude_to_velocity(p.amplitude)
                                if tie_type is not None:
                                    pt = tie_type
                                elif hasattr(p, '_tie_type') and p._tie_type is not None:
                                    pt = p._tie_type
                                else:
                                    pt = None
                                self._create_note(measure, pitch, dur, vel,
                                                  is_chord=(ci > 0),
                                                  tuplet_start=(ts and ci == 0),
                                                  tuplet_stop=(te and ci == 0),
                                                  tie_type=pt,
                                                  quantization=quant)

        # -- Coda section --
        total_coda_measures = 0
        chord_events = [(onset, partials) for onset, (dur, partials) in frame_notes
                        if partials]

        if include_coda and chord_events and self.mode == "harmonic":
            coda_m_num = num_measures + 1
            whole_note_divs = self.divisions * 4
            coda_tempo = 60
            first_coda = True

            for chord_idx, (onset, partials) in enumerate(chord_events):
                partials_to_use = partials[:self.max_partials]
                if self.mode == "harmonic":
                    partials_to_use = self._deduplicate_chord(partials_to_use)
                if not partials_to_use:
                    continue

                measure = SubElement(part, 'measure', number=str(coda_m_num))

                if first_coda:
                    self._create_attributes(measure)
                    direction = SubElement(measure, 'direction', placement="above")
                    dt_el = SubElement(direction, 'direction-type')
                    SubElement(dt_el, 'coda')
                    direction2 = SubElement(measure, 'direction', placement="above")
                    dt2 = SubElement(direction2, 'direction-type')
                    metro = SubElement(dt2, 'metronome')
                    bu = SubElement(metro, 'beat-unit')
                    bu.text = "quarter"
                    pm = SubElement(metro, 'per-minute')
                    pm.text = str(coda_tempo)
                    sound = SubElement(direction2, 'sound', tempo=str(coda_tempo))
                    first_coda = False

                for ci, p in enumerate(partials_to_use):
                    pitch = self.freq_to_pitch(p.frequency, quantization=quant)
                    vel = self.amplitude_to_velocity(p.amplitude)
                    self._create_note(measure, pitch, whole_note_divs, vel,
                                      is_chord=(ci > 0), quantization=quant)

                coda_m_num += 1

                measure = SubElement(part, 'measure', number=str(coda_m_num))
                ascending = sorted(partials_to_use, key=lambda p: p.frequency)
                n_notes = len(ascending)

                if n_notes == 0:
                    self._create_rest(measure, whole_note_divs)
                else:
                    if n_notes <= 4:
                        note_dur = self.divisions
                    elif n_notes <= 8:
                        note_dur = self.divisions // 2
                    else:
                        note_dur = self.divisions // 4

                    scale_cursor = 0
                    for p in ascending:
                        if scale_cursor + note_dur > whole_note_divs:
                            break
                        pitch = self.freq_to_pitch(p.frequency, quantization=quant)
                        vel = self.amplitude_to_velocity(p.amplitude)
                        self._create_note(measure, pitch, note_dur, vel,
                                          quantization=quant)
                        scale_cursor += note_dur

                    remaining = whole_note_divs - scale_cursor
                    if remaining > 0:
                        sub_durs = self._split_into_standard(remaining)
                        for sd in sub_durs:
                            self._create_rest(measure, sd)

                coda_m_num += 1

            total_coda_measures = coda_m_num - num_measures - 1

        return total_coda_measures

    def _compute_subharmonics(self, frames: List[TimeFrame]) -> List[TimeFrame]:
        """Infer foundation tones from high inharmonic partials.

        For each frame, compute subharmonics (freq/N for N=2..6) for each
        partial, find convergence zones where subharmonics from *different*
        source partials cluster tightly.  Strong convergence — where many
        independent partials agree on the same implied fundamental — indicates
        a real foundation tone.

        Scoring rewards:
          • number of distinct source partials converging (most important)
          • variety of divisor ratios (a tone confirmed by /2, /3, /5 is
            stronger than one confirmed only by /2, /2, /2)
          • amplitude of the contributing partials

        Returns a new list of TimeFrames with synthetic foundation tones.
        If no convergence is found for a frame, uses the original partials.
        """
        result_frames = []

        for frame in frames:
            if not frame.partials:
                result_frames.append(frame)
                continue

            partials = frame.partials[:self.max_partials]

            # Step 1: Generate all subharmonics, tagging each with its
            # source partial index so we can count distinct sources later.
            # (sub_freq, source_index, source_amplitude, divisor_N)
            subharmonics = []
            for idx, p in enumerate(partials):
                if p.frequency <= 0:
                    continue
                for n in range(2, 7):  # N = 2, 3, 4, 5, 6
                    sub_freq = p.frequency / n
                    if sub_freq >= self.min_frequency:
                        subharmonics.append((sub_freq, idx, p.amplitude, n))

            if not subharmonics:
                result_frames.append(frame)
                continue

            # Step 2: Cluster by proximity (2% tolerance — tighter than before)
            subharmonics.sort(key=lambda x: x[0])
            clusters = []  # list of [(freq, src_idx, amp, n), ...]
            used = [False] * len(subharmonics)

            for i in range(len(subharmonics)):
                if used[i]:
                    continue
                cluster = [subharmonics[i]]
                used[i] = True
                centroid = subharmonics[i][0]

                for j in range(i + 1, len(subharmonics)):
                    if used[j]:
                        continue
                    ratio = abs(subharmonics[j][0] - centroid) / max(centroid, 1e-10)
                    if ratio < 0.02:
                        cluster.append(subharmonics[j])
                        used[j] = True
                        centroid = sum(s[0] for s in cluster) / len(cluster)

                clusters.append(cluster)

            # Step 3: Score each cluster — require genuine convergence
            scored = []
            for cluster in clusters:
                # Count distinct source partials
                source_indices = set(s[1] for s in cluster)
                # Count distinct divisor ratios
                divisor_set = set(s[3] for s in cluster)

                n_sources = len(source_indices)
                n_divisors = len(divisor_set)

                # STRICT: require at least 3 distinct source partials converging
                # This eliminates spurious clusters from just 2 partials
                # happening to land near each other
                if n_sources < 3:
                    continue

                centroid = sum(s[0] for s in cluster) / len(cluster)

                # Score formula:
                # - n_sources^2 rewards convergence from many independent partials
                # - n_divisors rewards confirmation across different harmonic ratios
                # - amplitude term uses mean(amp) not amp/n to avoid over-weighting
                #   low divisors
                mean_amp = sum(s[2] for s in cluster) / len(cluster)
                score = (n_sources ** 2) * n_divisors * mean_amp

                scored.append((centroid, score, n_sources, n_divisors))

            if not scored:
                # No strong convergence — use original partials
                result_frames.append(frame)
                continue

            # Sort by score descending, keep only the strongest foundations
            # Limit to fewer than max_partials — foundation tones should be
            # sparse (typically 1-3 per chord, not 8)
            max_foundations = min(self.max_partials, 4)
            scored.sort(key=lambda x: x[1], reverse=True)
            scored = scored[:max_foundations]

            # Normalize scores to amplitude range
            max_score = scored[0][1]
            max_orig_amp = max(p.amplitude for p in partials)
            synthetic_partials = []
            for centroid, score, n_src, n_div in scored:
                # Strongest cluster → 80% of loudest original partial
                synth_amp = max_orig_amp * (score / max_score) * 0.8
                synthetic_partials.append(PartialEvent(
                    time=frame.time,
                    frequency=centroid,
                    amplitude=synth_amp,
                    duration=frame.partials[0].duration if frame.partials else 0.1,
                ))

            synthetic_partials.sort(key=lambda p: p.amplitude, reverse=True)
            result_frames.append(TimeFrame(time=frame.time, partials=synthetic_partials))

        return result_frames

    def _create_part_list_multi(self, root: Element,
                                parts: List[Tuple[str, str]]):
        """Create <part-list> with multiple parts.

        Args:
            parts: List of (part_id, part_name) tuples.
        """
        pl = SubElement(root, 'part-list')
        for part_id, part_name in parts:
            sp = SubElement(pl, 'score-part', id=part_id)
            pn = SubElement(sp, 'part-name')
            pn.text = part_name

    def _build_score(self, frames: List[TimeFrame], output_path: str):
        """Build the full MusicXML score and write to file."""
        if not frames:
            print("Error: no data to convert.")
            return

        root = self._create_header()

        # Determine total duration
        max_time = max(f.time + (f.partials[0].duration if f.partials else 0.1)
                       for f in frames)
        measure_duration_sec = 4.0 * (60.0 / self.tempo)
        num_measures = int(max_time / measure_duration_sec) + 1

        # Prepare frame notes (shared timing data)
        frame_notes, grace_notes = self._prepare_frame_notes(frames)

        # Double-staff mode: microtonal + harmonic → two parts
        # (unless ensemble_only is set — then just the single microtonal part)
        is_double_staff = (self.quantization in ("quarter-tone", "eighth-tone")
                           and self.mode == "harmonic"
                           and not self.ensemble_only)

        if is_double_staff:
            # Build part list: Ensemble (microtonal) on top,
            # then Foundations/Keyboards below, optionally a semitone staff
            part_defs = [("P1", "Ensemble")]
            if self.use_subharmonics:
                part_defs.append(("P2", "Foundations"))
            else:
                part_defs.append(("P2", "Keyboards"))
            if self.include_semitone_staff:
                part_defs.append(("P3", "Keyboards (semitone)"))
            self._create_part_list_multi(root, part_defs)

            # Part 1 (top): Ensemble — user's chosen microtonal quantization
            part_ens = SubElement(root, 'part', id="P1")
            coda_ens = self._build_part_measures(
                part_ens, frames, frame_notes, num_measures,
                grace_notes=grace_notes,
                quantization=self.quantization, include_coda=True)

            # Part 2: Foundations (subharmonic) or Keyboards (semitone doubling)
            part_kb = SubElement(root, 'part', id="P2")
            if self.use_subharmonics:
                kb_frames = self._compute_subharmonics(frames)
                kb_frame_notes, kb_graces = self._prepare_frame_notes(kb_frames)
                kb_quant = self.quantization  # microtonal — real spectral pitches
                n_foundations = sum(len(f.partials) for f in kb_frames
                                   if f.partials and f not in frames)
                print(f"  Subharmonics: {n_foundations} "
                      f"foundation tones inferred from {len(frames)} frames")
            else:
                kb_frame_notes = frame_notes
                kb_graces = grace_notes
                kb_quant = "semitone"

            coda_kb = self._build_part_measures(
                part_kb, frames, kb_frame_notes, num_measures,
                grace_notes=kb_graces,
                quantization=kb_quant, include_coda=True)

            coda_values = [coda_ens, coda_kb]

            # Optional Part 3: semitone reduction staff
            if self.include_semitone_staff:
                part_semi = SubElement(root, 'part', id="P3")
                coda_semi = self._build_part_measures(
                    part_semi, frames, frame_notes, num_measures,
                    grace_notes=grace_notes,
                    quantization="semitone", include_coda=True)
                coda_values.append(coda_semi)

            total_coda = max(coda_values)
            print(f"\n  Created: {output_path}")
            print(f"  {num_measures} measures + {total_coda} coda measures")
            staff_desc = [f"Ensemble ({self.quantization})"]
            if self.use_subharmonics:
                staff_desc.append(f"Foundations ({self.quantization})")
                print(f"  Staves: {' + '.join(staff_desc)}")
                print(f"  Foundation tones: subharmonic inference (≥3 partial convergence)")
            else:
                staff_desc.append("Keyboards (semitone)")
                print(f"  Staves: {' + '.join(staff_desc)}")
            if self.include_semitone_staff:
                print(f"  + Keyboards (semitone) reduction staff")
        else:
            self._create_part_list(root)
            part = SubElement(root, 'part', id="P1")
            total_coda = self._build_part_measures(
                part, frames, frame_notes, num_measures,
                grace_notes=grace_notes,
                quantization=self.quantization, include_coda=True)

            print(f"\n  Created: {output_path}")
            print(f"  {num_measures} measures + {total_coda} coda measures")

        print(f"  {len(frames)} time frames")
        print(f"  Pitch: {self.quantization}  |  Rhythm: {self.rhythm}  |  Mode: {self.mode}")
        print(f"  Ready to import into Dorico!")

        self._write_xml(root, output_path)

    def _write_xml(self, root: Element, output_path: str):
        xml_str = tostring(root, encoding='unicode')
        dom = minidom.parseString(xml_str)
        pretty_xml = dom.toprettyxml(indent="  ", encoding="UTF-8")
        with open(output_path, 'wb') as f:
            f.write(pretty_xml)

    # -- SDIF export --------------------------------------------------------

    def _write_sdif(self, frames: List, output_path: str,
                    frames_per_chord: int = 3, gap_duration: float = 0.02):
        """Write frames to a 1TRC SDIF file compatible with sdif_processor.py.

        Each TimeFrame is written as a sustained chord with multiple 1TRC
        frames spread across the hold duration, plus attack/release envelopes
        between chords to prevent glissando artifacts in SPEAR.

        Args:
            frames: List of TimeFrame objects
            output_path: Output SDIF file path
            frames_per_chord: Number of sustain frames per chord (default 3)
            gap_duration: Duration of attack/release envelope gap (seconds)
        """
        # Filter to non-empty frames
        active_frames = [fr for fr in frames if fr.partials]
        if not active_frames:
            return

        with open(output_path, 'wb') as f:
            # SDIF header
            f.write(b'SDIF')
            f.write(struct.pack('>I', 8))       # header size
            f.write(struct.pack('>I', 3))        # standard types version
            f.write(struct.pack('>I', 1))        # standard header size

            # NVT (Name-Value Table) — required metadata frame
            f.write(b'1NVT')
            f.write(struct.pack('>I', 32))       # frame size
            f.write(struct.pack('>d', float('-inf')))  # special time
            f.write(struct.pack('>I', 0xFFFFFFFD))     # special stream ID
            f.write(struct.pack('>I', 1))        # num matrices
            f.write(b'1NVT')                     # matrix signature
            f.write(struct.pack('>I', 0x0301))   # matrix data type
            f.write(struct.pack('>I', 0))        # rows
            f.write(struct.pack('>I', 1))        # cols

            # Write sustained chords with attack/release envelopes
            for idx, frame in enumerate(active_frames):
                partials_to_write = frame.partials[:self.max_partials]

                # Compute hold duration from gap to next frame (or last
                # partial's duration as fallback)
                if idx < len(active_frames) - 1:
                    hold = active_frames[idx + 1].time - frame.time
                    # Leave room for the gap envelope
                    hold = max(0.01, hold - gap_duration)
                else:
                    # Last frame: use the first partial's duration or 0.5s
                    hold = getattr(partials_to_write[0], 'duration', 0.5)
                    hold = max(0.1, hold)

                # --- Attack frame (fade-in) ---
                if idx > 0:
                    attack_time = frame.time - gap_duration / 2
                    self._write_1trc_frame(
                        f, attack_time, partials_to_write, amp_scale=0.01)

                # --- Sustain frames (full amplitude) ---
                for si in range(frames_per_chord):
                    t = frame.time + (si * hold / frames_per_chord)
                    self._write_1trc_frame(
                        f, t, partials_to_write, amp_scale=1.0)

                # --- Release frame (fade-out) ---
                if idx < len(active_frames) - 1:
                    release_time = frame.time + hold
                    self._write_1trc_frame(
                        f, release_time, partials_to_write, amp_scale=0.001)

    def _write_1trc_frame(self, f, time: float, partials: List,
                          amp_scale: float = 1.0):
        """Write a single 1TRC frame to an open SDIF file handle.

        Args:
            f: Open file handle (binary write mode)
            time: Frame timestamp in seconds
            partials: List of PartialEvent objects
            amp_scale: Amplitude multiplier (for attack/release envelopes)
        """
        num_partials = len(partials)
        matrix_data_size = num_partials * 4 * 4  # 4 columns, float32 each
        frame_size = 8 + 4 + 4 + 4 + 4 + 4 + 4 + matrix_data_size
        padding = (8 - (frame_size % 8)) % 8
        frame_size += padding

        f.write(b'1TRC')
        f.write(struct.pack('>I', frame_size))
        f.write(struct.pack('>d', time))
        f.write(struct.pack('>I', 0))        # stream ID
        f.write(struct.pack('>I', 1))        # num matrices

        # Matrix header
        f.write(b'1TRC')
        f.write(struct.pack('>I', 0x0004))   # float32
        f.write(struct.pack('>I', num_partials))
        f.write(struct.pack('>I', 4))        # columns

        # Partial data
        for i, p in enumerate(partials):
            f.write(struct.pack('>f', float(i)))
            f.write(struct.pack('>f', p.frequency))
            f.write(struct.pack('>f', p.amplitude * amp_scale))
            f.write(struct.pack('>f', 0.0))  # phase

        # Padding to 8-byte boundary
        f.write(b'\x00' * padding)

    # -- public entry point -------------------------------------------------

    def convert(self, input_path: str, output_path: str = None,
                musicxml_path: str = None, sdif_path: str = None):
        """Load input and export to MusicXML and/or SDIF.

        For a single output, pass ``output_path`` (format inferred from
        extension).  For dual output, pass ``musicxml_path`` and/or
        ``sdif_path`` explicitly.
        """
        # Clean any residual shell escapes
        input_path = input_path.replace("\\ ", " ").replace("\\'", "'").replace('\\"', '"').strip()
        path = Path(input_path)

        print(f"\n  Input: {input_path}")

        if path.is_dir():
            print("  Detected: directory of partial files")
            frames = self.load_from_directory(input_path)
        elif path.is_file() and path.suffix.lower() == '.sdif':
            print("  Detected: SDIF file")
            frames = self.load_from_sdif(input_path)
        elif path.is_file():
            print("  Detected: single JSON file")
            frames = self.load_from_single_json(input_path)
        else:
            print(f"  Error: path not found: {input_path}")
            return

        if not frames:
            print("  Error: no frames loaded from input.")
            return

        print(f"  Frames: {len(frames)}  |  "
              f"Duration: ~{frames[-1].time:.1f}s")

        # Calibrate dynamics from actual amplitude range in data
        self._calibrate_dynamics(frames)

        # Melodic mode: infer tempo if user set "auto"
        if self.mode == "melodic" and self.auto_tempo:
            inferred = self._infer_tempo(frames)
            if inferred != self.tempo:
                print(f"  Using inferred tempo: q={inferred} (was q={self.tempo})")
                self.tempo = inferred
                # Recompute grid since tempo changed
                self._grid_size = self._compute_grid_size()

        # Melodic mode: enforce minimum note duration and convert very
        # short notes to grace notes.
        # Grace note threshold: shorter than a 32nd note at current tempo.
        # Min duration threshold: 32nd note — shorter notes get bumped up.
        if self.mode == "melodic" and len(frames) >= 2:
            # 32nd note = 7.5 / tempo seconds
            grace_threshold = 7.5 / self.tempo * 0.5  # < half a 32nd → grace
            min_dur_sec = 7.5 / self.tempo              # 32nd note minimum

            main_frames = []
            grace_count = 0
            for f in frames:
                if not f.partials:
                    main_frames.append(f)
                    continue
                dur = f.partials[0].duration
                if dur > 0 and dur < grace_threshold:
                    # Very short note — make it a grace note
                    p = f.partials[0]
                    f.partials[0] = PartialEvent(
                        time=p.time, frequency=p.frequency,
                        amplitude=p.amplitude, duration=-1.0,  # sentinel
                    )
                    grace_count += 1
                    main_frames.append(f)
                elif dur > 0 and dur < min_dur_sec:
                    # Short but not tiny — enforce minimum duration
                    p = f.partials[0]
                    f.partials[0] = PartialEvent(
                        time=p.time, frequency=p.frequency,
                        amplitude=p.amplitude, duration=min_dur_sec,
                    )
                    main_frames.append(f)
                else:
                    main_frames.append(f)
            frames = main_frames
            if grace_count:
                print(f"  Grace notes: {grace_count} very short notes → grace notes")

        # Melodic mode: trim trailing artifact notes.
        # Pitch trackers often report noise/vocal fry at the tail of a sound.
        # If the last note(s) are significantly quieter than the peak amplitude
        # in the melody, they're likely artifacts rather than intentional notes.
        if self.mode == "melodic" and len(frames) >= 3:
            all_amps = [f.partials[0].amplitude for f in frames
                        if f.partials]
            if all_amps:
                peak_amp = max(all_amps)
                trim_threshold = peak_amp * 0.15
                trim_count = 0
                while len(frames) >= 2:
                    last = frames[-1]
                    if not last.partials:
                        break
                    last_amp = last.partials[0].amplitude
                    if last_amp < trim_threshold:
                        frames.pop()
                        trim_count += 1
                    else:
                        break
                if trim_count:
                    print(f"  Tail trim: removed {trim_count} trailing artifact note(s)")

        # Resolve output paths
        if output_path and not musicxml_path and not sdif_path:
            output_path = output_path.replace("\\ ", " ").replace("\\'", "'").replace('\\"', '"').strip()
            if output_path.lower().endswith('.sdif'):
                sdif_path = output_path
            else:
                musicxml_path = output_path

        # Export MusicXML
        if musicxml_path:
            self._build_score(frames, musicxml_path)

        # Export SDIF
        if sdif_path:
            self._write_sdif(frames, sdif_path)
            total_partials = sum(len(f.partials) for f in frames)
            print(f"\n  Created: {sdif_path}")
            print(f"  {len(frames)} frames, {total_partials} total partials")
            print(f"  Format: 1TRC SDIF (compatible with sdif_processor.py)")


# ---------------------------------------------------------------------------
# Interactive TUI
# ---------------------------------------------------------------------------

BANNER = """
\033[36m╔══════════════════════════════════════════════════════════╗
║        Partiels → MusicXML Interactive Converter         ║
╚══════════════════════════════════════════════════════════╝\033[0m
"""


def _prompt_select(question: str, choices: List[str], default: str = None) -> str:
    """Arrow-key select with questionary, fallback to numbered input."""
    if HAS_QUESTIONARY:
        return questionary.select(
            question,
            choices=choices,
            default=default,
            style=TUI_STYLE,
        ).ask()
    else:
        print(f"\n{question}")
        for i, c in enumerate(choices, 1):
            marker = " *" if c == default else ""
            print(f"  {i}. {c}{marker}")
        while True:
            raw = input(f"  Choice [1-{len(choices)}]: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return choices[int(raw) - 1]
            print("  Invalid choice, try again.")


def _prompt_text(question: str, default: str = "") -> str:
    """Text input with questionary, fallback to input()."""
    if HAS_QUESTIONARY:
        return questionary.text(question, default=default, style=TUI_STYLE).ask()
    else:
        result = input(f"{question} [{default}]: ").strip()
        return result if result else default


def _clean_path(raw: str) -> str:
    """Strip shell escape characters from a path string."""
    # Remove backslash-escapes that shell autocomplete can insert
    cleaned = raw.replace("\\ ", " ").replace("\\'", "'").replace('\\"', '"')
    # Also strip wrapping quotes if present
    if (cleaned.startswith('"') and cleaned.endswith('"')) or \
       (cleaned.startswith("'") and cleaned.endswith("'")):
        cleaned = cleaned[1:-1]
    return cleaned.strip()


def _prompt_path(question: str, default: str = "") -> str:
    """Path input with questionary, fallback to input()."""
    if HAS_QUESTIONARY:
        raw = questionary.path(question, default=default, style=TUI_STYLE).ask()
    else:
        raw = input(f"{question} [{default}]: ").strip()
        raw = raw if raw else default
    return _clean_path(raw)


def _prompt_float(question: str, default: float = 0.0) -> float:
    """Numeric float input."""
    raw = _prompt_text(question, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def _prompt_int(question: str, default: int = 0) -> int:
    """Numeric int input."""
    raw = _prompt_text(question, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def run_interactive(args) -> dict:
    """Run the interactive TUI, filling in any missing CLI args. Returns a config dict."""
    print(BANNER)

    config = {}

    # 1. Input path
    if args.input:
        config['input'] = _clean_path(args.input)
    else:
        config['input'] = _prompt_path("Input path (JSON file, SDIF file, or directory)")

    # Auto-detect input type for display
    p = Path(config['input'])
    if p.is_dir():
        print(f"  \033[33m→ Directory detected — will load partial track files\033[0m")
    elif p.is_file() and p.suffix.lower() == '.sdif':
        print(f"  \033[33m→ SDIF file detected — will read 1TRC partial data\033[0m")
    elif p.is_file():
        try:
            with open(p, 'r') as f:
                data = json.load(f)
            if 'results' in data:
                print(f"  \033[33m→ Raw Partiels JSON export detected\033[0m")
            elif 'chords' in data:
                print(f"  \033[33m→ Parsed analysis JSON detected\033[0m")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # Not JSON — will be handled in convert()

    # 2. Pitch quantization
    if args.quantization:
        config['quantization'] = args.quantization
    else:
        config['quantization'] = _prompt_select(
            "Pitch quantization",
            [
                "semitone — standard notation + cents as bend marks",
                "quarter-tone — 0.5 semitone resolution, microtonal accidentals",
                "eighth-tone — 0.25 semitone resolution, finest microtonal detail",
            ],
            default="quarter-tone — 0.5 semitone resolution, microtonal accidentals",
        ).split(" — ")[0]

    # 3. Output mode (ask before rhythm so we can skip the grid prompt in melodic)
    if args.mode:
        config['mode'] = args.mode
    else:
        config['mode'] = _prompt_select(
            "Output mode",
            [
                "harmonic — stacked chords (top N partials per frame)",
                "melodic — single notes (loudest partial per frame)",
            ],
            default="harmonic — stacked chords (top N partials per frame)",
        ).split(" — ")[0]

    # 3b. Rhythmic quantization (harmonic only — melodic always uses 16th)
    if config['mode'] == 'melodic':
        config['rhythm'] = args.rhythm or '16th'
        print(f"  \033[33m→ Rhythm: 16th (auto — melodic mode)\033[0m")
    elif args.rhythm:
        config['rhythm'] = args.rhythm
    else:
        config['rhythm'] = _prompt_select(
            "Rhythmic quantization",
            [
                "free — no grid, proportional durations",
                "16th — sixteenth-note grid",
                "32nd — thirty-second-note grid",
                "8th-triplet — eighth-note triplet grid",
                "16th-triplet — sixteenth-note triplet grid",
            ],
            default="16th — sixteenth-note grid",
        ).split(" — ")[0]

    # 4b. Subharmonic inference (only for microtonal + harmonic)
    is_microtonal = config['quantization'] in ('quarter-tone', 'eighth-tone')
    if config['mode'] == 'harmonic' and is_microtonal:
        if args.subharmonics is not None:
            config['use_subharmonics'] = args.subharmonics
            config['ensemble_only'] = False
        else:
            sub_choice = _prompt_select(
                "Second staff?",
                [
                    "foundations — infer low fundamentals from spectral convergence",
                    "keyboards — same partials, quantized to semitones",
                    "none — ensemble only, no extra staves",
                ],
                default="none — ensemble only, no extra staves",
            ).split(" — ")[0]
            config['use_subharmonics'] = (sub_choice == "foundations")
            config['ensemble_only'] = (sub_choice == "none")

        # 4c. Optional semitone reduction staff (skip if ensemble-only)
        if config.get('ensemble_only'):
            config['include_semitone_staff'] = False
        else:
            semi_choice = _prompt_select(
                "Add a semitone reduction staff (for piano/vibraphone)?",
                [
                    "no — just ensemble + foundations/keyboards",
                    "yes — add a 3rd staff with semitone quantization",
                ],
                default="no — just ensemble + foundations/keyboards",
            ).split(" — ")[0]
            config['include_semitone_staff'] = (semi_choice == "yes")

        # Summary
        if config.get('ensemble_only'):
            desc = f"Ensemble ({config['quantization']})"
        elif config['use_subharmonics']:
            desc = f"Ensemble ({config['quantization']}) + Foundations ({config['quantization']})"
        else:
            desc = f"Ensemble ({config['quantization']}) + Keyboards (semitone)"
        if config['include_semitone_staff']:
            desc += " + Keyboards (semitone)"
        print(f"  \033[33m→ Staves: {desc}\033[0m")
    else:
        config['use_subharmonics'] = False
        config['include_semitone_staff'] = False

    # 5. Max partials (only relevant for harmonic mode)
    if config['mode'] == 'harmonic':
        config['max_partials'] = args.max_partials or _prompt_int(
            "Max partials per frame", 8
        )
    else:
        config['max_partials'] = 1

    # 6. Frame duration
    config['frame_duration'] = args.frame_duration or _prompt_float(
        "Frame duration (seconds)", 0.05
    )

    # 6b. Min amplitude
    # In melodic mode, adaptive amplitude filtering is applied automatically
    # during loading (10% of max amplitude), so no manual threshold is needed.
    # In harmonic mode, keep all partials (amplitude filtering would erase
    # quieter chords since amplitudes vary enormously across chords).
    if args.min_amplitude is not None:
        config['min_amplitude'] = args.min_amplitude
    else:
        config['min_amplitude'] = 0.0  # adaptive filtering handles melodic mode

    # 6c. Min frequency
    config['min_frequency'] = args.min_frequency if args.min_frequency is not None else _prompt_float(
        "Min frequency Hz (filters sub-harmonics)", 80.0
    )

    # 6d. Max frequency
    config['max_frequency'] = args.max_frequency if args.max_frequency is not None else _prompt_float(
        "Max frequency Hz (C8 piano top = 4186)", 4186.0
    )

    # 7. Tempo
    if config['mode'] == 'melodic':
        if args.auto_tempo:
            config['auto_tempo'] = True
            config['tempo'] = args.tempo or 60  # will be overridden by inference
        elif args.tempo:
            config['auto_tempo'] = False
            config['tempo'] = args.tempo
        else:
            tempo_choice = _prompt_select(
                "Tempo",
                [
                    "auto — infer from note density",
                    "manual — set BPM manually",
                ],
                default="auto — infer from note density",
            ).split(" — ")[0]
            config['auto_tempo'] = (tempo_choice == "auto")
            if config['auto_tempo']:
                config['tempo'] = 60  # placeholder, will be inferred
            else:
                config['tempo'] = _prompt_int("Tempo (BPM)", 60)
    else:
        config['auto_tempo'] = False
        config['tempo'] = args.tempo or _prompt_int("Tempo (BPM)", 60)

    # 8. Title — asked first, then used as default output filename
    default_title = Path(config['input']).stem.replace('_', ' ').title()
    config['title'] = args.title or _prompt_text("Score title", default_title)

    # 9. Composer (no interactive prompt — set via CLI flag only)
    config['composer'] = args.composer or ''

    # 10. Output formats (can select multiple)
    if args.output:
        # CLI mode: infer from extension, output only that format
        config['export_musicxml'] = not args.output.lower().endswith('.sdif')
        config['export_sdif'] = not args.output.lower().endswith('.musicxml')
        config['output_base'] = str(Path(args.output).with_suffix(''))
    else:
        fmt = _prompt_select(
            "Output formats",
            [
                "Both MusicXML and SDIF",
                "MusicXML only",
                "SDIF only (for kaleidoscope / sdif_processor)",
            ],
            default="Both MusicXML and SDIF",
        )
        config['export_musicxml'] = 'MusicXML' in fmt or 'Both' in fmt
        config['export_sdif'] = 'SDIF' in fmt or 'Both' in fmt

        # 11. Output base path — derived from score title
        parent = Path(config['input']).parent
        # Convert title to filename: lowercase, spaces to underscores
        title_as_filename = config['title'].lower().replace(' ', '_')
        default_base = str(parent / title_as_filename)
        config['output_base'] = _prompt_text(
            "Output base name (extensions added automatically)", default_base)

    # Summary
    fmt_list = []
    if config['export_musicxml']:
        fmt_list.append('MusicXML')
    if config['export_sdif']:
        fmt_list.append('SDIF')

    print(f"\n\033[36m{'─' * 58}\033[0m")
    print(f"  \033[1mConversion settings:\033[0m")
    print(f"    Input:       {config['input']}")
    print(f"    Output fmt:  {' + '.join(fmt_list)}")
    print(f"    Pitch:       {config['quantization']}")
    print(f"    Rhythm:      {config['rhythm']}")
    print(f"    Mode:        {config['mode']}")
    if config['mode'] == 'harmonic':
        print(f"    Max partials: {config['max_partials']}")
    print(f"    Frame dur:   {config['frame_duration']}s")
    if config['mode'] == 'melodic':
        print(f"    Min amp:     {config['min_amplitude']}")
    print(f"    Min freq:    {config['min_frequency']} Hz")
    print(f"    Max freq:    {config['max_frequency']} Hz")
    if config.get('auto_tempo'):
        print(f"    Tempo:       auto (inferred from note density)")
    else:
        print(f"    Tempo:       {config['tempo']} BPM")
    print(f"    Title:       {config['title']}")
    if config['quantization'] in ('quarter-tone', 'eighth-tone') and config['mode'] == 'harmonic':
        if config['use_subharmonics']:
            print(f"    Staves: Ensemble ({config['quantization']}) + Foundations ({config['quantization']})")
        else:
            print(f"    Staves: Ensemble ({config['quantization']}) + Keyboards (semitone)")
        print(f"    Subharmonics: {'yes' if config['use_subharmonics'] else 'no'}")
        if config.get('include_semitone_staff'):
            print(f"    + Semitone reduction staff")
    if config['export_musicxml']:
        print(f"    Output:      {config['output_base']}.musicxml")
    if config['export_sdif']:
        print(f"    Output:      {config['output_base']}.sdif")
    print(f"\033[36m{'─' * 58}\033[0m")

    # Confirm
    if HAS_QUESTIONARY:
        go = questionary.confirm("Proceed with conversion?", default=True, style=TUI_STYLE).ask()
    else:
        go = input("\nProceed? [Y/n]: ").strip().lower() != 'n'

    if not go:
        print("Cancelled.")
        sys.exit(0)

    return config


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Interactive Partiels → MusicXML converter',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                  # fully interactive
  %(prog)s ./data -q quarter-tone -m harmonic -r 16th -o output.musicxml
  %(prog)s input.json --no-interactive      # use all defaults
        """,
    )
    parser.add_argument('input', nargs='?', default=None,
                        help='Input JSON file, SDIF file, or directory of partial files')
    parser.add_argument('-o', '--output', default=None,
                        help='Output MusicXML file')
    parser.add_argument('-q', '--quantization',
                        choices=['semitone', 'quarter-tone', 'eighth-tone'],
                        default=None, help='Pitch quantization mode')
    parser.add_argument('-r', '--rhythm',
                        choices=['free', '16th', '32nd', '8th-triplet', '16th-triplet'],
                        default=None, help='Rhythmic quantization grid')
    parser.add_argument('-m', '--mode',
                        choices=['melodic', 'harmonic'],
                        default=None, help='Output mode')
    parser.add_argument('--max-partials', type=int, default=None,
                        help='Max partials per frame (default: 8)')
    parser.add_argument('--frame-duration', type=float, default=None,
                        help='Frame duration in seconds (default: 0.05)')
    parser.add_argument('--min-amplitude', type=float, default=None,
                        help='Min amplitude threshold 0.0-1.0 (default: 0.1)')
    parser.add_argument('--min-frequency', type=float, default=None,
                        help='Min frequency in Hz (default: 80.0)')
    parser.add_argument('--max-frequency', type=float, default=None,
                        help='Max frequency in Hz (default: 4186.0, C8 piano top)')
    parser.add_argument('--tempo', type=int, default=None,
                        help='Tempo in BPM (default: 60)')
    parser.add_argument('--auto-tempo', action='store_true', default=False,
                        help='Infer tempo from note density (melodic mode)')
    parser.add_argument('--title', default=None, help='Score title')
    parser.add_argument('--composer', default=None, help='Composer name')
    parser.add_argument('--subharmonics', action='store_true', default=None,
                        dest='subharmonics',
                        help='Enable subharmonic inference for keyboard foundation tones')
    parser.add_argument('--no-subharmonics', action='store_false',
                        dest='subharmonics',
                        help='Disable subharmonic inference')
    parser.add_argument('--semitone-staff', action='store_true', default=False,
                        help='Add a semitone reduction staff (3rd staff for piano/vibraphone)')
    parser.add_argument('--ensemble-only', action='store_true', default=False,
                        help='Output only the microtonal ensemble staff (no foundations/keyboards)')
    parser.add_argument('--no-interactive', action='store_true',
                        help='Disable interactive prompts (use defaults for missing args)')
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.no_interactive:
        # Fill defaults for anything not provided
        out = args.output or 'output.musicxml'
        out_base = str(Path(out).with_suffix(''))
        config = {
            'input': args.input or '.',
            'output_base': out_base,
            'export_musicxml': not out.lower().endswith('.sdif'),
            'export_sdif': not out.lower().endswith('.musicxml'),
            'quantization': args.quantization or 'quarter-tone',
            'rhythm': args.rhythm or '16th',
            'mode': args.mode or 'harmonic',
            'max_partials': args.max_partials or 8,
            'frame_duration': args.frame_duration or 0.05,
            'min_amplitude': args.min_amplitude if args.min_amplitude is not None else 0.1,
            'min_frequency': args.min_frequency if args.min_frequency is not None else 80.0,
            'max_frequency': args.max_frequency if args.max_frequency is not None else 4186.0,
            'tempo': args.tempo or 60,
            'title': args.title or 'Partiels Analysis',
            'composer': args.composer or '',
            'use_subharmonics': args.subharmonics if args.subharmonics is not None else True,
            'auto_tempo': args.auto_tempo,
            'include_semitone_staff': args.semitone_staff,
            'ensemble_only': getattr(args, 'ensemble_only', False),
        }
    else:
        config = run_interactive(args)

    # Build converter and run
    converter = UnifiedMusicXMLConverter(
        title=config['title'],
        composer=config['composer'],
        tempo=config['tempo'],
        quantization=config['quantization'],
        mode=config['mode'],
        rhythm=config['rhythm'],
        max_partials=config['max_partials'],
        frame_duration=config['frame_duration'],
        min_amplitude=config['min_amplitude'],
        min_frequency=config['min_frequency'],
        max_frequency=config['max_frequency'],
        use_subharmonics=config.get('use_subharmonics', False),
        auto_tempo=config.get('auto_tempo', False),
        include_semitone_staff=config.get('include_semitone_staff', False),
        ensemble_only=config.get('ensemble_only', False),
    )

    converter.convert(
        config['input'],
        musicxml_path=config['output_base'] + '.musicxml' if config.get('export_musicxml') else None,
        sdif_path=config['output_base'] + '.sdif' if config.get('export_sdif') else None,
    )


if __name__ == "__main__":
    sys.exit(main() or 0)
