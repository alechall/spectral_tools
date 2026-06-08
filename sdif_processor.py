#!/usr/bin/env python3
"""
SDIF Spectral Processor
Reads SDIF files from SPEAR, applies spectral transformations,
and exports to SDIF and MusicXML formats.
"""

import struct
import numpy as np
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass
import argparse
from xml.etree.ElementTree import Element, SubElement, ElementTree


@dataclass
class SDIFFrame:
    """Represents a single SDIF frame with time and partial data"""
    time: float
    partials: List[Tuple[float, float, float]]  # (frequency, amplitude, phase)


@dataclass
class SDIFData:
    """Container for SDIF spectral data"""
    frames: List[SDIFFrame]
    sample_rate: float = 44100.0

    def __init__(self):
        self.frames = []
        self.sample_rate = 44100.0


@dataclass
class Chord:
    """Represents a stable chord grouping of partials"""
    partials: List[Tuple[float, float, float]]  # (frequency, amplitude, phase)
    start_time: float
    end_time: float
    stability_score: float  # 0-1, higher = more stable
    source_group: int = -1  # index of the source chord that generated this (for kaleidoscope boundary tracking)


@dataclass
class ChordSequence:
    """Container for chord-grouped spectral data"""
    chords: List[Chord]
    sample_rate: float = 44100.0

    def __init__(self):
        self.chords = []
        self.sample_rate = 44100.0


class SDIFReader:
    """Reads SDIF files (primarily 1TRC format used by SPEAR)"""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)

    def read(self) -> SDIFData:
        """Read SDIF file and return structured data"""
        data = SDIFData()

        with open(self.filepath, 'rb') as f:
            # Read entire file for simpler frame detection
            file_data = f.read()

        # Find all 1TRC frame signatures (frame-level, not matrix-level)
        # Frame signatures should be at 8-byte aligned positions typically
        pos = 0
        while pos < len(file_data):
            # Look for next "1TRC" signature
            next_pos = file_data.find(b'1TRC', pos)
            if next_pos == -1:
                break

            # Check if this looks like a frame signature (followed by reasonable frame size)
            if next_pos + 8 > len(file_data):
                break

            try:
                frame_size = struct.unpack('>I', file_data[next_pos+4:next_pos+8])[0]

                # Sanity check: frame size should be reasonable (between 32 and 100000 bytes)
                if 32 <= frame_size <= 100000:
                    # Check if there's a matrix signature "1TRC" inside at expected position
                    # Frame structure: sig(4) + size(4) + time(8) + stream_id(4) + num_matrices(4) + matrix_sig(4)
                    matrix_sig_pos = next_pos + 24
                    if matrix_sig_pos + 4 <= len(file_data):
                        matrix_sig = file_data[matrix_sig_pos:matrix_sig_pos+4]
                        if matrix_sig == b'1TRC':
                            # This looks like a frame-level 1TRC!
                            frame = self._read_1trc_frame_from_data(file_data, next_pos)
                            if frame:
                                data.frames.append(frame)
                            # Move past this frame
                            pos = next_pos + frame_size
                            # Align to 8-byte boundary
                            if pos % 8 != 0:
                                pos += (8 - pos % 8)
                            continue

            except:
                pass

            # Not a valid frame, keep searching
            pos = next_pos + 4

        return data

    def _read_1trc_frame_from_data(self, data: bytes, offset: int) -> Optional[SDIFFrame]:
        """Read a 1TRC frame from byte data at given offset"""
        try:
            pos = offset

            # Read frame signature (already verified)
            pos += 4

            # Read frame size
            frame_size = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4

            # Read frame time
            time = struct.unpack('>d', data[pos:pos+8])[0]
            pos += 8

            # Read stream ID
            stream_id = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4

            # Read number of matrices (usually 1)
            num_matrices = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4

            # Read matrix signature
            matrix_sig = data[pos:pos+4]
            pos += 4
            if matrix_sig != b'1TRC':
                return None

            # Read matrix data type
            data_type = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4

            # Read number of rows and columns
            num_rows = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4
            num_cols = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4

            # Read partial data
            partials = []
            for _ in range(num_rows):
                if num_cols >= 4:
                    index = struct.unpack('>f', data[pos:pos+4])[0]
                    pos += 4
                    freq = struct.unpack('>f', data[pos:pos+4])[0]
                    pos += 4
                    amp = struct.unpack('>f', data[pos:pos+4])[0]
                    pos += 4
                    phase = struct.unpack('>f', data[pos:pos+4])[0]
                    pos += 4

                    # Skip additional columns
                    pos += (num_cols - 4) * 4

                    partials.append((freq, amp, phase))
                else:
                    pos += num_cols * 4

            return SDIFFrame(time=time, partials=partials)

        except Exception as e:
            return None

    def _read_1trc_frame(self, f) -> Optional[SDIFFrame]:
        """Read a 1TRC (partial tracking) frame"""
        try:
            # Remember start position (after frame signature, at frame size field)
            frame_start = f.tell() - 4  # -4 for the frame signature we already read

            # Read frame size
            frame_size = struct.unpack('>I', f.read(4))[0]

            # Read frame time
            time = struct.unpack('>d', f.read(8))[0]

            # Read stream ID
            stream_id = struct.unpack('>I', f.read(4))[0]

            # Read matrix signature
            matrix_sig = f.read(4)
            if matrix_sig != b'1TRC':
                return None

            # Read matrix data type (should be float32)
            data_type = struct.unpack('>I', f.read(4))[0]

            # Read number of rows (partials) and columns
            num_rows = struct.unpack('>I', f.read(4))[0]
            num_cols = struct.unpack('>I', f.read(4))[0]

            # Read partial data
            partials = []
            for _ in range(num_rows):
                if num_cols >= 4:
                    # index, frequency, amplitude, phase
                    index = struct.unpack('>f', f.read(4))[0]
                    freq = struct.unpack('>f', f.read(4))[0]
                    amp = struct.unpack('>f', f.read(4))[0]
                    phase = struct.unpack('>f', f.read(4))[0]

                    # Skip any additional columns
                    for _ in range(num_cols - 4):
                        f.read(4)

                    partials.append((freq, amp, phase))
                else:
                    # Skip malformed rows
                    f.read(num_cols * 4)

            # Seek to end of frame based on frame_size, then align
            frame_end = frame_start + frame_size
            if frame_end % 8 != 0:
                frame_end += (8 - frame_end % 8)
            f.seek(frame_end)

            return SDIFFrame(time=time, partials=partials)

        except Exception as e:
            print(f"Error reading frame: {e}")
            return None


class PartielsReader:
    """Reads Partiels JSON export files and converts to SDIF format"""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)

    def read(self) -> SDIFData:
        """Read Partiels JSON and convert to SDIFData format"""
        import json

        with open(self.filepath, 'r') as f:
            data = json.load(f)

        sdif_data = SDIFData()

        if 'results' not in data:
            print(f"Warning: No 'results' key found in {self.filepath}")
            return sdif_data

        # Group partial tracking events by time to create frames
        time_groups = {}

        for channel in data['results']:
            for event in channel:
                time = event.get('time', 0.0)
                freq = event.get('value')

                # Get amplitude from 'extra' array (first element is amplitude/confidence)
                amp = 0.5  # default
                if 'extra' in event and event['extra']:
                    amp = event['extra'][0]

                # Skip invalid frequencies or very low amplitudes
                if freq is None or freq <= 0 or amp < 0.001:
                    continue

                phase = 0.0
                time_key = round(time, 3)

                if time_key not in time_groups:
                    time_groups[time_key] = []

                time_groups[time_key].append((freq, amp, phase))

        # Convert time groups to SDIF frames
        for time in sorted(time_groups.keys()):
            partials = time_groups[time]
            partials.sort(key=lambda p: p[1], reverse=True)
            sdif_data.frames.append(SDIFFrame(time=time, partials=partials))

        print(f"Loaded {len(sdif_data.frames)} frames from Partiels JSON")
        if sdif_data.frames:
            avg_partials = sum(len(f.partials) for f in sdif_data.frames) / len(sdif_data.frames)
            print(f"Average partials per frame: {avg_partials:.1f}")

        return sdif_data

    def read_directory(self) -> SDIFData:
        """Read all JSON files from a directory and merge into single timeline"""
        import json
        from collections import defaultdict

        dir_path = Path(self.filepath)

        # Find all JSON files - first try Partiels naming convention
        json_files = sorted(dir_path.glob("Group 1_Partial *.json"))
        if not json_files:
            # Try broader pattern
            json_files = sorted(dir_path.glob("*.json"))

        if not json_files:
            print(f"Warning: No JSON files found in {dir_path}")
            return SDIFData()

        print(f"  Found {len(json_files)} JSON files")

        # Collect all events from all files
        time_groups = defaultdict(list)

        for filepath in json_files:
            with open(filepath, 'r') as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    print(f"  Warning: Could not parse {filepath.name}, skipping")
                    continue

            if 'results' not in data:
                continue

            for channel in data['results']:
                for event in channel:
                    time = event.get('time', 0.0)
                    freq = event.get('value')

                    if freq is None or freq <= 0:
                        continue

                    amp = 0.5
                    if 'extra' in event and event['extra']:
                        amp = event['extra'][0]

                    if amp < 0.001:
                        continue

                    time_key = round(time, 3)
                    time_groups[time_key].append((freq, amp, 0.0))

        # Convert to SDIF frames — deduplicate partials at same frequency
        sdif_data = SDIFData()
        total_dupes_removed = 0
        for time in sorted(time_groups.keys()):
            raw_partials = time_groups[time]
            # Merge partials with same frequency (within 0.5 Hz), keep loudest
            raw_partials.sort(key=lambda p: p[0])  # sort by freq
            deduped = [raw_partials[0]] if raw_partials else []
            for freq, amp, phase in raw_partials[1:]:
                prev_f, prev_a, prev_p = deduped[-1]
                if abs(freq - prev_f) < 0.5:
                    # Same frequency — keep the louder amplitude
                    deduped[-1] = (prev_f, max(prev_a, amp), prev_p)
                else:
                    deduped.append((freq, amp, phase))
            total_dupes_removed += len(raw_partials) - len(deduped)
            deduped.sort(key=lambda p: p[1], reverse=True)  # sort by amp
            sdif_data.frames.append(SDIFFrame(time=time, partials=deduped))
        if total_dupes_removed > 0:
            print(f"  Removed {total_dupes_removed} duplicate partials across all frames")

        print(f"  Loaded {len(sdif_data.frames)} frames from directory")
        if sdif_data.frames:
            avg_partials = sum(len(f.partials) for f in sdif_data.frames) / len(sdif_data.frames)
            print(f"  Average partials per frame: {avg_partials:.1f}")

        return sdif_data


class MusicXMLReader:
    """Reads MusicXML files and converts to SDIFData format.

    Allows feeding notated melodies/chords back into the spectral
    processing pipeline (kaleidoscope, morph, stretch, etc.).
    Requires music21 (pip install music21).
    """

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)

    def read(self) -> SDIFData:
        """Read MusicXML file and convert to SDIFData.

        Each unique note onset becomes an SDIFFrame.  Notes at the same
        onset are grouped as simultaneous partials (like a chord).

        Pitch → frequency conversion preserves microtonal alterations
        (quarter-tones, eighth-tones) so round-tripping through the
        pipeline keeps spectral accuracy.
        """
        try:
            from music21 import converter, note as m21note, chord as m21chord
            from music21 import tempo as m21tempo, dynamics as m21dynamics
        except ImportError:
            print("Error: music21 is required for MusicXML input.")
            print("Install it with:  pip install music21")
            return SDIFData()

        print(f"  Parsing MusicXML with music21...")
        score = converter.parse(str(self.filepath))

        # Determine tempo (seconds per quarter note)
        bpm = 60.0  # default
        for el in score.flatten():
            if isinstance(el, m21tempo.MetronomeMark):
                if el.number is not None:
                    bpm = el.number
                    break
        secs_per_quarter = 60.0 / bpm

        # Collect (time_seconds, frequency, amplitude) from all parts
        from collections import defaultdict
        time_groups = defaultdict(list)

        for part in score.parts:
            # Try to find a default dynamic for this part
            default_amp = 0.5

            for el in part.flatten().notesAndRests:
                if isinstance(el, m21note.Rest):
                    continue

                # Time in seconds
                offset_seconds = round(float(el.offset) * secs_per_quarter, 4)

                # Gather pitches (single note or chord)
                if isinstance(el, m21chord.Chord):
                    pitches = el.pitches
                elif isinstance(el, m21note.Note):
                    pitches = [el.pitch]
                else:
                    continue

                # Amplitude from dynamics context
                amp = self._get_amplitude(el, part, default_amp)

                for p in pitches:
                    # Frequency: use pitch.frequency which respects
                    # microtonal alterations (accidentalModifier)
                    freq = p.frequency
                    if freq <= 0:
                        continue
                    time_groups[offset_seconds].append((freq, amp, 0.0))

        # Build SDIFData
        sdif_data = SDIFData()
        for t in sorted(time_groups.keys()):
            partials = sorted(time_groups[t], key=lambda p: p[1], reverse=True)
            sdif_data.frames.append(SDIFFrame(time=t, partials=partials))

        print(f"  Loaded {len(sdif_data.frames)} frames from MusicXML")
        if sdif_data.frames:
            avg_partials = sum(len(f.partials) for f in sdif_data.frames) / len(sdif_data.frames)
            duration = sdif_data.frames[-1].time
            print(f"  Average partials per frame: {avg_partials:.1f}")
            print(f"  Duration: {duration:.2f} seconds")
            print(f"  Tempo: {bpm} BPM")

        return sdif_data

    @staticmethod
    def _get_amplitude(element, part, default: float = 0.5) -> float:
        """Extract amplitude (0-1) from dynamics context.

        Looks for the nearest preceding dynamic marking.  Falls back to
        *default* if nothing is found.
        """
        from music21 import dynamics as m21dynamics

        # Check for dynamics attached directly to this note's context
        try:
            dyn = element.getContextByClass(m21dynamics.Dynamic)
            if dyn is not None:
                # Dynamic.volumeScalar is 0-1
                return dyn.volumeScalar if dyn.volumeScalar else default
        except Exception:
            pass

        return default


class SDIFWriter:
    """Writes SDIF files in 1TRC format"""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)

    def write(self, data: SDIFData):
        """Write SDIF data to file"""
        with open(self.filepath, 'wb') as f:
            # Write SDIF header
            f.write(b'SDIF')

            # Write SDIF header size (not file size!)
            # This appears to be the size of the general header (8 bytes after "SDIF")
            f.write(struct.pack('>I', 8))

            # Write SDIF standard header fields
            f.write(struct.pack('>I', 3))  # Standard types version
            f.write(struct.pack('>I', 1))  # Standard header size

            # Write type definitions
            self._write_type_definitions(f)

            # Write frames
            for frame in data.frames:
                self._write_1trc_frame(f, frame)

    def _write_type_definitions(self, f):
        """Write SDIF type definitions"""
        # NVT (Name-Value Table) for metadata
        f.write(b'1NVT')
        f.write(struct.pack('>I', 32))  # Frame size
        f.write(struct.pack('>d', float('-inf')))  # Special time marker (NVT header)
        f.write(struct.pack('>I', 0xFFFFFFFD))  # Special stream ID
        f.write(struct.pack('>I', 1))  # Num matrices
        f.write(b'1NVT')  # Matrix signature
        f.write(struct.pack('>I', 0x0301))  # Matrix data (empty NVT)
        f.write(struct.pack('>I', 0))  # padding
        f.write(struct.pack('>I', 1))  # padding

    def _write_1trc_frame(self, f, frame: SDIFFrame):
        """Write a 1TRC frame"""
        # Frame signature
        f.write(b'1TRC')

        # Calculate frame size
        num_partials = len(frame.partials)
        matrix_data_size = num_partials * 4 * 4  # 4 columns, 4 bytes each
        # Frame structure AFTER sig and size fields:
        # time(8) + stream_id(4) + num_matrices(4) + matrix_sig(4) +
        # data_type(4) + rows(4) + cols(4) + matrix_data
        frame_size = 8 + 4 + 4 + 4 + 4 + 4 + 4 + matrix_data_size

        # Align to 8-byte boundary
        padding = (8 - (frame_size % 8)) % 8
        frame_size += padding

        f.write(struct.pack('>I', frame_size))

        # Frame time
        f.write(struct.pack('>d', frame.time))

        # Stream ID
        f.write(struct.pack('>I', 0))

        # Number of matrices (always 1 for us)
        f.write(struct.pack('>I', 1))

        # Matrix signature
        f.write(b'1TRC')

        # Data type (0x0004 = float32)
        f.write(struct.pack('>I', 0x0004))

        # Rows and columns
        f.write(struct.pack('>I', num_partials))
        f.write(struct.pack('>I', 4))  # index, freq, amp, phase

        # Write partial data
        for i, (freq, amp, phase) in enumerate(frame.partials):
            f.write(struct.pack('>f', float(i)))
            f.write(struct.pack('>f', freq))
            f.write(struct.pack('>f', amp))
            f.write(struct.pack('>f', phase))

        # Padding
        for _ in range(padding):
            f.write(b'\x00')


class ChordGrouper:
    """Groups partials into stable chords based on temporal stability"""

    @staticmethod
    def kaleidoscope_chords(chord_seq: ChordSequence, mode: str = 'auto',
                           rotation_duration: float = 0.5,
                           frames_per_rotation: int = 1,
                           smooth_transitions: bool = True,
                           cyclic_return: bool = True,
                           reflection_mode: str = 'musical',
                           omit_originals: bool = False,
                           shuffle_order: bool = False,
                           max_partials_per_chord: int = 12,
                           upward_rotation: bool = False,
                           rise_semitones: float = 24.0,
                           fade_low_hz: float = 50.0,
                           fade_high_hz: float = 2500.0,
                           psychoacoustic_spacing: bool = False,
                           scalar_motion: bool = False) -> ChordSequence:
        """
        Apply kaleidoscope reflection with temporal evolution to chord sequences

        Modes:
        - 'auto': Automatically choose based on chord count
        - 'temporal_evolution': Single chord - rotate reflections over time
        - 'sequential_evolution': Multiple chords - complete rotation for each chord in sequence
        - 'interweave': Two chords - interleave chord 1 and 2 with reflections
        - 'morph': Two chords - morph between the two chords with reflections

        Args:
            chord_seq: Input chord sequence
            mode: Kaleidoscope mode
            rotation_duration: Duration for each rotation state (seconds)
            frames_per_rotation: Frames to create per rotation for smooth playback
            smooth_transitions: If True, morph smoothly (glissando); if False, add gaps between states
            cyclic_return: If True, add original chord state at start and end to complete the cycle
            reflection_mode: 'linear' = reflect in Hz space, 'musical' = reflect in semitone space
            omit_originals: If True, use ONLY reflections (no original partials in rotation states)
            shuffle_order: If True, randomize order of reflection centers (prevents predictable patterns)

        Returns:
            New ChordSequence with kaleidoscope effects
        """
        num_chords = len(chord_seq.chords)

        # Auto-select mode based on chord count
        if mode == 'auto':
            if num_chords == 1:
                mode = 'temporal_evolution'
            elif num_chords == 2:
                # Default to interweave for two chords
                mode = 'interweave'
            else:
                # For 3+ chords, just apply standard reflection
                mode = 'simple'

        if mode == 'nested_cycles' and num_chords >= 1:
            return ChordGrouper._kaleidoscope_nested_cycles(
                chord_seq, rotation_duration, frames_per_rotation, cyclic_return, reflection_mode, omit_originals, shuffle_order, True, max_partials_per_chord,
                upward_rotation, rise_semitones, fade_low_hz, fade_high_hz, psychoacoustic_spacing, scalar_motion
            )
        elif mode == 'temporal_evolution' and num_chords >= 1:
            return ChordGrouper._kaleidoscope_temporal_evolution(
                chord_seq, rotation_duration, frames_per_rotation, cyclic_return, reflection_mode, omit_originals, shuffle_order
            )
        elif mode == 'sequential_evolution' and num_chords >= 1:
            return ChordGrouper._kaleidoscope_sequential_evolution(
                chord_seq, rotation_duration, frames_per_rotation, cyclic_return, reflection_mode, omit_originals, shuffle_order
            )
        elif mode == 'interweave' and num_chords >= 2:
            return ChordGrouper._kaleidoscope_interweave(
                chord_seq, rotation_duration, frames_per_rotation
            )
        elif mode == 'morph' and num_chords >= 2:
            return ChordGrouper._kaleidoscope_morph(
                chord_seq, rotation_duration, frames_per_rotation, smooth_transitions
            )
        else:
            # Fallback: return original
            return chord_seq

    @staticmethod
    def _reflect_partial(freq: float, center_freq: float, reflection_mode: str) -> float:
        """
        Reflect a frequency around a center frequency

        Args:
            freq: Frequency to reflect
            center_freq: Center frequency for reflection
            reflection_mode: 'linear' = Hz space, 'musical' = semitone space

        Returns:
            Reflected frequency
        """
        if reflection_mode == 'musical':
            # Convert to MIDI note numbers (semitone space)
            # A4 = 440 Hz = MIDI 69
            freq_midi = 69 + 12 * np.log2(freq / 440.0) if freq > 0 else 0
            center_midi = 69 + 12 * np.log2(center_freq / 440.0) if center_freq > 0 else 0

            # Reflect in semitone space
            distance_midi = freq_midi - center_midi
            reflected_midi = center_midi - distance_midi

            # Convert back to Hz
            reflected_freq = 440.0 * (2.0 ** ((reflected_midi - 69) / 12.0))

            # Ensure minimum frequency (no clamping at 20 Hz for musical mode)
            return max(10.0, reflected_freq)
        else:
            # Linear reflection in Hz space
            distance = freq - center_freq
            reflected_freq = center_freq - distance
            return max(20.0, reflected_freq)

    @staticmethod
    def _kaleidoscope_temporal_evolution(chord_seq: ChordSequence,
                                        rotation_duration: float,
                                        frames_per_rotation: int,
                                        cyclic_return: bool = True,
                                        reflection_mode: str = 'musical',
                                        omit_originals: bool = False,
                                        shuffle_order: bool = False) -> ChordSequence:
        """
        Single chord mode: Create temporal evolution of reflections

        Takes the partials in the chord and creates rotations where each
        partial becomes the center of reflection in sequence.
        """
        result_seq = ChordSequence()
        result_seq.sample_rate = chord_seq.sample_rate

        # Use the first chord (or all if multiple)
        for source_chord in chord_seq.chords:
            partials = source_chord.partials
            num_partials = len(partials)

            if num_partials == 0:
                continue

            current_time = source_chord.start_time

            # Add original chord at the start if cyclic return
            if cyclic_return:
                original_chord = Chord(
                    partials=partials[:],
                    start_time=current_time,
                    end_time=current_time + rotation_duration,
                    stability_score=source_chord.stability_score
                )
                result_seq.chords.append(original_chord)
                current_time += rotation_duration

            # Create order of reflection centers (shuffled or sequential)
            rotation_order = list(range(num_partials))
            if shuffle_order:
                import random
                random.shuffle(rotation_order)

            # Create rotation for each partial as center
            for i, rotation_idx in enumerate(rotation_order):
                # Use this partial's frequency as the center
                center_freq = partials[rotation_idx][0]

                # Calculate amplitude balance based on position in rotation cycle
                # Progress through rotation: 0.0 at start, 0.5 at middle, 1.0 at end
                progress = i / (num_partials - 1) if num_partials > 1 else 0.5

                # Use a triangle wave: fade originals down to middle, then back up
                # This creates maximum reflection prominence in the middle of the cycle
                if progress <= 0.5:
                    # First half: fade originals down, reflections up
                    original_scale = 1.0 - (progress * 2.0 * 0.7)  # goes from 1.0 to 0.3
                    reflection_scale = progress * 2.0  # goes from 0.0 to 1.0
                else:
                    # Second half: fade originals back up, reflections down
                    original_scale = 0.3 + ((progress - 0.5) * 2.0 * 0.7)  # goes from 0.3 to 1.0
                    reflection_scale = 1.0 - ((progress - 0.5) * 2.0)  # goes from 1.0 to 0.0

                # Reflect all partials around this center
                reflected_partials = []
                for freq, amp, phase in partials:
                    if not omit_originals:
                        # Add original with scaled amplitude
                        reflected_partials.append((freq, amp * original_scale, phase))

                    # Add reflection with scaled amplitude (use full amplitude if omitting originals)
                    ref_amp = amp if omit_originals else amp * reflection_scale
                    reflected_freq = ChordGrouper._reflect_partial(freq, center_freq, reflection_mode)
                    reflected_partials.append((reflected_freq, ref_amp, phase))

                # Sort by frequency
                reflected_partials.sort(key=lambda p: p[0])

                # Create new chord for this rotation
                new_chord = Chord(
                    partials=reflected_partials,
                    start_time=current_time,
                    end_time=current_time + rotation_duration,
                    stability_score=source_chord.stability_score
                )
                result_seq.chords.append(new_chord)
                current_time += rotation_duration

            # Add original chord at the end if cyclic return
            if cyclic_return:
                final_chord = Chord(
                    partials=partials[:],
                    start_time=current_time,
                    end_time=current_time + rotation_duration,
                    stability_score=source_chord.stability_score
                )
                result_seq.chords.append(final_chord)

        return result_seq

    @staticmethod
    def _kaleidoscope_sequential_evolution(chord_seq: ChordSequence,
                                           rotation_duration: float,
                                           frames_per_rotation: int,
                                           cyclic_return: bool = True,
                                           reflection_mode: str = 'musical',
                                           omit_originals: bool = False,
                                           shuffle_order: bool = False) -> ChordSequence:
        """
        Sequential evolution mode: Apply temporal evolution to each chord in sequence

        Goes through chord 1 and does complete temporal evolution (rotating through
        all partials as reflection centers), then moves to chord 2 and does the same,
        and so on.
        """
        result_seq = ChordSequence()
        result_seq.sample_rate = chord_seq.sample_rate

        current_time = 0.0

        # Process each chord sequentially
        for chord_idx, source_chord in enumerate(chord_seq.chords):
            partials = source_chord.partials
            num_partials = len(partials)

            if num_partials == 0:
                continue

            # Add original chord at the start if cyclic return
            if cyclic_return:
                original_chord = Chord(
                    partials=partials[:],
                    start_time=current_time,
                    end_time=current_time + rotation_duration,
                    stability_score=source_chord.stability_score
                )
                result_seq.chords.append(original_chord)
                current_time += rotation_duration

            # Create order of reflection centers (shuffled or sequential)
            rotation_order = list(range(num_partials))
            if shuffle_order:
                import random
                random.shuffle(rotation_order)

            # Create rotation for each partial as center
            for i, rotation_idx in enumerate(rotation_order):
                # Use this partial's frequency as the center
                center_freq = partials[rotation_idx][0]

                # Calculate amplitude balance based on position in rotation cycle
                # Progress through rotation: 0.0 at start, 0.5 at middle, 1.0 at end
                progress = i / (num_partials - 1) if num_partials > 1 else 0.5

                # Use a triangle wave: fade originals down to middle, then back up
                # This creates maximum reflection prominence in the middle of the cycle
                if progress <= 0.5:
                    # First half: fade originals down, reflections up
                    original_scale = 1.0 - (progress * 2.0 * 0.7)  # goes from 1.0 to 0.3
                    reflection_scale = progress * 2.0  # goes from 0.0 to 1.0
                else:
                    # Second half: fade originals back up, reflections down
                    original_scale = 0.3 + ((progress - 0.5) * 2.0 * 0.7)  # goes from 0.3 to 1.0
                    reflection_scale = 1.0 - ((progress - 0.5) * 2.0)  # goes from 1.0 to 0.0

                # Reflect all partials around this center
                reflected_partials = []
                for freq, amp, phase in partials:
                    if not omit_originals:
                        # Add original with scaled amplitude
                        reflected_partials.append((freq, amp * original_scale, phase))

                    # Add reflection with scaled amplitude (use full amplitude if omitting originals)
                    ref_amp = amp if omit_originals else amp * reflection_scale
                    reflected_freq = ChordGrouper._reflect_partial(freq, center_freq, reflection_mode)
                    reflected_partials.append((reflected_freq, ref_amp, phase))

                # Sort by frequency
                reflected_partials.sort(key=lambda p: p[0])

                # Create new chord for this rotation
                new_chord = Chord(
                    partials=reflected_partials,
                    start_time=current_time,
                    end_time=current_time + rotation_duration,
                    stability_score=source_chord.stability_score
                )
                result_seq.chords.append(new_chord)
                current_time += rotation_duration

            # Add original chord at the end if cyclic return
            if cyclic_return:
                final_chord = Chord(
                    partials=partials[:],
                    start_time=current_time,
                    end_time=current_time + rotation_duration,
                    stability_score=source_chord.stability_score
                )
                result_seq.chords.append(final_chord)
                current_time += rotation_duration

        return result_seq

    @staticmethod
    def _kaleidoscope_interweave(chord_seq: ChordSequence,
                                rotation_duration: float,
                                frames_per_rotation: int) -> ChordSequence:
        """
        Two chord mode: Interweave chord 1 and 2 with reflections

        Alternates between chord 1 and chord 2, with each subsequent
        reflection also being interwoven sequentially.
        """
        result_seq = ChordSequence()
        result_seq.sample_rate = chord_seq.sample_rate

        if len(chord_seq.chords) < 2:
            return chord_seq

        chord1 = chord_seq.chords[0]
        chord2 = chord_seq.chords[1]

        # Calculate center frequencies for each chord
        if chord1.partials:
            center1 = np.mean([f for f, _, _ in chord1.partials])
        else:
            center1 = 440.0

        if chord2.partials:
            center2 = np.mean([f for f, _, _ in chord2.partials])
        else:
            center2 = 440.0

        # Create reflections for both chords
        def reflect_chord(chord, center):
            reflected = []
            for freq, amp, phase in chord.partials:
                # Original
                reflected.append((freq, amp, phase))
                # Reflection
                distance = freq - center
                ref_freq = max(20.0, center - distance)
                reflected.append((ref_freq, amp * 0.7, phase))
            reflected.sort(key=lambda p: p[0])
            return reflected

        reflected1 = reflect_chord(chord1, center1)
        reflected2 = reflect_chord(chord2, center2)

        # Interweave: alternate between the two
        current_time = 0.0
        max_iterations = max(len(chord1.partials), len(chord2.partials), 4)

        for i in range(max_iterations):
            # Alternate between chord 1 and chord 2
            if i % 2 == 0:
                partials = reflected1
            else:
                partials = reflected2

            new_chord = Chord(
                partials=partials,
                start_time=current_time,
                end_time=current_time + rotation_duration,
                stability_score=1.0
            )
            result_seq.chords.append(new_chord)
            current_time += rotation_duration

        return result_seq

    @staticmethod
    def _kaleidoscope_morph(chord_seq: ChordSequence,
                           rotation_duration: float,
                           frames_per_rotation: int,
                           smooth_transitions: bool = True) -> ChordSequence:
        """
        Two chord mode: Create reflection that morphs between two chords

        Smoothly interpolates between chord 1 and chord 2, applying
        reflections at each morph stage.

        Args:
            chord_seq: Input chord sequence
            rotation_duration: Duration for each morph state
            frames_per_rotation: Number of frames per rotation
            smooth_transitions: If True, connect smoothly (glissando);
                              if False, add silent gaps between morph states
        """
        result_seq = ChordSequence()
        result_seq.sample_rate = chord_seq.sample_rate

        if len(chord_seq.chords) < 2:
            return chord_seq

        chord1 = chord_seq.chords[0]
        chord2 = chord_seq.chords[1]

        # Number of morph steps
        num_steps = 8

        # Calculate centers
        if chord1.partials:
            center1 = np.mean([f for f, _, _ in chord1.partials])
        else:
            center1 = 440.0

        if chord2.partials:
            center2 = np.mean([f for f, _, _ in chord2.partials])
        else:
            center2 = 440.0

        current_time = 0.0

        for step in range(num_steps + 1):
            morph_factor = step / num_steps

            # Interpolate center frequency
            center = center1 * (1 - morph_factor) + center2 * morph_factor

            # Match and morph partials
            max_partials = max(len(chord1.partials), len(chord2.partials))
            p1 = list(chord1.partials) + [(0, 0, 0)] * (max_partials - len(chord1.partials))
            p2 = list(chord2.partials) + [(0, 0, 0)] * (max_partials - len(chord2.partials))

            # Morph partials
            morphed_partials = []
            for (f1, a1, ph1), (f2, a2, ph2) in zip(p1, p2):
                freq = f1 * (1 - morph_factor) + f2 * morph_factor
                amp = a1 * (1 - morph_factor) + a2 * morph_factor
                phase = ph1 * (1 - morph_factor) + ph2 * morph_factor

                if freq > 20.0 and amp > 0.001:
                    # Original
                    morphed_partials.append((freq, amp, phase))
                    # Reflection
                    distance = freq - center
                    ref_freq = max(20.0, center - distance)
                    morphed_partials.append((ref_freq, amp * 0.7, phase))

            morphed_partials.sort(key=lambda p: p[0])

            new_chord = Chord(
                partials=morphed_partials,
                start_time=current_time,
                end_time=current_time + rotation_duration,
                stability_score=1.0
            )
            result_seq.chords.append(new_chord)
            current_time += rotation_duration

            # Add silent gap between morph states if not smooth transitions
            if not smooth_transitions and step < num_steps:
                gap_duration = 0.05  # 50ms gap
                silent_chord = Chord(
                    partials=[],  # Empty partials = silence
                    start_time=current_time,
                    end_time=current_time + gap_duration,
                    stability_score=0.0
                )
                result_seq.chords.append(silent_chord)
                current_time += gap_duration

        return result_seq

    @staticmethod
    def _octave_fold(freq: float, upper_limit: float = 2200.0, lower_limit: float = 100.0) -> float:
        """
        Recursively fold frequencies into optimal range using octave doubling/halving

        Args:
            freq: Frequency to fold
            upper_limit: Upper frequency limit (default: 2200 Hz)
            lower_limit: Lower frequency limit (default: 100 Hz)

        Returns:
            Folded frequency between lower_limit and upper_limit
        """
        # Fold down if too high
        while freq > upper_limit:
            freq = freq / 2.0

        # Fold up if too low
        while freq < lower_limit and freq > 0:
            freq = freq * 2.0

        return freq

    @staticmethod
    def _is_octave_equivalent(freq1: float, freq2: float, tolerance: float = 0.02) -> bool:
        """
        Check if two frequencies are octave equivalents (same pitch class)

        Args:
            freq1: First frequency
            freq2: Second frequency
            tolerance: Tolerance for octave ratio (default 2%)

        Returns:
            True if frequencies are octave equivalents
        """
        if freq1 <= 0 or freq2 <= 0:
            return False

        ratio = max(freq1, freq2) / min(freq1, freq2)

        # Check if ratio is close to a power of 2 (octave relationship)
        import math
        log_ratio = math.log2(ratio)
        nearest_octave = round(log_ratio)

        # Check if we're within tolerance of an octave
        return abs(log_ratio - nearest_octave) < tolerance

    @staticmethod
    def _apply_open_voicing(partials: List[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
        """
        Apply open voicing by spreading partials widely across octaves

        Takes closed-voicing partials and redistributes them across multiple octaves
        to maximize spacing. Keeps within MIDI range (roughly 16 Hz to 12543 Hz).

        Args:
            partials: List of (frequency, amplitude, phase) tuples

        Returns:
            Partials spread across wider frequency range
        """
        import math
        import random

        if len(partials) <= 1:
            return partials

        # MIDI range: C0 (16.35 Hz) to G10 (12543.85 Hz)
        # Use a comfortable range: C1 (32.7 Hz) to C9 (8372 Hz)
        MIN_FREQ = 32.7
        MAX_FREQ = 8372.0

        # Group by pitch class (octave equivalence)
        pitch_classes = {}
        for freq, amp, phase in partials:
            # Find the pitch class by normalizing to MIDI note
            midi_note = 69 + 12 * math.log2(freq / 440.0)
            pitch_class = midi_note % 12

            if pitch_class not in pitch_classes:
                pitch_classes[pitch_class] = []
            pitch_classes[pitch_class].append((freq, amp, phase))

        # Collect unique pitch classes
        unique_classes = sorted(pitch_classes.keys())
        num_classes = len(unique_classes)

        if num_classes <= 1:
            return partials

        # Calculate target octave distribution to maximize spacing
        # Spread across roughly 2-3 octaves for more musical cohesion
        octave_range = min(3, max(2, num_classes // 2))

        # Assign target octaves to each pitch class
        open_voiced_partials = []

        for idx, pitch_class in enumerate(unique_classes):
            # Distribute across octaves with some randomness
            # Lower pitch classes tend toward lower octaves, higher toward upper
            if num_classes > 1:
                position = idx / (num_classes - 1)  # 0.0 to 1.0
            else:
                position = 0.5

            # Add randomness to position (±10%)
            position = position + random.uniform(-0.1, 0.1)
            position = max(0.0, min(1.0, position))

            # Map to octave within range
            # Lower position → lower octaves, higher position → higher octaves
            target_octave_shift = int(position * octave_range) - (octave_range // 2)

            # Take representative partial from this pitch class
            representative = pitch_classes[pitch_class][0]
            freq, amp, phase = representative

            # Shift by target octave
            shifted_freq = freq * (2.0 ** target_octave_shift)

            # Clamp to MIDI range
            while shifted_freq < MIN_FREQ and shifted_freq > 0:
                shifted_freq *= 2.0
            while shifted_freq > MAX_FREQ:
                shifted_freq /= 2.0

            open_voiced_partials.append((shifted_freq, amp, phase))

        # Sort by frequency
        return sorted(open_voiced_partials, key=lambda p: p[0])

    @staticmethod
    def _apply_psychoacoustic_spacing(partials: List[Tuple[float, float, float]]) -> List[Tuple[float, float, float]]:
        """
        Apply psychoacoustic spacing based on critical bandwidth theory (Sethares et al.)

        Ensures partials are spaced according to frequency-dependent critical bandwidth:
        - Low frequencies (<500 Hz): ~100 Hz minimum spacing
        - Mid frequencies (500-1500 Hz): ~160 Hz minimum spacing
        - High frequencies (>1500 Hz): ~20% of center frequency minimum spacing

        When partials are too close, keeps the stronger partial and removes the weaker one.

        Args:
            partials: List of (frequency, amplitude, phase) tuples

        Returns:
            Partials spaced according to psychoacoustic principles
        """
        if len(partials) <= 1:
            return partials

        # Sort by frequency
        sorted_partials = sorted(partials, key=lambda p: p[0])

        # Calculate critical bandwidth for a given frequency (in Hz)
        def critical_bandwidth(freq_hz):
            """
            Critical bandwidth approximation based on Sethares' research
            Returns minimum spacing in Hz for given frequency
            Relaxed by ~50% to allow richer chords while maintaining clarity
            """
            if freq_hz < 500:
                return 50.0  # Low frequencies need ~50 Hz spacing (relaxed from 100)
            elif freq_hz < 1500:
                return 80.0  # Mid frequencies need ~80 Hz spacing (relaxed from 160)
            else:
                # High frequencies: ~10% of center frequency (relaxed from 20%)
                return freq_hz * 0.1

        # Filter partials to maintain critical bandwidth spacing
        filtered = []

        for freq, amp, phase in sorted_partials:
            # Check if this partial is too close to any already-kept partial
            too_close = False

            for kept_freq, kept_amp, kept_phase in filtered:
                # Calculate required spacing at the lower frequency
                lower_freq = min(freq, kept_freq)
                required_spacing = critical_bandwidth(lower_freq)

                # Check if partials are too close
                actual_spacing = abs(freq - kept_freq)

                if actual_spacing < required_spacing:
                    # Too close - keep the stronger one
                    if amp <= kept_amp:
                        # Current partial is weaker, skip it
                        too_close = True
                        break
                    else:
                        # Current partial is stronger, remove the kept one
                        filtered.remove((kept_freq, kept_amp, kept_phase))
                        break

            if not too_close:
                filtered.append((freq, amp, phase))

        # Re-sort by frequency (should already be sorted, but ensure consistency)
        return sorted(filtered, key=lambda p: p[0])

    @staticmethod
    def _limit_partials_preserve_octaves(partials: List[Tuple[float, float, float]],
                                         max_partials: int = 12,
                                         psychoacoustic_spacing: bool = False,
                                         scalar_motion: bool = False) -> List[Tuple[float, float, float]]:
        """
        Limit partials to max count using randomized band-based selection for spectral variety

        Divides spectrum into low/mid/high bands and randomly distributes picks across bands.
        Uses weighted random selection within bands for color variety.
        Octave equivalents don't count against the limit for added richness.

        Args:
            partials: List of (frequency, amplitude, phase) tuples
            max_partials: Maximum number of unique pitch classes to keep

        Returns:
            Limited list of partials with good spectral spread and color variety
        """
        if len(partials) <= max_partials:
            return partials

        import random

        # Adaptive per-chord folding to target musical range (400-900 Hz center)
        # Skip this in scalar_motion mode — we need full frequency range for ascending motion
        if not scalar_motion:
            TARGET_LOW = 400.0
            TARGET_HIGH = 900.0
            if partials:
                avg_freq = sum(f for f, a, p in partials) / len(partials)
                # Fold down if too high
                while avg_freq > TARGET_HIGH:
                    partials = [(f / 2.0, a, p) for f, a, p in partials]
                    avg_freq = avg_freq / 2.0
                # Fold up if too low
                while avg_freq < TARGET_LOW and avg_freq > 0:
                    partials = [(f * 2.0, a, p) for f, a, p in partials]
                    avg_freq = avg_freq * 2.0

        # Define frequency bands for spectral variety
        # Low: 100-700 Hz, Mid: 700-1400 Hz, High: 1400+ Hz
        low_band = [p for p in partials if p[0] < 700]
        mid_band = [p for p in partials if 700 <= p[0] < 1400]
        high_band = [p for p in partials if p[0] >= 1400]

        # Sort each band with bias toward lower frequencies
        # Weight = amplitude * inverse_frequency_factor (favors fundamentals)
        def selection_weight(partial):
            freq, amp, phase = partial
            # Inverse frequency bias: lower frequencies get subtle boost
            # Use gentle bias (1.2x at most) to slightly favor fundamentals
            freq_factor = min(1.2, 500.0 / max(250.0, freq))
            return amp * freq_factor

        low_band_sorted = sorted(low_band, key=selection_weight, reverse=True)
        mid_band_sorted = sorted(mid_band, key=selection_weight, reverse=True)
        high_band_sorted = sorted(high_band, key=selection_weight, reverse=True)

        # Directional register motion: create musical contour instead of random jumps
        # Use class variable to track register tendency across calls
        # Initialize with bias toward low register (index 0)
        if not hasattr(ChordGrouper, '_register_state'):
            ChordGrouper._register_state = {'focus': 0, 'direction': 1, 'steps_in_direction': 0}

        state = ChordGrouper._register_state
        base_per_band = max_partials // 3
        remainder = max_partials % 3

        # Start with balanced distribution across registers
        distributions = [base_per_band, base_per_band, base_per_band]

        # Distribute remainder to current focus band
        for _ in range(remainder):
            distributions[state['focus']] += 1

        # Create directional motion based on mode
        if scalar_motion:
            # Scalar kaleidoscope mode: constantly rising with adaptive voicing width
            # Open voicing at bottom, compact voicing at top

            # Calculate approximate current register position based on recent chord centers
            # This helps determine voicing spread
            if hasattr(ChordGrouper, '_prev_chord_center'):
                import math
                prev_center = ChordGrouper._prev_chord_center

                # Define range
                bottom_freq = 250.0
                top_freq = 1200.0

                # Calculate position (0.0 = bottom, 1.0 = top)
                range_span = math.log2(top_freq / bottom_freq)
                position = math.log2(prev_center / bottom_freq) / range_span
                position = max(0.0, min(1.0, position))

                # Adjust distributions based on position
                # Bottom (0.0-0.3): Wide voicing (spread across all registers)
                # Middle (0.3-0.7): Balanced
                # Top (0.7-1.0): Compact voicing (focus on mid register)
                if position < 0.3:
                    # Bottom - very open voicing (emphasize all registers)
                    distributions = [
                        max_partials // 3 + 1,  # Low
                        max_partials // 3,      # Mid
                        max_partials // 3 - 1   # High
                    ]
                elif position > 0.7:
                    # Top - compact voicing (emphasize mid, reduce extremes)
                    distributions = [
                        max_partials // 3 - 1,  # Low
                        max_partials // 3 + 2,  # Mid (concentrate here)
                        max_partials // 3 - 1   # High
                    ]
                else:
                    # Middle - balanced
                    distributions = [base_per_band, base_per_band, base_per_band]

                # Ensure we use all partials
                total = sum(distributions)
                if total < max_partials:
                    distributions[1] += max_partials - total  # Add to middle
            else:
                # First chord - start with open voicing
                distributions = [
                    max_partials // 3 + 1,
                    max_partials // 3,
                    max_partials // 3 - 1
                ]
        else:
            # Varied motion mode: stepwise with occasional turns and leaps
            motion_choice = random.random()

            if motion_choice < 0.60:  # 60% - small step in current direction
                if state['direction'] != 0:
                    new_focus = max(0, min(2, state['focus'] + state['direction']))
                    if new_focus != state['focus']:
                        # Shift weight toward new register
                        if distributions[state['focus']] > 1:
                            distributions[state['focus']] -= 1
                            distributions[new_focus] += 1
                        state['focus'] = new_focus
                        state['steps_in_direction'] += 1

            elif motion_choice < 0.85:  # 25% - change direction (stepwise in new direction)
                state['direction'] = random.choice([-1, 1])
                new_focus = max(0, min(2, state['focus'] + state['direction']))
                if new_focus != state['focus'] and distributions[state['focus']] > 1:
                    distributions[state['focus']] -= 1
                    distributions[new_focus] += 1
                    state['focus'] = new_focus
                state['steps_in_direction'] = 0

            else:  # 15% - leap to different register
                old_focus = state['focus']
                state['focus'] = random.choice([i for i in range(3) if i != old_focus])
                state['direction'] = 1 if state['focus'] > old_focus else -1
                # Transfer more weight for dramatic leap
                if distributions[old_focus] > 1:
                    amount = min(2, distributions[old_focus] - 1)
                    distributions[old_focus] -= amount
                    distributions[state['focus']] += amount
                state['steps_in_direction'] = 0

        low_count, mid_count, high_count = distributions

        kept_partials = []
        pitch_classes_kept = []

        # Helper to add partials from a band with weighted random selection
        def add_from_band(band_partials, count):
            added = 0
            available_indices = list(range(len(band_partials)))

            while added < count and available_indices:
                # Weighted random selection:
                # 70% chance: pick from top 1-2 strongest
                # 20% chance: pick from 3-5th strongest
                # 10% chance: pick from weaker partials
                rand_val = random.random()

                if rand_val < 0.7 and len(available_indices) > 0:
                    # Pick from strongest available
                    idx = available_indices[0]
                elif rand_val < 0.9 and len(available_indices) > 2:
                    # Pick from mid-strength
                    idx = available_indices[random.randint(2, min(4, len(available_indices) - 1))]
                elif len(available_indices) > 5:
                    # Pick from weaker partials
                    idx = available_indices[random.randint(5, len(available_indices) - 1)]
                else:
                    # Fallback: pick from what's available
                    idx = available_indices[0]

                freq, amp, phase = band_partials[idx]
                available_indices.remove(idx)

                # Check if already have this pitch class
                is_duplicate = any(
                    ChordGrouper._is_octave_equivalent(freq, kept_freq)
                    for kept_freq in pitch_classes_kept
                )
                if not is_duplicate:
                    kept_partials.append((freq, amp, phase))
                    pitch_classes_kept.append(freq)
                    added += 1

        # Select from each band with randomized counts
        if low_band_sorted:
            add_from_band(low_band_sorted, low_count)
        if mid_band_sorted:
            add_from_band(mid_band_sorted, mid_count)
        if high_band_sorted:
            add_from_band(high_band_sorted, high_count)

        # Now add octave duplicates without counting against limit
        for freq, amp, phase in partials:
            is_octave_of_kept = any(
                ChordGrouper._is_octave_equivalent(freq, kept_freq)
                for kept_freq in pitch_classes_kept
            )
            # If octave duplicate and not already in kept_partials
            if is_octave_of_kept and (freq, amp, phase) not in kept_partials:
                kept_partials.append((freq, amp, phase))

        # 25% chance: apply open voicing to spread partials across octaves
        if random.random() < 0.25:
            kept_partials = ChordGrouper._apply_open_voicing(kept_partials)

        # Apply psychoacoustic spacing based on critical bandwidth if enabled
        if psychoacoustic_spacing:
            kept_partials = ChordGrouper._apply_psychoacoustic_spacing(kept_partials)

        # Re-sort by frequency for consistency
        result = sorted(kept_partials, key=lambda p: p[0])

        # Voice-leading optimization: shift register to minimize leap from previous chord
        if hasattr(ChordGrouper, '_prev_chord_center'):
            if result:
                # Calculate current chord center
                current_freqs = [f for f, a, p in result]
                current_center = sum(current_freqs) / len(current_freqs)

                prev_center = ChordGrouper._prev_chord_center

                if scalar_motion:
                    # SCALAR MOTION MODE: Reflections are pre-sorted by pitch in nested_cycles
                    # DO NOT apply octave shifts here - sorting already done!
                    # Just update the center for next chord
                    pass
                else:
                    # STANDARD MODE: Try octave shifts to minimize distance
                    best_shift = 0
                    best_distance = abs(current_center - prev_center)

                    # Try smaller shifts first for smoother voice-leading
                    for shift in [-1, 1, -2, 2, -3, 3]:  # Try up to 3 octaves
                        shifted_center = current_center * (2.0 ** shift)
                        distance = abs(shifted_center - prev_center)

                        # Accept any improvement (even small ones)
                        if distance < best_distance:
                            best_distance = distance
                            best_shift = shift

                    # Apply best shift
                    if best_shift != 0:
                        result = [(f * (2.0 ** best_shift), a, p) for f, a, p in result]
                        current_center = current_center * (2.0 ** best_shift)

                # Update for next chord
                ChordGrouper._prev_chord_center = current_center
        else:
            # First chord - establish center
            if result:
                current_freqs = [f for f, a, p in result]
                ChordGrouper._prev_chord_center = sum(current_freqs) / len(current_freqs)

        return sorted(result, key=lambda p: p[0])

    @staticmethod
    def _kaleidoscope_nested_cycles(chord_seq: ChordSequence,
                                    rotation_duration: float,
                                    frames_per_rotation: int,
                                    cyclic_return: bool = True,
                                    reflection_mode: str = 'musical',
                                    omit_originals: bool = False,
                                    shuffle_order: bool = True,
                                    octave_fold: bool = True,
                                    max_partials_per_chord: int = 12,
                                    upward_rotation: bool = False,
                                    rise_semitones: float = 24.0,
                                    fade_low_hz: float = 50.0,
                                    fade_high_hz: float = 2500.0,
                                    psychoacoustic_spacing: bool = False,
                                    scalar_motion: bool = False) -> ChordSequence:
        """
        Nested anchor cycles: True kaleidoscope mode with layered reflections

        For each partial as anchor (in shuffled order):
          - Start with original chord
          - Cycle through other partials as reflection centers (shuffled)
          - Anchor stays constant, all partials reflected around each center
          - Apply octave folding to prevent ascending into stratosphere
          - Return to original chord

        Creates nested structure: 5 partials → 5 cycles × ~5 states = ~25 total states

        Optional upward rotation sub-mode creates shepard tone illusion:
          - Chords gradually rise over duration
          - Reflected partials enter from below
          - High frequencies fade out, low frequencies fade in
        """
        result_seq = ChordSequence()
        result_seq.sample_rate = chord_seq.sample_rate

        # Calculate total duration for upward rotation
        if upward_rotation and chord_seq.chords:
            total_duration = max(c.end_time for c in chord_seq.chords)
            # Calculate center frequency for reflection
            all_freqs = []
            for chord in chord_seq.chords:
                for freq, _, _ in chord.partials:
                    all_freqs.append(freq)
            center_freq = np.median(all_freqs) if all_freqs else 440.0
            print(f"  Upward rotation enabled: {rise_semitones} semitones over {total_duration:.1f}s")
            print(f"  Reflection center: {center_freq:.1f} Hz")
        else:
            total_duration = 0
            center_freq = 440.0

        # Initialize timeline — cycles run sequentially to avoid overlap
        current_time = chord_seq.chords[0].start_time if chord_seq.chords else 0.0

        # Process each chord in the sequence
        for source_idx, source_chord in enumerate(chord_seq.chords):
            # Limit partials while preserving octave duplicates for variety
            partials = ChordGrouper._limit_partials_preserve_octaves(
                source_chord.partials, max_partials_per_chord, psychoacoustic_spacing, scalar_motion
            )
            num_partials = len(partials)

            if num_partials == 0:
                continue

            print(f"  Limited chord from {len(source_chord.partials)} to {num_partials} partials (preserving octave duplicates)")

            # Adaptive pre-folding: fold all partials if average is too high or too low
            # Skip in scalar_motion mode — need full frequency range for ascending motion
            if octave_fold and not scalar_motion and num_partials > 0:
                total_freq = sum([f for f, a, p in partials])
                avg_freq = total_freq / num_partials
                upper_avg_threshold = 1625.0  # Average above 1625 Hz → fold down
                lower_avg_threshold = 180.0   # Average below 180 Hz → fold up

                if avg_freq > upper_avg_threshold:
                    # Pre-fold down: average too high
                    partials = [(ChordGrouper._octave_fold(f), a, p) for f, a, p in partials]
                    print(f"  Pre-folding DOWN (avg: {avg_freq:.1f} Hz > {upper_avg_threshold:.1f} Hz)")
                elif avg_freq < lower_avg_threshold:
                    # Pre-fold up: average too low
                    partials = [(ChordGrouper._octave_fold(f), a, p) for f, a, p in partials]
                    print(f"  Pre-folding UP (avg: {avg_freq:.1f} Hz < {lower_avg_threshold:.1f} Hz)")

            if not scalar_motion:
                # Start this cycle at source chord's time or after the
                # previous cycle, whichever is later — avoids temporal
                # overlap when source chords are close together
                current_time = max(current_time, source_chord.start_time)

            # Add original chord ONCE at the very beginning (if cyclic_return)
            # Skip in scalar_motion mode — we build one continuous ascending sequence
            if cyclic_return and not scalar_motion:
                folded_originals = [(ChordGrouper._octave_fold(f), a, p) for f, a, p in partials] if octave_fold else partials[:]
                # Limit partials to maintain clarity
                folded_originals = ChordGrouper._limit_partials_preserve_octaves(
                    folded_originals, max_partials_per_chord, psychoacoustic_spacing, scalar_motion
                )
                original_chord = Chord(
                    partials=folded_originals,
                    start_time=current_time,
                    end_time=current_time + rotation_duration,
                    stability_score=source_chord.stability_score,
                    source_group=source_idx
                )
                result_seq.chords.append(original_chord)
                current_time += rotation_duration

            # Create shuffled order of anchors
            import random
            anchor_indices = list(range(num_partials))
            if shuffle_order:
                random.shuffle(anchor_indices)

            # Collect all reflection states for this source chord
            reflection_chords = []

            # For each partial as anchor
            for anchor_idx in anchor_indices:
                anchor_freq, anchor_amp, anchor_phase = partials[anchor_idx]

                # Fold anchor to ensure it stays in range (unless scalar_motion needs full range)
                if octave_fold and not scalar_motion:
                    anchor_freq = ChordGrouper._octave_fold(anchor_freq)

                # Get reflection centers (all partials except anchor)
                reflection_indices = [i for i in range(num_partials) if i != anchor_idx]
                if shuffle_order:
                    random.shuffle(reflection_indices)

                num_reflections = len(reflection_indices)

                # Create reflection state for each center
                for refl_i, center_idx in enumerate(reflection_indices):
                    center_freq = partials[center_idx][0]

                    # Calculate amplitude crossfade (triangle wave pattern)
                    if num_reflections > 1:
                        progress = refl_i / (num_reflections - 1)
                        if progress <= 0.5:
                            original_scale = 1.0 - (progress * 2.0 * 0.7)  # 1.0 to 0.3
                            reflection_scale = progress * 2.0  # 0.0 to 1.0
                        else:
                            original_scale = 0.3 + ((progress - 0.5) * 2.0 * 0.7)  # 0.3 to 1.0
                            reflection_scale = 1.0 - ((progress - 0.5) * 2.0)  # 1.0 to 0.0
                    else:
                        original_scale = 0.5
                        reflection_scale = 1.0

                    # Build reflected chord
                    reflected_partials = []

                    # Always include anchor at full amplitude
                    reflected_partials.append((anchor_freq, anchor_amp, anchor_phase))

                    # Reflect all other partials around center
                    for i, (freq, amp, phase) in enumerate(partials):
                        if i == anchor_idx:
                            # Skip anchor - already added
                            continue

                        # Add original partial (if not omitting)
                        if not omit_originals:
                            reflected_partials.append((freq, amp * original_scale, phase))

                        # Add reflection
                        ref_amp = amp if omit_originals else amp * reflection_scale
                        reflected_freq = ChordGrouper._reflect_partial(freq, center_freq, reflection_mode)

                        # Apply octave folding (unless scalar_motion needs full frequency range)
                        if octave_fold and not scalar_motion:
                            reflected_freq = ChordGrouper._octave_fold(reflected_freq)

                        reflected_partials.append((reflected_freq, ref_amp, phase))

                    # Sort by frequency
                    reflected_partials.sort(key=lambda p: p[0])

                    # Limit partials in the reflected chord to maintain clarity
                    reflected_partials = ChordGrouper._limit_partials_preserve_octaves(
                        reflected_partials, max_partials_per_chord, psychoacoustic_spacing, scalar_motion
                    )

                    # Create chord for this reflection state (don't set time yet)
                    new_chord = Chord(
                        partials=reflected_partials,
                        start_time=0,  # Will set after sorting
                        end_time=0,
                        stability_score=source_chord.stability_score,
                        source_group=source_idx
                    )
                    reflection_chords.append(new_chord)

            if scalar_motion:
                # Scalar motion: each source chord gets its own ascending cycle
                # Sort this chord's reflections by centroid
                def _sc_centroid(ch):
                    if not ch.partials:
                        return 0
                    freqs = [f for f, a, p in ch.partials]
                    amps = [a for f, a, p in ch.partials]
                    ta = sum(amps)
                    if ta == 0:
                        return sum(freqs) / len(freqs)
                    return sum(f * a for f, a in zip(freqs, amps)) / ta

                reflection_chords.sort(key=_sc_centroid)

                # Deduplicate similar chords
                deduped = [reflection_chords[0]] if reflection_chords else []
                for rc in reflection_chords[1:]:
                    if abs(_sc_centroid(rc) - _sc_centroid(deduped[-1])) > 30.0:
                        deduped.append(rc)

                if len(reflection_chords) > len(deduped):
                    print(f"  Scalar: removed {len(reflection_chords) - len(deduped)} similar (kept {len(deduped)})")

                # Apply progressive pitch shift with even spacing in log-frequency
                # Target: evenly spaced centroids from lowest to +12 semitones above highest
                import math
                n_refl = len(deduped)
                if n_refl > 1:
                    first_centroid = _sc_centroid(deduped[0])
                    last_centroid = _sc_centroid(deduped[-1])
                    # Target range: from first centroid to one octave above last
                    target_top = last_centroid * 2.0  # +12 semitones
                    # Even spacing in log space
                    log_bottom = math.log2(max(50, first_centroid))
                    log_top = math.log2(max(100, target_top))

                    for ri, rc in enumerate(deduped):
                        progress = ri / (n_refl - 1)
                        # Target centroid for this position (evenly spaced in log)
                        target_log = log_bottom + progress * (log_top - log_bottom)
                        target_centroid = 2.0 ** target_log
                        # Current centroid
                        current_centroid = _sc_centroid(rc)
                        if current_centroid > 0:
                            shift_ratio = target_centroid / current_centroid
                            rc.partials = [(f * shift_ratio, a, p) for f, a, p in rc.partials]

                        # Fold partials above 3500 Hz back down
                        folded = []
                        for f, a, p in rc.partials:
                            while f > 3500.0:
                                f /= 2.0
                                a *= 0.7
                            folded.append((f, a, p))
                        rc.partials = folded

                # Re-sort after shift for smooth ascending output
                deduped.sort(key=_sc_centroid)

                # Add to result with timing
                for rc in deduped:
                    rc.start_time = current_time
                    rc.end_time = current_time + rotation_duration
                    result_seq.chords.append(rc)
                    current_time += rotation_duration

                print(f"  Scalar cycle: {n_refl} ascending chords for source chord at t={source_chord.start_time:.2f}s")

            else:
                # Standard mode: deduplicate, sort by centroid for flow,
                # then add reflections with timing

                def _std_centroid(ch):
                    if not ch.partials:
                        return 0
                    freqs = [f for f, a, p in ch.partials]
                    amps = [a for f, a, p in ch.partials]
                    ta = sum(amps)
                    if ta == 0:
                        return sum(freqs) / len(freqs)
                    return sum(f * a for f, a in zip(freqs, amps)) / ta

                # Sort by centroid temporarily for deduplication
                reflection_chords.sort(key=_std_centroid)

                # Deduplicate chords with similar centroids (within 30 Hz)
                deduped = [reflection_chords[0]] if reflection_chords else []
                for rc in reflection_chords[1:]:
                    if abs(_std_centroid(rc) - _std_centroid(deduped[-1])) > 30.0:
                        deduped.append(rc)

                # Shuffle so the output doesn't just ascend like scalar mode
                if shuffle_order:
                    random.shuffle(deduped)

                if len(reflection_chords) > len(deduped):
                    print(f"  Removed {len(reflection_chords) - len(deduped)} similar reflections (kept {len(deduped)}) for source chord at t={source_chord.start_time:.2f}s")

                for chord in deduped:
                    chord.start_time = current_time
                    chord.end_time = current_time + rotation_duration
                    result_seq.chords.append(chord)
                    current_time += rotation_duration

                # Add original chord ONCE at the very end (if cyclic_return)
                if cyclic_return:
                    folded_originals = [(ChordGrouper._octave_fold(f), a, p) for f, a, p in partials] if octave_fold else partials[:]
                    folded_originals = ChordGrouper._limit_partials_preserve_octaves(
                        folded_originals, max_partials_per_chord, psychoacoustic_spacing, scalar_motion
                    )
                    final_chord = Chord(
                        partials=folded_originals,
                        start_time=current_time,
                        end_time=current_time + rotation_duration,
                        stability_score=source_chord.stability_score,
                        source_group=source_idx
                    )
                    result_seq.chords.append(final_chord)
                    current_time += rotation_duration

        # Apply upward rotation as post-process if enabled
        if upward_rotation:
            print(f"  Applying upward rotation post-process ({rise_semitones} semitones)")
            result_seq = ChordGrouper._apply_upward_rotation_to_sequence(
                result_seq, rise_semitones, fade_low_hz, fade_high_hz
            )

        return result_seq

    @staticmethod
    def _apply_upward_rotation_to_sequence(chord_seq: ChordSequence,
                                           rise_semitones: float,
                                           fade_low_hz: float,
                                           fade_high_hz: float) -> ChordSequence:
        """
        Apply upward rotation transformation to entire chord sequence

        This is applied AFTER all kaleidoscope reflections are complete
        """
        if not chord_seq.chords:
            return chord_seq

        # Get total duration and center frequency
        total_duration = max(c.end_time for c in chord_seq.chords)
        all_freqs = []
        for chord in chord_seq.chords:
            for freq, _, _ in chord.partials:
                all_freqs.append(freq)
        center_freq = np.median(all_freqs) if all_freqs else 440.0

        result_seq = ChordSequence()
        result_seq.sample_rate = chord_seq.sample_rate

        for chord in chord_seq.chords:
            # Calculate shift for this chord's time position
            progress = chord.start_time / total_duration if total_duration > 0 else 0
            shift_ratio = 2.0 ** (rise_semitones * progress / 12.0)

            new_partials = []

            for freq, amp, phase in chord.partials:
                # ORIGINAL - shifted upward
                shifted_freq = freq * shift_ratio

                # Fade high frequencies
                if shifted_freq > fade_high_hz:
                    fade_factor = max(0.0, 1.0 - (shifted_freq - fade_high_hz) / fade_high_hz)
                    shifted_amp = amp * fade_factor
                else:
                    shifted_amp = amp

                if shifted_amp > 0.001:
                    new_partials.append((shifted_freq, shifted_amp, phase))

                # REFLECTED - enter from below
                distance = freq - center_freq
                reflected_freq = center_freq - distance

                # Drop down octaves
                while reflected_freq > fade_low_hz:
                    reflected_freq /= 2.0

                reflected_shifted = reflected_freq * shift_ratio

                # Fade in low frequencies
                if reflected_shifted < fade_low_hz:
                    fade_factor = reflected_shifted / fade_low_hz
                    reflected_amp = amp * fade_factor * 0.7
                else:
                    reflected_amp = amp * 0.7

                if 20.0 < reflected_shifted < 20000.0 and reflected_amp > 0.001:
                    new_partials.append((reflected_shifted, reflected_amp, phase))

            # Sort and create new chord
            new_partials.sort(key=lambda p: p[0])
            new_chord = Chord(
                partials=new_partials,
                start_time=chord.start_time,
                end_time=chord.end_time,
                stability_score=chord.stability_score
            )
            result_seq.chords.append(new_chord)

        return result_seq

    @staticmethod
    def group_to_chords(data: SDIFData, window_size: float = 0.1,
                       freq_tolerance: float = 0.05, amp_weight: float = 0.5,
                       mode: str = 'simple', transition_threshold: float = 0.05,
                       transition_gap: float = 0.1) -> ChordSequence:
        """
        Group partials into stable chords based on stability over time

        Args:
            data: Input SDIF data with frames
            window_size: Time window in seconds for chord duration
            freq_tolerance: Maximum frequency variation (as ratio) to be considered stable
            amp_weight: Weight for amplitude stability (0-1), vs frequency stability
            mode: 'simple' = one chord per window (default),
                  'stability' = analyze stability,
                  'transition' = detect chord boundaries by frequency changes
            transition_threshold: For transition mode, percentage change threshold (e.g., 0.05 = 5%)
            transition_gap: For transition mode, gap duration between chords in seconds (default: 0.1)

        Returns:
            ChordSequence with grouped chords
        """
        if not data.frames:
            return ChordSequence()

        if mode == 'simple':
            return ChordGrouper._group_to_chords_simple(data, window_size)
        elif mode == 'transition':
            return ChordGrouper._group_to_chords_transition(data, transition_threshold, transition_gap)
        else:
            return ChordGrouper._group_to_chords_stability(data, window_size, freq_tolerance, amp_weight)

    @staticmethod
    def _group_to_chords_simple(data: SDIFData, window_size: float) -> ChordSequence:
        """
        Simple chord grouping: create one chord per time window using representative frame

        This creates distinct chords without trying to find stable partials,
        ensuring that each time window gets its own unique chord.
        """
        chord_seq = ChordSequence()
        chord_seq.sample_rate = data.sample_rate

        if not data.frames:
            return chord_seq

        current_time = 0.0
        total_duration = data.frames[-1].time

        while current_time < total_duration:
            window_end = current_time + window_size

            # Get frames within this window
            window_frames = [
                f for f in data.frames
                if current_time <= f.time < window_end
            ]

            if window_frames:
                # Use the middle frame as representative (or first if only one)
                rep_frame = window_frames[len(window_frames) // 2]

                # Create chord from this frame's partials
                if rep_frame.partials:
                    chord = Chord(
                        partials=rep_frame.partials[:],  # Copy partials
                        start_time=current_time,
                        end_time=window_end,
                        stability_score=1.0
                    )
                    chord_seq.chords.append(chord)

            current_time = window_end

        return chord_seq

    @staticmethod
    def _group_to_chords_stability(data: SDIFData, window_size: float,
                                   freq_tolerance: float, amp_weight: float) -> ChordSequence:
        """
        Stability-based chord grouping: analyze partial stability over time windows
        """
        chord_seq = ChordSequence()
        chord_seq.sample_rate = data.sample_rate

        # Analyze partial tracks over time windows
        current_time = 0.0
        while current_time < data.frames[-1].time:
            window_end = current_time + window_size

            # Get frames within window
            window_frames = [
                f for f in data.frames
                if current_time <= f.time < window_end
            ]

            if not window_frames:
                current_time = window_end
                continue

            # Analyze partial stability within window
            stable_partials = ChordGrouper._find_stable_partials(
                window_frames, freq_tolerance, amp_weight
            )

            if stable_partials:
                # Calculate stability score
                stability = ChordGrouper._calculate_stability(
                    window_frames, stable_partials, freq_tolerance
                )

                chord = Chord(
                    partials=stable_partials,
                    start_time=current_time,
                    end_time=window_end,
                    stability_score=stability
                )
                chord_seq.chords.append(chord)

            current_time = window_end

        return chord_seq

    @staticmethod
    def _group_to_chords_transition(data: SDIFData, transition_threshold: float,
                                    transition_gap: float) -> ChordSequence:
        """
        Transition-based chord grouping: detect chord boundaries by analyzing
        frequency changes between consecutive frames.

        Handles SDIF files with attack/release envelope frames (from
        interactive_converter) by filtering out low-amplitude frames before
        comparison.  Also handles varying partial counts between chords by
        comparing the intersection of frequency sets rather than requiring
        identical counts.

        Args:
            data: Input SDIF data
            transition_threshold: Percentage change threshold (e.g., 0.05 = 5%)
            transition_gap: Gap duration between chords in seconds

        Returns:
            ChordSequence with chords separated at transition points
        """
        chord_seq = ChordSequence()
        chord_seq.sample_rate = data.sample_rate

        if not data.frames or len(data.frames) < 2:
            return chord_seq

        # ---- Step 1: Identify "sustain" frames (significant amplitude) ----
        # SDIF files from interactive_converter write attack/release envelope
        # frames at ~1% / 0.1% amplitude.  We only want to compare the real
        # sustain frames when detecting chord changes.
        amp_floor = 0.005  # frames below this max-amp are envelope artifacts
        sustain_indices = []
        for i, frame in enumerate(data.frames):
            if not frame.partials:
                continue
            max_amp = max(a for _, a, _ in frame.partials)
            if max_amp >= amp_floor:
                sustain_indices.append(i)

        if not sustain_indices:
            # All frames below floor — fall back to using every frame
            sustain_indices = list(range(len(data.frames)))

        # ---- Step 2: Detect transitions between consecutive sustain frames --
        transition_indices = []
        prev_freqs = None
        prev_si = None

        for si in sustain_indices:
            frame = data.frames[si]
            freqs = sorted([f for f, a, _ in frame.partials if f > 0])
            if not freqs:
                continue

            if prev_freqs is not None:
                # Different partial count is itself a strong transition signal
                if len(freqs) != len(prev_freqs):
                    transition_indices.append(si)
                else:
                    # Same count — compare sorted frequency lists
                    max_change = max(
                        abs(f2 - f1) / f1
                        for f1, f2 in zip(prev_freqs, freqs)
                        if f1 > 0
                    )
                    if max_change > transition_threshold:
                        transition_indices.append(si)

            prev_freqs = freqs
            prev_si = si

        # ---- Step 3: Enforce minimum gap between transitions ----------------
        if transition_gap > 0 and transition_indices:
            filtered = [transition_indices[0]]
            for ti in transition_indices[1:]:
                if data.frames[ti].time - data.frames[filtered[-1]].time >= transition_gap:
                    filtered.append(ti)
            transition_indices = filtered

        # ---- Step 4: Build chords from the boundary list --------------------
        chord_boundaries = [0] + transition_indices + [len(data.frames)]

        for i in range(len(chord_boundaries) - 1):
            start_idx = chord_boundaries[i]
            end_idx = chord_boundaries[i + 1]

            if start_idx >= len(data.frames) or end_idx > len(data.frames):
                continue

            chord_frames = data.frames[start_idx:end_idx]
            if not chord_frames:
                continue

            # Pick the representative frame: prefer the sustain frame with the
            # highest max-amplitude (best snapshot of the actual chord).
            rep_frame = max(
                chord_frames,
                key=lambda fr: max((a for _, a, _ in fr.partials), default=0)
            )

            if rep_frame.partials:
                start_time = chord_frames[0].time
                end_time = chord_frames[-1].time

                # Subtract gap from end time (except for the last chord)
                if i < len(chord_boundaries) - 2:
                    end_time = max(start_time, end_time - transition_gap)

                chord = Chord(
                    partials=rep_frame.partials[:],
                    start_time=start_time,
                    end_time=end_time,
                    stability_score=1.0
                )
                chord_seq.chords.append(chord)

        return chord_seq

    # ------------------------------------------------------------------
    #  "Juiciest chords" selection — score and pick the N most
    #  musically interesting chords for kaleidoscope processing
    # ------------------------------------------------------------------

    @staticmethod
    def select_juiciest(chord_seq: ChordSequence, n: int = 5) -> ChordSequence:
        """Select the *n* most musically interesting chords.

        Scores every chord on intervallic richness, registral spread,
        harmonic tension (roughness), voicing balance, and partial count.
        Then uses greedy diverse selection that rewards shared pitches
        between adjacent picks while enforcing centroid diversity.

        Returns a new ChordSequence (time-ordered) with at most *n* chords.
        """
        if len(chord_seq.chords) <= n:
            return chord_seq

        # --- individual scores ---
        scored = []
        for chord in chord_seq.chords:
            freqs = sorted([f for f, a, p in chord.partials if f > 0])
            if len(freqs) < 2:
                scored.append((chord, freqs, 0.0))
                continue
            s = (0.25 * ChordGrouper._interval_richness(freqs)
                 + 0.15 * ChordGrouper._registral_spread(freqs)
                 + 0.25 * ChordGrouper._harmonic_tension(freqs)
                 + 0.15 * ChordGrouper._voicing_balance(freqs)
                 + 0.10 * ChordGrouper._partial_count_score(len(freqs)))
            scored.append((chord, freqs, s))

        # --- greedy diverse selection ---
        selected = ChordGrouper._greedy_diverse_select(scored, n)

        # preserve original time ordering
        selected.sort(key=lambda c: c.start_time)

        result = ChordSequence()
        result.sample_rate = chord_seq.sample_rate
        result.chords = selected
        return result

    # -- quarter-tone helpers ------------------------------------------

    @staticmethod
    def _freq_to_qt(freq: float, ref: float = 440.0) -> float:
        """Convert Hz to quarter-tone steps relative to *ref*."""
        if freq <= 0:
            return 0.0
        return 24.0 * np.log2(freq / ref)

    # -- individual scoring criteria -----------------------------------

    @staticmethod
    def _interval_richness(freqs: List[float]) -> float:
        """Diversity of pairwise interval classes in 24-TET."""
        if len(freqs) < 2:
            return 0.0
        qts = [ChordGrouper._freq_to_qt(f) for f in freqs]
        ics = set()
        for i in range(len(qts)):
            for j in range(i + 1, len(qts)):
                interval = abs(qts[j] - qts[i])
                ic = round(interval) % 24          # quarter-tone IC
                ic = min(ic, 24 - ic)               # fold to ≤12
                ics.add(ic)
        return min(1.0, len(ics) / 12.0)

    @staticmethod
    def _registral_spread(freqs: List[float]) -> float:
        """Bell-curve score for registral range (sweet spot ~2 octaves)."""
        if len(freqs) < 2:
            return 0.0
        qts = [ChordGrouper._freq_to_qt(f) for f in freqs]
        span = max(qts) - min(qts)
        # bell curve centered on 48 QT (2 octaves), σ = 18 QT
        return float(np.exp(-0.5 * ((span - 48.0) / 18.0) ** 2))

    @staticmethod
    def _harmonic_tension(freqs: List[float]) -> float:
        """Roughness approximation: fraction of pairs within critical bandwidth."""
        if len(freqs) < 2:
            return 0.0

        def _cb(freq_hz: float) -> float:
            if freq_hz < 500:
                return 50.0
            elif freq_hz < 1500:
                return 80.0
            return freq_hz * 0.1

        within = 0
        total = 0
        for i in range(len(freqs)):
            for j in range(i + 1, len(freqs)):
                total += 1
                center = (freqs[i] + freqs[j]) / 2.0
                if abs(freqs[j] - freqs[i]) < _cb(center):
                    within += 1
        return within / total if total else 0.0

    @staticmethod
    def _voicing_balance(freqs: List[float]) -> float:
        """Evenness of partial distribution across low/mid/high registers."""
        if len(freqs) < 3:
            return 0.5
        lo, hi = min(freqs), max(freqs)
        if hi <= lo:
            return 0.0
        thirds = [(lo + (hi - lo) * k / 3, lo + (hi - lo) * (k + 1) / 3)
                  for k in range(3)]
        counts = [0, 0, 0]
        for f in freqs:
            for k, (a, b) in enumerate(thirds):
                if a <= f <= b:
                    counts[k] += 1
                    break
        ideal = len(freqs) / 3.0
        if ideal == 0:
            return 0.0
        std = (sum((c - ideal) ** 2 for c in counts) / 3.0) ** 0.5
        return max(0.0, 1.0 - std / ideal)

    @staticmethod
    def _partial_count_score(n: int) -> float:
        """Bell-curve score for partial count (sweet spot 4-8)."""
        return float(np.exp(-0.5 * ((n - 6.0) / 2.0) ** 2))

    # -- greedy diverse selection --------------------------------------

    @staticmethod
    def _shared_pitch_count(freqs_a: List[float],
                            freqs_b: List[float],
                            tolerance_qt: float = 1.0) -> int:
        """Count pitches shared within ±tolerance quarter-tones."""
        qts_a = sorted(ChordGrouper._freq_to_qt(f) for f in freqs_a)
        qts_b = sorted(ChordGrouper._freq_to_qt(f) for f in freqs_b)
        shared = 0
        j = 0
        for qa in qts_a:
            while j < len(qts_b) and qts_b[j] < qa - tolerance_qt:
                j += 1
            if j < len(qts_b) and abs(qts_b[j] - qa) <= tolerance_qt:
                shared += 1
        return shared

    @staticmethod
    def _chord_centroid(freqs: List[float]) -> float:
        """Amplitude-unweighted centroid (mean frequency)."""
        return sum(freqs) / len(freqs) if freqs else 0.0

    @staticmethod
    def _greedy_diverse_select(scored: List[Tuple], n: int) -> List:
        """Pick *n* chords maximising individual score + shared-pitch
        connectivity while enforcing centroid diversity.

        *scored* is a list of (Chord, freqs_list, score) tuples.
        """
        if not scored:
            return []

        # start with the highest-scored chord
        pool = list(scored)
        pool.sort(key=lambda x: x[2], reverse=True)
        chosen = [pool.pop(0)]

        while len(chosen) < n and pool:
            best_idx = None
            best_val = -1e9
            for idx, (chord, freqs, score) in enumerate(pool):
                if not freqs:
                    continue
                centroid = ChordGrouper._chord_centroid(freqs)

                # centroid diversity: penalise if too close to any selected
                min_cent_dist = min(
                    abs(centroid - ChordGrouper._chord_centroid(cf))
                    for _, cf, _ in chosen
                )
                # 6 quarter-tones ≈ 3 semitones minimum separation
                if min_cent_dist < 6.0 * (440.0 / 24.0):
                    # Convert QT distance to Hz roughly — actually
                    # let's just compare centroids in Hz directly.
                    pass
                # Use QT centroid distance instead for consistency
                centroid_qt = ChordGrouper._freq_to_qt(centroid)
                min_qt_dist = min(
                    abs(centroid_qt - ChordGrouper._freq_to_qt(
                        ChordGrouper._chord_centroid(cf)))
                    for _, cf, _ in chosen
                )
                diversity_penalty = max(0.0, 0.15 * (1.0 - min_qt_dist / 6.0)) \
                    if min_qt_dist < 6.0 else 0.0

                # shared pitch bonus (with the most-recently selected chord)
                _, last_freqs, _ = chosen[-1]
                shared = ChordGrouper._shared_pitch_count(freqs, last_freqs)
                shared_bonus = 0.10 * min(1.0, shared / 2.0)

                val = score + shared_bonus - diversity_penalty
                if val > best_val:
                    best_val = val
                    best_idx = idx

            if best_idx is not None:
                chosen.append(pool.pop(best_idx))
            else:
                break

        return [chord for chord, _, _ in chosen]

    @staticmethod
    def _find_stable_partials(frames: List[SDIFFrame], freq_tolerance: float,
                             amp_weight: float) -> List[Tuple[float, float, float]]:
        """Find partials that are stable across frames"""
        if not frames:
            return []

        # Track partials across frames
        # Use first frame as reference, track which partials persist
        all_partials = []
        for frame in frames:
            all_partials.extend(frame.partials)

        if not all_partials:
            return []

        # Cluster partials by frequency proximity
        clusters = ChordGrouper._cluster_by_frequency(all_partials, freq_tolerance)

        # Average each cluster to get stable partial
        stable_partials = []
        for cluster in clusters:
            if len(cluster) >= len(frames) * 0.5:  # Present in at least 50% of frames
                avg_freq = np.mean([p[0] for p in cluster])
                avg_amp = np.mean([p[1] for p in cluster])
                avg_phase = np.mean([p[2] for p in cluster])
                stable_partials.append((avg_freq, avg_amp, avg_phase))

        return stable_partials

    @staticmethod
    def _cluster_by_frequency(partials: List[Tuple[float, float, float]],
                             tolerance: float) -> List[List[Tuple[float, float, float]]]:
        """Cluster partials by frequency proximity"""
        if not partials:
            return []

        sorted_partials = sorted(partials, key=lambda x: x[0])
        clusters = []
        current_cluster = [sorted_partials[0]]

        for partial in sorted_partials[1:]:
            freq = partial[0]
            cluster_freq = current_cluster[0][0]

            # Check if within tolerance
            if abs(freq - cluster_freq) / cluster_freq <= tolerance:
                current_cluster.append(partial)
            else:
                clusters.append(current_cluster)
                current_cluster = [partial]

        if current_cluster:
            clusters.append(current_cluster)

        return clusters

    @staticmethod
    def _calculate_stability(frames: List[SDIFFrame],
                           stable_partials: List[Tuple[float, float, float]],
                           tolerance: float) -> float:
        """Calculate stability score (0-1) for the chord"""
        if not frames or not stable_partials:
            return 0.0

        # Count how many frames contain each stable partial
        matches = 0
        total = len(stable_partials) * len(frames)

        for stable_freq, _, _ in stable_partials:
            for frame in frames:
                for freq, _, _ in frame.partials:
                    if abs(freq - stable_freq) / stable_freq <= tolerance:
                        matches += 1
                        break

        return matches / total if total > 0 else 0.0

    @staticmethod
    def chords_to_sdif(chord_seq: ChordSequence, hold_duration: float = 0.5,
                       frames_per_chord: int = 3, gap_duration: float = 0.02,
                       freq_continuity_threshold: float = 0.02) -> SDIFData:
        """
        Convert chord sequence to SDIF format with sustained chords and release/attack envelopes

        Prevents glissando by using amplitude envelopes to explicitly end and start partials.

        Args:
            chord_seq: Chord sequence to convert
            hold_duration: How long each chord should sustain (seconds)
            frames_per_chord: Number of frames to create per chord for smooth playback
            gap_duration: Time for release/attack transition (seconds)
            freq_continuity_threshold: Frequency ratio threshold for continuing partials (0.02 = 2%)
        """
        data = SDIFData()
        data.sample_rate = chord_seq.sample_rate

        current_time = 0.0

        for chord_idx, chord in enumerate(chord_seq.chords):
            # Sort partials by frequency for consistent ordering
            sorted_partials = sorted(chord.partials, key=lambda p: p[0])

            # Add attack frame (fade-in) at the start of the chord
            if chord_idx > 0:  # Not the first chord
                attack_partials = [(f, a * 0.01, p) for f, a, p in sorted_partials]
                attack_frame = SDIFFrame(time=current_time, partials=attack_partials)
                data.frames.append(attack_frame)
                current_time += gap_duration / 2

            # Create multiple frames for this chord to sustain it (full amplitude)
            for i in range(frames_per_chord):
                time = current_time + (i * hold_duration / frames_per_chord)
                # Use the same partials for all frames in this chord
                frame = SDIFFrame(time=time, partials=sorted_partials)
                data.frames.append(frame)

            # Move to next chord time
            current_time += hold_duration

            # Add release frame (fade-out) at the end of the chord
            if chord_idx < len(chord_seq.chords) - 1:  # Not the last chord
                release_partials = [(f, a * 0.001, p) for f, a, p in sorted_partials]
                release_frame = SDIFFrame(time=current_time, partials=release_partials)
                data.frames.append(release_frame)
                current_time += gap_duration / 2

        return data

    @staticmethod
    def _check_frequency_change(partials1: List[Tuple[float, float, float]],
                               partials2: List[Tuple[float, float, float]],
                               threshold: float) -> bool:
        """
        Check if frequencies change significantly between two sets of partials

        Returns True if partials have different frequencies (need to split)
        """
        if len(partials1) != len(partials2):
            return True

        # Sort both by frequency
        sorted1 = sorted(partials1, key=lambda p: p[0])
        sorted2 = sorted(partials2, key=lambda p: p[0])

        # Check if any frequency changes significantly
        for (f1, _, _), (f2, _, _) in zip(sorted1, sorted2):
            if f1 > 0 and f2 > 0:
                ratio = abs(f2 - f1) / f1
                if ratio > threshold:
                    return True

        return False


class SpectralTransformer:
    """Applies spectral transformations to SDIF data"""

    @staticmethod
    def pitch_shift(data: SDIFData, semitones: float) -> SDIFData:
        """Shift all frequencies by a given number of semitones"""
        ratio = 2.0 ** (semitones / 12.0)

        shifted_data = SDIFData()
        shifted_data.sample_rate = data.sample_rate

        for frame in data.frames:
            shifted_partials = [
                (freq * ratio, amp, phase)
                for freq, amp, phase in frame.partials
            ]
            shifted_data.frames.append(
                SDIFFrame(time=frame.time, partials=shifted_partials)
            )

        return shifted_data

    @staticmethod
    def spectral_morph(data1: SDIFData, data2: SDIFData, morph_factor: float) -> SDIFData:
        """
        Morph between two spectral representations
        morph_factor: 0.0 = data1, 1.0 = data2
        """
        morphed_data = SDIFData()
        morphed_data.sample_rate = data1.sample_rate

        # Align frames by time
        max_frames = min(len(data1.frames), len(data2.frames))

        for i in range(max_frames):
            frame1 = data1.frames[i]
            frame2 = data2.frames[i]

            # Interpolate time
            time = frame1.time * (1 - morph_factor) + frame2.time * morph_factor

            # Match partials (simple approach: by frequency proximity)
            matched_partials = SpectralTransformer._match_partials(
                frame1.partials, frame2.partials
            )

            # Interpolate partial parameters
            morphed_partials = []
            for (f1, a1, p1), (f2, a2, p2) in matched_partials:
                freq = f1 * (1 - morph_factor) + f2 * morph_factor
                amp = a1 * (1 - morph_factor) + a2 * morph_factor
                phase = p1 * (1 - morph_factor) + p2 * morph_factor
                morphed_partials.append((freq, amp, phase))

            morphed_data.frames.append(
                SDIFFrame(time=time, partials=morphed_partials)
            )

        return morphed_data

    @staticmethod
    def _match_partials(partials1, partials2):
        """Match partials from two frames by frequency proximity"""
        matched = []
        max_len = max(len(partials1), len(partials2))

        # Sort by frequency
        p1_sorted = sorted(partials1, key=lambda x: x[0])
        p2_sorted = sorted(partials2, key=lambda x: x[0])

        # Pad shorter list with silent partials
        while len(p1_sorted) < max_len:
            p1_sorted.append((0.0, 0.0, 0.0))
        while len(p2_sorted) < max_len:
            p2_sorted.append((0.0, 0.0, 0.0))

        return list(zip(p1_sorted, p2_sorted))

    @staticmethod
    def amplitude_scale(data: SDIFData, scale_factor: float) -> SDIFData:
        """Scale amplitudes by a constant factor"""
        scaled_data = SDIFData()
        scaled_data.sample_rate = data.sample_rate

        for frame in data.frames:
            scaled_partials = [
                (freq, amp * scale_factor, phase)
                for freq, amp, phase in frame.partials
            ]
            scaled_data.frames.append(
                SDIFFrame(time=frame.time, partials=scaled_partials)
            )

        return scaled_data

    @staticmethod
    def kaleidoscope_reflect(data: SDIFData, center_mode: str = 'mean',
                            center_freq: Optional[float] = None,
                            num_reflections: int = 1,
                            keep_original: bool = True,
                            invert_amplitudes: bool = False) -> SDIFData:
        """
        Reflect partials around a center frequency like a kaleidoscope

        Args:
            data: Input SDIF data
            center_mode: How to determine center frequency
                'mean' - use mean of all frequencies
                'lowest' - use lowest frequency
                'highest' - use highest frequency
                'median' - use median frequency
                'custom' - use center_freq parameter
            center_freq: Custom center frequency (used when center_mode='custom')
            num_reflections: Number of reflections to create (1 = simple mirror, 2+ = kaleidoscope)
            keep_original: If True, keep original partials along with reflections
            invert_amplitudes: If True, reflected partials have inverted amplitude envelope

        Returns:
            Reflected SDIF data with kaleidoscope symmetry
        """
        reflected_data = SDIFData()
        reflected_data.sample_rate = data.sample_rate

        for frame in data.frames:
            if not frame.partials:
                reflected_data.frames.append(
                    SDIFFrame(time=frame.time, partials=[])
                )
                continue

            # Extract frequencies to determine center
            frequencies = [freq for freq, _, _ in frame.partials]

            # Calculate center frequency based on mode
            if center_mode == 'mean':
                center = np.mean(frequencies)
            elif center_mode == 'lowest':
                center = min(frequencies)
            elif center_mode == 'highest':
                center = max(frequencies)
            elif center_mode == 'median':
                center = np.median(frequencies)
            elif center_mode == 'custom' and center_freq is not None:
                center = center_freq
            else:
                center = np.mean(frequencies)  # fallback

            # Start with original partials if requested
            new_partials = list(frame.partials) if keep_original else []

            # Create reflections
            for reflection_num in range(1, num_reflections + 1):
                for freq, amp, phase in frame.partials:
                    # Calculate distance from center
                    distance = freq - center

                    # Simple reflection (mirror)
                    if num_reflections == 1:
                        reflected_freq = center - distance
                    else:
                        # Multiple reflections: create symmetrical pattern
                        # Distribute reflections evenly around the center
                        angle = (reflection_num * 2 * np.pi) / (num_reflections + 1)

                        # In frequency space, we can think of this as
                        # rotating the distance vector
                        # For simplicity: alternate between positive and negative reflections
                        if reflection_num % 2 == 1:
                            reflected_freq = center - distance * (reflection_num / num_reflections)
                        else:
                            reflected_freq = center + distance * (reflection_num / num_reflections)

                    # Ensure frequency stays positive and reasonable
                    reflected_freq = max(20.0, reflected_freq)

                    # Optionally invert amplitude for reflected partials
                    if invert_amplitudes:
                        # Scale amplitude based on reflection number
                        reflected_amp = amp * (1.0 - 0.2 * reflection_num)
                    else:
                        reflected_amp = amp

                    # Add reflected partial
                    new_partials.append((reflected_freq, reflected_amp, phase))

            # Sort by frequency for consistency
            new_partials.sort(key=lambda p: p[0])

            reflected_data.frames.append(
                SDIFFrame(time=frame.time, partials=new_partials)
            )

        return reflected_data

    @staticmethod
    def spectral_stretch(data: SDIFData, stretch_factor: float,
                        reference_mode: str = 'lowest') -> SDIFData:
        """
        Stretch the spacing between partials (chord members) by a given factor

        Args:
            data: Input SDIF data
            stretch_factor: Multiplier for spacing (1.0 = no change, 2.0 = double spacing)
            reference_mode: Reference point for stretching
                'lowest' - stretch from lowest frequency
                'mean' - stretch from mean frequency
                'center' - stretch from center of frequency range

        Returns:
            Stretched SDIF data
        """
        stretched_data = SDIFData()
        stretched_data.sample_rate = data.sample_rate

        for frame in data.frames:
            if not frame.partials:
                stretched_data.frames.append(
                    SDIFFrame(time=frame.time, partials=[])
                )
                continue

            # Extract frequencies
            frequencies = [freq for freq, _, _ in frame.partials]

            # Calculate reference frequency based on mode
            if reference_mode == 'lowest':
                reference_freq = min(frequencies)
            elif reference_mode == 'mean':
                reference_freq = np.mean(frequencies)
            elif reference_mode == 'center':
                reference_freq = (min(frequencies) + max(frequencies)) / 2.0
            else:
                reference_freq = min(frequencies)

            # Stretch each partial relative to reference
            stretched_partials = []
            for freq, amp, phase in frame.partials:
                # Calculate interval from reference
                interval = freq - reference_freq

                # Stretch the interval
                stretched_interval = interval * stretch_factor

                # Calculate new frequency
                new_freq = reference_freq + stretched_interval

                # Ensure frequency stays positive
                new_freq = max(20.0, new_freq)

                stretched_partials.append((new_freq, amp, phase))

            stretched_data.frames.append(
                SDIFFrame(time=frame.time, partials=stretched_partials)
            )

        return stretched_data

    # Chord sequence transformations
    @staticmethod
    def transform_chords(chord_seq: ChordSequence, transformation_func, *args, **kwargs) -> ChordSequence:
        """Apply a transformation to a chord sequence"""
        # Convert to SDIF, transform, convert back
        sdif_data = ChordGrouper.chords_to_sdif(chord_seq)
        transformed_sdif = transformation_func(sdif_data, *args, **kwargs)

        # For now, preserve chord structure with transformed partials
        transformed_seq = ChordSequence()
        transformed_seq.sample_rate = chord_seq.sample_rate

        for i, chord in enumerate(chord_seq.chords):
            if i < len(transformed_sdif.frames):
                transformed_chord = Chord(
                    partials=transformed_sdif.frames[i].partials,
                    start_time=chord.start_time,
                    end_time=chord.end_time,
                    stability_score=chord.stability_score
                )
                transformed_seq.chords.append(transformed_chord)

        return transformed_seq


class MusicXMLExporter:
    """Exports SDIF spectral data to MusicXML chord-based representation"""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)

    def _add_note_to_measure(self, measure, freq: float, amp: float,
                              eighth_tone: bool, is_chord_tone: bool = False,
                              show_dynamic: bool = False, staff: int = 1):
        """Add a single note element to a measure with proper accidentals.

        Args:
            measure: XML measure element
            freq: frequency in Hz
            amp: amplitude
            eighth_tone: quantization mode
            is_chord_tone: if True, add <chord/> tag
            show_dynamic: if True, add dynamics marking
            staff: staff number (1=treble, 2=bass)
        """
        note_elem = SubElement(measure, 'note')
        if is_chord_tone:
            SubElement(note_elem, 'chord')

        pitch_data = self._freq_to_pitch(freq, eighth_tone)
        pitch = SubElement(note_elem, 'pitch')
        SubElement(pitch, 'step').text = pitch_data['step']
        alter_val = pitch_data['alter']
        if alter_val != 0:
            if alter_val == int(alter_val):
                SubElement(pitch, 'alter').text = str(int(alter_val))
            else:
                SubElement(pitch, 'alter').text = str(round(alter_val, 3))
        SubElement(pitch, 'octave').text = str(pitch_data['octave'])

        SubElement(note_elem, 'duration').text = '1'
        SubElement(note_elem, 'type').text = 'quarter'

        # Omit <accidental> — Dorico infers from <alter> after respell with 48-EDO

        # Assign to staff
        SubElement(note_elem, 'staff').text = str(staff)

        if show_dynamic:
            notations = SubElement(note_elem, 'notations')
            dynamics = SubElement(notations, 'dynamics')
            SubElement(dynamics, self._amplitude_to_dynamic(amp))

    def _dedup_partials(self, partials_list, eighth_tone: bool):
        """Merge partials that would produce the same notated pitch.

        Quantizes each partial to its notated pitch (eighth-tone or semitone)
        and keeps only the loudest at each quantized pitch. This prevents
        doubled notes in the MusicXML output.

        Returns deduplicated list of (freq, amp) tuples.
        """
        if not partials_list:
            return partials_list

        # Quantize to the same resolution used for notation
        # eighth_tone: 8 divisions per semitone (48-EDO); semitone: 1 division
        divisor = 8 if eighth_tone else 1
        pitch_map = {}  # quantized_key -> (freq, amp)
        for freq, amp in partials_list:
            if freq <= 0:
                continue
            midi_exact = 69 + 12 * np.log2(freq / 440.0)
            quantized = round(midi_exact * divisor)
            if quantized in pitch_map:
                # Keep the louder one (use its original freq)
                if amp > pitch_map[quantized][1]:
                    pitch_map[quantized] = (freq, amp)
            else:
                pitch_map[quantized] = (freq, amp)
        return list(pitch_map.values())

    def _thin_clusters(self, partials_list, min_interval_cents: float = 150.0):
        """Remove partials that are too close together in pitch.

        Walks through partials sorted by frequency and when two are within
        min_interval_cents of each other, keeps only the louder one.
        This prevents muddy clusters of near-identical pitches.

        Args:
            partials_list: list of (freq, amp) tuples
            min_interval_cents: minimum spacing in cents (default 150 = 1.5 semitones)

        Returns:
            Thinned list of (freq, amp) tuples.
        """
        if len(partials_list) <= 1 or min_interval_cents <= 0:
            return partials_list

        # Sort by frequency
        sorted_p = sorted(partials_list, key=lambda x: x[0])
        kept = [sorted_p[0]]

        for freq, amp in sorted_p[1:]:
            prev_freq = kept[-1][0]
            if prev_freq > 0 and freq > 0:
                cents_apart = 1200.0 * np.log2(freq / prev_freq)
            else:
                cents_apart = float('inf')

            if cents_apart >= min_interval_cents:
                kept.append((freq, amp))
            else:
                # Too close — keep the louder one
                if amp > kept[-1][1]:
                    kept[-1] = (freq, amp)
                # else: discard current, keep previous

        return kept

    def _export_chord_list(self, part, chord_list, min_amplitude: float,
                            max_partials_per_chord: int, eighth_tone: bool,
                            relative_threshold: float = 0.1,
                            min_interval_cents: float = 0.0,
                            bass_anchor: bool = False,
                            registral_continuity: bool = False):
        """Export a list of (partials, time) tuples as one chord per beat.

        Args:
            relative_threshold: fraction of loudest partial below which notes are dropped
                               (0.1 = keep all above 10% of loudest; 0.3 = above 30%)
            min_interval_cents: minimum spacing between chord tones in cents (0 = off).
                               150 = 1.5 semitones is a good default for thinning clusters.
            bass_anchor: if True, reserve a slot for the lowest available partial
                        when it's more than an octave below the current selection's lowest.
            registral_continuity: if True, when the bass jumps up more than an octave
                                 from the previous chord, pull in the closest partial
                                 to the previous bass pitch.
        """
        beats_per_measure = 4
        prev_lowest_freq = None  # for registral continuity tracking
        is_first_chord_done = False
        selected_per_chord = []  # collect final partials for 12-TET derivation

        for chord_idx, (partials_list, chord_time, source_group) in enumerate(chord_list):
            # Pack chords sequentially: 4 per measure
            measure_num = (chord_idx // beats_per_measure) + 1
            measure = self._find_or_create_measure(part, measure_num)

            # Deduplicate partials at same pitch before filtering
            deduped = self._dedup_partials(
                [(freq, amp) for freq, amp in partials_list if amp > 0 and freq > 0],
                eighth_tone
            )
            if not deduped:
                continue

            max_amp = max(amp for _, amp in deduped)
            rel_thresh = max_amp * relative_threshold
            effective_threshold = min(min_amplitude, rel_thresh)

            significant = [
                (freq, amp) for freq, amp in deduped
                if amp >= effective_threshold
            ]

            # Thin clusters: remove partials too close together
            if min_interval_cents > 0:
                significant = self._thin_clusters(significant, min_interval_cents)

            # Save all amplitude-filtered partials before capping for bass anchor lookup
            all_filtered = list(significant)

            significant.sort(key=lambda x: x[1], reverse=True)
            significant = significant[:max_partials_per_chord]

            if not significant:
                continue

            # Bass anchor: ensure lowest available partial is represented
            if bass_anchor and all_filtered:
                lowest_available = min(all_filtered, key=lambda x: x[0])
                lowest_selected = min(significant, key=lambda x: x[0])
                if lowest_available[0] > 0 and lowest_selected[0] > 0:
                    gap_semitones = 12.0 * np.log2(lowest_selected[0] / lowest_available[0])
                    if gap_semitones > 12.0:
                        # Swap out the weakest selected partial for the bass anchor
                        weakest = min(significant, key=lambda x: x[1])
                        significant.remove(weakest)
                        significant.append(lowest_available)

            # Registral continuity: smooth sudden bass dropout
            # Only apply to chords with 3+ notes (enough to spare one for folding)
            did_fold = False
            if registral_continuity and prev_lowest_freq is not None and len(significant) >= 3:
                current_lowest = min(significant, key=lambda x: x[0])
                if current_lowest[0] > 0 and prev_lowest_freq > 0:
                    jump_semitones = 12.0 * np.log2(current_lowest[0] / prev_lowest_freq)
                    if jump_semitones > 12.0:
                        # First try: find an available partial closer to previous bass
                        candidates = [p for p in all_filtered
                                      if p[0] < current_lowest[0] and p not in significant]
                        if candidates:
                            closest = min(candidates,
                                          key=lambda x: abs(12.0 * np.log2(x[0] / prev_lowest_freq))
                                          if x[0] > 0 else float('inf'))
                            weakest = min(significant, key=lambda x: x[1])
                            significant.remove(weakest)
                            significant.append(closest)
                        else:
                            # No lower partials available — octave-fold the highest
                            # selected partial downward toward the previous bass.
                            highest = max(significant, key=lambda x: x[0])
                            folded_freq = highest[0]
                            # Fold down by octaves until within ~6 semitones of prev bass
                            while folded_freq > 0 and 12.0 * np.log2(folded_freq / prev_lowest_freq) > 6.0:
                                folded_freq /= 2.0
                            # Only apply if fold lands in a reasonable range (>= C2 ~65 Hz)
                            if folded_freq >= 65.0 and folded_freq < current_lowest[0]:
                                significant.remove(highest)
                                significant.append((folded_freq, highest[1]))
                                did_fold = True

            # Update previous lowest for next iteration.
            # When we octave-folded, use the *unfolded* chord's lowest to avoid
            # the folded note pulling subsequent chords to extreme registers.
            if significant:
                if did_fold:
                    # Use second-lowest (the original lowest before folding)
                    sorted_sig = sorted(significant, key=lambda x: x[0])
                    prev_lowest_freq = sorted_sig[1][0] if len(sorted_sig) > 1 else sorted_sig[0][0]
                else:
                    prev_lowest_freq = min(significant, key=lambda x: x[0])[0]

            # Save selected partials for 12-TET derivation
            selected_per_chord.append(list(significant))

            # Split into treble (>= middle C ~261 Hz) and bass staves
            # Sort by frequency for clean voice layout
            significant.sort(key=lambda x: x[0])
            middle_c_hz = 261.63
            treble = [(f, a) for f, a in significant if f >= middle_c_hz]
            bass = [(f, a) for f, a in significant if f < middle_c_hz]

            # Add mf dynamic as a direction on the very first chord only
            if not is_first_chord_done:
                direction = SubElement(measure, 'direction', placement='below')
                dir_type = SubElement(direction, 'direction-type')
                dynamics_dir = SubElement(dir_type, 'dynamics')
                SubElement(dynamics_dir, 'mf')
                is_first_chord_done = True

            # Write treble staff notes (staff 1)
            if treble:
                first_freq, first_amp = treble[0]
                self._add_note_to_measure(measure, first_freq, first_amp,
                                           eighth_tone, is_chord_tone=False,
                                           show_dynamic=False, staff=1)
                for freq, amp in treble[1:]:
                    self._add_note_to_measure(measure, freq, amp,
                                               eighth_tone, is_chord_tone=True, staff=1)

                # If bass notes exist, backup to write them at the same beat
                if bass:
                    backup = SubElement(measure, 'backup')
                    SubElement(backup, 'duration').text = '1'

            # Write bass staff notes (staff 2)
            if bass:
                first_freq, first_amp = bass[0]
                self._add_note_to_measure(measure, first_freq, first_amp,
                                           eighth_tone, is_chord_tone=False,
                                           show_dynamic=False, staff=2)
                for freq, amp in bass[1:]:
                    self._add_note_to_measure(measure, freq, amp,
                                               eighth_tone, is_chord_tone=True, staff=2)

        return selected_per_chord

    def _export_12tet_from_selected(self, part, selected_per_chord):
        """Export a 12-TET reduction derived from the 48-EDO staff's selected partials.

        Takes the exact partials chosen for P1, rounds each to the nearest
        semitone, deduplicates collisions (keeping loudest), and writes notes.
        This preserves the voicing shape of the microtonal staff.
        """
        beats_per_measure = 4
        is_first_chord_done = False

        for chord_idx, partials in enumerate(selected_per_chord):
            measure_num = (chord_idx // beats_per_measure) + 1
            measure = self._find_or_create_measure(part, measure_num)

            if not partials:
                continue

            # Round to semitone and deduplicate collisions
            deduped = self._dedup_partials(partials, eighth_tone=False)
            if not deduped:
                continue

            # Sort by frequency for voice layout
            deduped.sort(key=lambda x: x[0])
            middle_c_hz = 261.63
            treble = [(f, a) for f, a in deduped if f >= middle_c_hz]
            bass = [(f, a) for f, a in deduped if f < middle_c_hz]

            # Add mf dynamic on first chord only
            if not is_first_chord_done:
                direction = SubElement(measure, 'direction', placement='below')
                dir_type = SubElement(direction, 'direction-type')
                dynamics_dir = SubElement(dir_type, 'dynamics')
                SubElement(dynamics_dir, 'mf')
                is_first_chord_done = True

            # Write treble staff notes (staff 1)
            if treble:
                first_freq, first_amp = treble[0]
                self._add_note_to_measure(measure, first_freq, first_amp,
                                           eighth_tone=False, is_chord_tone=False,
                                           show_dynamic=False, staff=1)
                for freq, amp in treble[1:]:
                    self._add_note_to_measure(measure, freq, amp,
                                               eighth_tone=False, is_chord_tone=True, staff=1)

                if bass:
                    backup = SubElement(measure, 'backup')
                    SubElement(backup, 'duration').text = '1'

            # Write bass staff notes (staff 2)
            if bass:
                first_freq, first_amp = bass[0]
                self._add_note_to_measure(measure, first_freq, first_amp,
                                           eighth_tone=False, is_chord_tone=False,
                                           show_dynamic=False, staff=2)
                for freq, amp in bass[1:]:
                    self._add_note_to_measure(measure, freq, amp,
                                               eighth_tone=False, is_chord_tone=True, staff=2)

    def export(self, data, min_amplitude: float = 0.0001,
               max_partials_per_chord: int = 12, eighth_tone: bool = True,
               semitone_reduction: bool = False,
               min_interval_cents: float = 0.0,
               bass_anchor: bool = False,
               registral_continuity: bool = False):
        """
        Export SDIF data or ChordSequence to MusicXML

        Args:
            data: SDIFData or ChordSequence
            min_amplitude: minimum amplitude threshold for including partials
            max_partials_per_chord: maximum number of notes per chord
            eighth_tone: if True, quantize to 8th tones (12.5 cents); if False, semitones
            semitone_reduction: if True, add a second staff with 12-TET reduction
            min_interval_cents: minimum spacing between chord tones in cents (0 = off)
            bass_anchor: if True, reserve a slot for the lowest available partial
            registral_continuity: if True, smooth sudden bass dropouts
        """
        # Build a list of (partials, time, source_group) — one entry per chord
        is_chord_seq = isinstance(data, ChordSequence)
        if is_chord_seq:
            # One entry per chord — no frame repetition
            chord_list = [
                ([(f, a) for f, a, _ in chord.partials], chord.start_time, chord.source_group)
                for chord in data.chords
            ]
        else:
            # SDIF frames: one entry per frame
            chord_list = [
                ([(f, a) for f, a, _ in frame.partials], frame.time, -1)
                for frame in data.frames
            ]

        # Create MusicXML structure
        score = Element('score-partwise', version="4.0")

        # Add part list
        part_list = SubElement(score, 'part-list')
        score_part = SubElement(part_list, 'score-part', id="P1")
        SubElement(score_part, 'part-name').text = 'Spectral Analysis'
        if semitone_reduction:
            score_part_2 = SubElement(part_list, 'score-part', id="P2")
            SubElement(score_part_2, 'part-name').text = '12-TET Reduction'

        # --- Part 1: full microtonal resolution ---
        part = SubElement(score, 'part', id="P1")
        selected_per_chord = self._export_chord_list(
            part, chord_list, min_amplitude,
            max_partials_per_chord, eighth_tone,
            min_interval_cents=min_interval_cents,
            bass_anchor=bass_anchor,
            registral_continuity=registral_continuity)

        # --- Optional Part 2: 12-TET reduction ---
        # Derives from P1's selected partials, rounded to semitones.
        # Preserves the same voicing shape as the microtonal staff.
        if semitone_reduction:
            part2 = SubElement(score, 'part', id="P2")
            self._export_12tet_from_selected(part2, selected_per_chord)

        # Write to file
        tree = ElementTree(score)
        tree.write(self.filepath, encoding='UTF-8', xml_declaration=True)

    def _find_or_create_measure(self, part, measure_num):
        """Find existing measure or create new one"""
        for measure in part.findall('measure'):
            if measure.get('number') == str(measure_num):
                return measure

        measure = SubElement(part, 'measure', number=str(measure_num))

        # Add attributes for first measure — grand staff with treble + bass
        if measure_num == 1:
            attributes = SubElement(measure, 'attributes')
            SubElement(attributes, 'divisions').text = '1'
            key = SubElement(attributes, 'key')
            SubElement(key, 'fifths').text = '0'
            time = SubElement(attributes, 'time')
            SubElement(time, 'beats').text = '4'
            SubElement(time, 'beat-type').text = '4'
            SubElement(attributes, 'staves').text = '2'
            # Staff 1: treble clef
            clef1 = SubElement(attributes, 'clef', number='1')
            SubElement(clef1, 'sign').text = 'G'
            SubElement(clef1, 'line').text = '2'
            # Staff 2: bass clef
            clef2 = SubElement(attributes, 'clef', number='2')
            SubElement(clef2, 'sign').text = 'F'
            SubElement(clef2, 'line').text = '4'

            # Explicitly disable sustain pedal — Dorico applies it by default
            direction = SubElement(measure, 'direction', placement='below')
            dir_type = SubElement(direction, 'direction-type')
            pedal_elem = SubElement(dir_type, 'pedal', type='stop', line='no')
            sound = SubElement(direction, 'sound')
            sound.set('damper-pedal', 'no')

        return measure

    def _freq_to_pitch(self, freq: float, eighth_tone: bool = True) -> Dict:
        """
        Convert frequency to musical pitch notation with microtonal support

        Args:
            freq: Frequency in Hz
            eighth_tone: If True, quantize to 8th tones (12.5 cents)
                        If False, quantize to semitones

        Returns:
            Dictionary with 'step', 'alter', 'octave'
        """
        if freq <= 0:
            freq = 440.0

        # A4 = 440 Hz = MIDI note 69
        # Calculate exact MIDI note (fractional)
        midi_note_exact = 69 + 12 * np.log2(freq / 440.0)

        if eighth_tone:
            # Quantize to eighth-tones (1/8 semitone = 0.125 semitones)
            # This gives 96 divisions per octave; 48-EDO uses every step
            # since 1/48 octave = 1/4 semitone... but Dorico's 48-EDO library
            # counts in 1/48 octave units where each diatonic interval has
            # 8 divisions (e.g. C-D = 8/48), so 1 step = 0.125 semitones.
            midi_note_quantized = round(midi_note_exact * 8) / 8
        else:
            # Standard semitone quantization
            midi_note_quantized = round(midi_note_exact)

        # Clamp to valid MIDI range
        midi_note_quantized = max(0.0, min(127.0, midi_note_quantized))

        # Natural note MIDI offsets within an octave:
        # C=0, D=2, E=4, F=5, G=7, A=9, B=11
        natural_notes = [
            (0, 'C'), (2, 'D'), (4, 'E'), (5, 'F'),
            (7, 'G'), (9, 'A'), (11, 'B')
        ]

        # Find the nearest natural note to minimise alter magnitude
        octave_base = int(midi_note_quantized) // 12
        best_step = 'C'
        best_alter = 999.0
        best_octave = octave_base - 1

        for nat_offset, nat_name in natural_notes:
            # Try this natural in the same octave and adjacent octaves
            for oct_try in (octave_base, octave_base + 1, octave_base - 1):
                nat_midi = oct_try * 12 + nat_offset
                alter = midi_note_quantized - nat_midi
                if abs(alter) < abs(best_alter):
                    best_alter = alter
                    best_step = nat_name
                    best_octave = oct_try - 1  # MusicXML octave = MIDI octave - 1

        return {
            'step': best_step,
            'alter': best_alter,
            'octave': best_octave
        }

    @staticmethod
    def _alter_to_accidental(alter: float):
        """
        Map an alter value to (MusicXML accidental name, SMuFL glyph name).

        Returns a tuple of (accidental_text, smufl_name).  For standard
        accidentals smufl_name is None; for Gould arrow eighth-tone
        accidentals the SMuFL glyph is specified so Dorico renders the
        correct symbol from the 48-EDO tonality system.
        """
        # Round to nearest 0.25 (eighth-tone) for lookup
        rounded = round(alter * 4) / 4
        # (MusicXML accidental text, SMuFL glyph name or None)
        accidental_map = {
            -2.0:   ('flat-flat',            'accidentalDoubleFlat'),
            -1.75:  ('flat-flat',            'accidentalDoubleFlat'),
            -1.5:   ('three-quarters-flat',  'accidentalThreeQuarterTonesFlatZimmermann'),
            -1.25:  ('other',                'accidentalThreeQuarterTonesFlatArrowDown'),
            -1.0:   ('flat',                 'accidentalFlat'),
            -0.75:  ('other',                'accidentalQuarterToneFlatArrowUp'),
            -0.5:   ('quarter-flat',         'accidentalQuarterToneFlatStein'),
            -0.25:  ('other',                'accidentalQuarterToneFlatNaturalArrowDown'),
            0.0:    ('natural',              'accidentalNatural'),
            0.25:   ('other',                'accidentalQuarterToneSharpNaturalArrowUp'),
            0.5:    ('quarter-sharp',        'accidentalQuarterToneSharpStein'),
            0.75:   ('other',                'accidentalQuarterToneSharpArrowDown'),
            1.0:    ('sharp',                'accidentalSharp'),
            1.25:   ('other',                'accidentalThreeQuarterTonesSharpArrowUp'),
            1.5:    ('three-quarters-sharp', 'accidentalThreeQuarterTonesSharpStein'),
            1.75:   ('double-sharp',         'accidentalDoubleSharp'),
            2.0:    ('double-sharp',         'accidentalDoubleSharp'),
        }
        return accidental_map.get(rounded, ('natural', 'accidentalNatural'))

    def _amplitude_to_dynamic(self, amplitude: float) -> str:
        """Convert amplitude to MusicXML dynamic marking"""
        if amplitude < 0.1:
            return 'pp'
        elif amplitude < 0.3:
            return 'p'
        elif amplitude < 0.5:
            return 'mp'
        elif amplitude < 0.7:
            return 'mf'
        elif amplitude < 0.9:
            return 'f'
        else:
            return 'ff'


class MIDIExporter:
    """Exports SDIF spectral data to MIDI with microtonal pitch bends"""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)

    def export(self, data, min_amplitude: float = 0.01):
        """
        Export SDIF data or ChordSequence to MIDI with pitch bends for microtonality

        Args:
            data: SDIFData or ChordSequence
            min_amplitude: minimum amplitude threshold for including partials
        """
        try:
            from midiutil import MIDIFile
        except ImportError:
            print("Error: midiutil library not found. Install with: pip install midiutil")
            return

        # Keep as ChordSequence if possible (preserves chord durations)
        # Otherwise convert SDIFData to ChordSequence by grouping frames
        if isinstance(data, SDIFData):
            # Convert frames to chords - treat each frame as a chord
            chord_seq = ChordSequence()
            for i, frame in enumerate(data.frames):
                if not frame.partials:
                    continue
                # Estimate duration until next frame
                if i < len(data.frames) - 1:
                    duration = data.frames[i + 1].time - frame.time
                else:
                    duration = 0.1  # Default for last frame

                chord = Chord(
                    partials=frame.partials,
                    start_time=frame.time,
                    end_time=frame.time + duration,
                    stability_score=1.0
                )
                chord_seq.chords.append(chord)
            data = chord_seq

        # Debug: Check chord durations
        if hasattr(data, 'chords') and len(data.chords) > 0:
            durations = [c.end_time - c.start_time for c in data.chords[:5]]
            print(f"  First 5 chord durations: {[f'{d:.3f}s' for d in durations]}")

        # Create MIDI file with single channel for MIDI Mono Mode
        midi = MIDIFile(1)  # 1 track
        track = 0
        channel = 0  # Use channel 1 (MIDI channel 0 in code)
        tempo = 60  # Quarter note = 60 BPM
        midi.addTempo(track, 0, tempo)

        # Set pitch bend range to ±48 semitones on channel 1 (±4800 cents)
        # Add these at time -0.5 to ensure they're processed first
        midi.addControllerEvent(track, channel, 0, 101, 0)  # RPN MSB
        midi.addControllerEvent(track, channel, 0, 100, 0)  # RPN LSB
        midi.addControllerEvent(track, channel, 0, 6, 48)   # Data Entry MSB (48 semitones)
        midi.addControllerEvent(track, channel, 0, 38, 0)   # Data Entry LSB

        # Add initial silence (0.2 beats) to ensure first pitch bend has time to process
        start_offset = 0.2

        # Process each chord
        for chord in data.chords:
            # Filter partials by amplitude
            significant_partials = [
                (freq, amp, phase) for freq, amp, phase in chord.partials
                if amp >= min_amplitude
            ]

            if not significant_partials:
                continue

            # Convert times to MIDI beats (at 60 BPM, 1 second = 1 beat)
            start_beats = chord.start_time * (tempo / 60.0)
            end_beats = chord.end_time * (tempo / 60.0)
            duration = end_beats - start_beats

            # Ensure minimum duration for audibility
            if duration < 0.1:
                duration = 0.1

            # Set ALL pitch bends well BEFORE note-ons
            # This prevents the pitch bend artifact from happening after note starts
            # Using 0.1 beats (60ms at 60 BPM) to ensure pitch wheel arrives first
            pitch_bend_time = max(0, start_beats - 0.1)

            pitch_bend_data = []
            for freq, amp, phase in significant_partials[:16]:  # Limit to 16 channels
                midi_note, pitch_bend_cents = self._freq_to_midi_with_bend(freq)
                pitch_bend_value = int((pitch_bend_cents / 4800.0) * 8192) + 8192
                pitch_bend_value = max(0, min(16383, pitch_bend_value))
                velocity = int(min(127, max(1, amp * 127)))
                pitch_bend_data.append((midi_note, pitch_bend_value, velocity))

            # Add all pitch bend events well BEFORE notes (0.1 beats = 100ms at 60 BPM)
            for channel, (midi_note, pitch_bend_value, velocity) in enumerate(pitch_bend_data):
                midi.addPitchWheelEvent(track, channel, pitch_bend_time, pitch_bend_value)

            # Then add all note-ons at the actual start time
            for channel, (midi_note, pitch_bend_value, velocity) in enumerate(pitch_bend_data):
                midi.addNote(track, channel, midi_note, start_beats, duration, velocity)

        # Write MIDI file
        with open(self.filepath, 'wb') as output_file:
            midi.writeFile(output_file)

    def _freq_to_midi_with_bend(self, freq: float) -> Tuple[int, float]:
        """
        Convert frequency to MIDI note number and pitch bend in cents

        Returns:
            Tuple of (midi_note, bend_cents) where:
            - midi_note is the nearest equal-tempered MIDI note
            - bend_cents is the deviation in cents (±4800 max for ±48 semitone range)
        """
        import math

        # Convert frequency to MIDI note (fractional)
        # MIDI note 69 = A4 = 440 Hz
        midi_float = 69 + 12 * math.log2(freq / 440.0)

        # Get nearest semitone
        midi_note = round(midi_float)

        # Calculate deviation in cents (100 cents = 1 semitone)
        bend_cents = (midi_float - midi_note) * 100

        # With ±48 semitone range, we typically don't need to clamp
        # But ensure we stay within the ±4800 cent range
        if bend_cents > 4800:
            # Shift up as needed
            semitones_to_shift = int(bend_cents / 100)
            midi_note += semitones_to_shift
            bend_cents -= semitones_to_shift * 100
        elif bend_cents < -4800:
            # Shift down as needed
            semitones_to_shift = int(-bend_cents / 100)
            midi_note -= semitones_to_shift
            bend_cents += semitones_to_shift * 100

        # Ensure MIDI note is in valid range (0-127)
        midi_note = max(0, min(127, midi_note))

        return midi_note, bend_cents


# Interactive mode helper functions
def _prompt_text(question: str, default: str = "") -> str:
    """Text input with fallback to basic input()."""
    result = input(f"{question} [{default}]: ").strip()
    return result if result else default


def _prompt_float(question: str, default: float) -> float:
    """Numeric float input."""
    raw = _prompt_text(question, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def _prompt_confirm(question: str, default: bool = True) -> bool:
    """Yes/no confirmation."""
    default_str = "Y/n" if default else "y/N"
    result = input(f"{question} [{default_str}]: ").strip().lower()
    if not result:
        return default
    return result == 'y'


def interactive_mode(raw_data: SDIFData, input_path: str):
    """Interactive mode - ask user what to do after importing"""
    juiciest_source_chords = None  # tracks whether juiciest selection was used
    print(f"\n{'='*60}")
    print(f"Spectral Data Imported Successfully!")
    print(f"{'='*60}")
    print(f"Frames: {len(raw_data.frames)}")
    if raw_data.frames:
        avg_partials = sum(len(f.partials) for f in raw_data.frames) / len(raw_data.frames)
        print(f"Average partials per frame: {avg_partials:.1f}")
        print(f"Duration: {raw_data.frames[-1].time:.2f} seconds")
    print(f"{'='*60}\n")

    # Ask if user wants to group into chords
    group_chords = input("Group partials into chords? (y/n) [y]: ").strip().lower() != 'n'

    if group_chords:
        print("\nGrouping mode:")
        print("  1. Simple - fixed time windows (distinct chords per window)")
        print("  2. Stability - analyze stability over time (averaged partials)")
        print("  3. Transition - detect chord boundaries by frequency changes")
        mode_choice = input("Choice [1]: ").strip()

        if mode_choice == '2':
            mode = 'stability'
        elif mode_choice == '3':
            mode = 'transition'
        else:
            mode = 'simple'

        if mode == 'transition':
            # Transition mode: detect chords by frequency changes
            threshold_input = input("Transition threshold percentage (3, 5, 8, etc.) [5]: ").strip()
            threshold_pct = float(threshold_input) if threshold_input else 5.0
            transition_threshold = threshold_pct / 100.0

            gap_input = input("Gap duration between chords (seconds) [0.1]: ").strip()
            transition_gap = float(gap_input) if gap_input else 0.1

            print(f"\nGrouping into chords (mode=transition, threshold={threshold_pct}%, gap={transition_gap}s)...")
            chord_seq = ChordGrouper.group_to_chords(
                raw_data, mode='transition',
                transition_threshold=transition_threshold,
                transition_gap=transition_gap
            )
        else:
            # Time-based modes (simple or stability)
            window_size = input("Chord duration / window size (seconds) [0.1]: ").strip()
            window_size = float(window_size) if window_size else 0.1

            if mode == 'stability':
                freq_tolerance = input("Frequency tolerance (0-1) [0.05]: ").strip()
                freq_tolerance = float(freq_tolerance) if freq_tolerance else 0.05
            else:
                freq_tolerance = 0.05

            print(f"\nGrouping into chords (mode={mode}, window={window_size}s)...")
            chord_seq = ChordGrouper.group_to_chords(
                raw_data, window_size=window_size,
                freq_tolerance=freq_tolerance, mode=mode
            )
        print(f"Created {len(chord_seq.chords)} chord groups\n")
        working_data = chord_seq
        is_chord_mode = True
    else:
        working_data = raw_data
        is_chord_mode = False

    # Ask about transformations
    print("\nAvailable transformations:")
    print("1. Kaleidoscope reflection (mirror partials)")
    print("2. Pitch shift")
    print("3. Spectral stretch (change chord spacing)")
    print("4. Amplitude scale")
    print("5. Spectral morph (with another SDIF file)")

    transforms = input("\nWhich transformations? (comma-separated numbers, or 'none') [none]: ").strip()

    if transforms and transforms.lower() != 'none':
        transform_list = [int(t.strip()) for t in transforms.split(',')]

        for t in transform_list:
            if t == 1:
                if is_chord_mode:
                    num_chords = len(working_data.chords)
                    print(f"\nDetected {num_chords} chord(s) in sequence")

                    # --- Offer juiciest-chord selection for large sets ---
                    if num_chords > 5:
                        print(f"\n{num_chords} chords is a lot for nested_cycles.")
                        print("  1. Use all " + str(num_chords) + " chords")
                        print("  2. Auto-select 5 juiciest chords (recommended for nested_cycles)")
                        print("  3. Choose how many to select")
                        sel_choice = input("Choice [2]: ").strip()
                        if sel_choice == '1':
                            pass  # keep all
                        else:
                            sel_n = 5
                            if sel_choice == '3':
                                sel_n_input = input("How many chords? [5]: ").strip()
                                sel_n = int(sel_n_input) if sel_n_input else 5
                            working_data = ChordGrouper.select_juiciest(working_data, n=sel_n)
                            num_chords = len(working_data.chords)
                            # Store source chords to prepend before kaleidoscope output
                            juiciest_source_chords = [
                                Chord(partials=c.partials[:], start_time=c.start_time,
                                      end_time=c.end_time, stability_score=c.stability_score)
                                for c in working_data.chords
                            ]
                            # Print selection summary
                            print(f"\nSelected {num_chords} juiciest chords:")
                            for si, sc in enumerate(working_data.chords):
                                sfreqs = sorted([f for f, a, p in sc.partials if f > 0])
                                shared_str = ""
                                if si > 0:
                                    prev_f = sorted([f for f, a, p in working_data.chords[si-1].partials if f > 0])
                                    shared = ChordGrouper._shared_pitch_count(sfreqs, prev_f)
                                    if shared > 0:
                                        shared_str = f"  (shares {shared} pitch{'es' if shared > 1 else ''})"
                                print(f"  {si+1}. t={sc.start_time:.2f}s  n={len(sfreqs)}  "
                                      f"freqs={[round(f,0) for f in sfreqs[:6]]}{shared_str}")

                    if num_chords == 1:
                        print("Single chord mode - choose kaleidoscope type:")
                        print("  1. Nested cycles - true kaleidoscope with anchored reflections")
                        print("  2. Temporal evolution - rotating reflections")
                        mode_choice = input("Choice [1]: ").strip()
                        mode = 'nested_cycles' if mode_choice != '2' else 'temporal_evolution'

                        rot_dur = input("Duration for each rotation state (seconds) [0.5]: ").strip()
                        rot_dur = float(rot_dur) if rot_dur else 0.5

                        # Ask about cyclic return
                        cyclic = input("Return to original chord at end? (y/n) [y]: ").strip().lower() != 'n'

                        # Reflection mode: always musical (log-frequency space)
                        refl_mode = 'musical'

                        # Ask about omitting originals
                        omit_orig = input("\nUse ONLY reflections (omit original partials)? (y/n) [n]: ").strip().lower() == 'y'

                        # Ask about shuffling reflection order
                        shuffle = input("\nRandomize reflection order? (y/n) [y]: ").strip().lower() != 'n'

                        # Ask about psychoacoustic spacing
                        psycho_spacing = input("\nApply psychoacoustic spacing (reduce masking)? (y/n) [n]: ").strip().lower() == 'y'

                        # Ask about scalar motion
                        scalar = input("\nScalar kaleidoscope motion (constantly ascending like rotating kaleidoscope)? (y/n) [n]: ").strip().lower() == 'y'

                        working_data = ChordGrouper.kaleidoscope_chords(
                            working_data, mode=mode,
                            rotation_duration=rot_dur,
                            cyclic_return=cyclic,
                            reflection_mode=refl_mode,
                            omit_originals=omit_orig,
                            shuffle_order=shuffle,
                            psychoacoustic_spacing=psycho_spacing,
                            scalar_motion=scalar
                        )
                    elif num_chords >= 2:
                        print(f"{num_chords} chord mode - choose option:")
                        print("  1. Nested cycles - true kaleidoscope with anchored reflections")
                        print("  2. Sequential evolution - complete rotation through each chord's partials")
                        print("  3. Interweave - alternate between chords with reflections")
                        print("  4. Morph - smoothly morph between chords with reflections (2 chords only)")
                        choice = input("Choice [1]: ").strip()

                        if choice == '2':
                            mode = 'sequential_evolution'
                        elif choice == '3':
                            mode = 'interweave'
                        elif choice == '4' and num_chords == 2:
                            mode = 'morph'
                        else:
                            mode = 'nested_cycles'

                        rot_dur = input("Duration per state (seconds) [0.5]: ").strip()
                        rot_dur = float(rot_dur) if rot_dur else 0.5

                        # Ask about cyclic return for evolution modes
                        cyclic = True
                        refl_mode = 'musical'
                        omit_orig = False
                        if mode in ['nested_cycles', 'temporal_evolution', 'sequential_evolution']:
                            cyclic = input("Return to original chord at end of each cycle? (y/n) [y]: ").strip().lower() != 'n'

                            # Reflection mode: always musical (log-frequency space)
                            refl_mode = 'musical'

                            # Ask about omitting originals
                            omit_orig = input("\nUse ONLY reflections (omit original partials)? (y/n) [n]: ").strip().lower() == 'y'

                        # Ask about shuffling reflection order
                        shuffle = input("\nRandomize reflection order? (y/n) [y]: ").strip().lower() != 'n'

                        # Ask about transition style for morph mode
                        smooth_trans = True
                        if mode == 'morph':
                            print("\nTransition style:")
                            print("  1. Smooth (glissando) - continuous frequency transitions")
                            print("  2. Sharp cuts - gaps between morph states")
                            trans_choice = input("Choice [1]: ").strip()
                            smooth_trans = trans_choice != '2'

                        # Ask about psychoacoustic spacing
                        psycho_spacing = input("\nApply psychoacoustic spacing (reduce masking)? (y/n) [n]: ").strip().lower() == 'y'

                        # Ask about scalar motion
                        scalar = input("\nScalar kaleidoscope motion (constantly ascending like rotating kaleidoscope)? (y/n) [n]: ").strip().lower() == 'y'

                        working_data = ChordGrouper.kaleidoscope_chords(
                            working_data, mode=mode,
                            rotation_duration=rot_dur,
                            smooth_transitions=smooth_trans,
                            cyclic_return=cyclic,
                            reflection_mode=refl_mode,
                            omit_originals=omit_orig,
                            shuffle_order=shuffle,
                            psychoacoustic_spacing=psycho_spacing,
                            scalar_motion=scalar
                        )
                else:
                    # Frame-based kaleidoscope (original implementation)
                    center = input("Center frequency mode (mean/lowest/highest/median/custom) [mean]: ").strip()
                    center = center if center in ['mean', 'lowest', 'highest', 'median', 'custom'] else 'mean'

                    center_freq = None
                    if center == 'custom':
                        custom = input("Custom center frequency (Hz): ").strip()
                        center_freq = float(custom) if custom else 440.0

                    num_ref = input("Number of reflections (1=simple mirror, 2+=kaleidoscope) [1]: ").strip()
                    num_ref = int(num_ref) if num_ref else 1

                    keep_orig = input("Keep original partials? (y/n) [y]: ").strip().lower() != 'n'

                    invert_amps = input("Invert amplitudes for reflections? (y/n) [n]: ").strip().lower() == 'y'

                    print(f"Applying kaleidoscope reflection (center: {center}, reflections: {num_ref})")
                    working_data = SpectralTransformer.kaleidoscope_reflect(
                        working_data, center, center_freq, num_ref, keep_orig, invert_amps
                    )

                # --- Prepend original source chords before kaleidoscope output ---
                if juiciest_source_chords and is_chord_mode and hasattr(working_data, 'chords'):
                    print(f"\nPrepending {len(juiciest_source_chords)} source chords before kaleidoscope output")
                    # Re-time source chords to sit before the kaleidoscope output
                    earliest_k = working_data.chords[0].start_time if working_data.chords else 0.0
                    src_duration = 0.5  # each source chord gets 0.5s
                    retimed_src = []
                    for si, sc in enumerate(juiciest_source_chords):
                        t_start = si * src_duration
                        retimed_src.append(Chord(
                            partials=sc.partials[:],
                            start_time=t_start,
                            end_time=t_start + src_duration,
                            stability_score=sc.stability_score
                        ))
                    # Shift all kaleidoscope chords forward in time
                    offset = len(juiciest_source_chords) * src_duration
                    for kc in working_data.chords:
                        kc.start_time += offset
                        kc.end_time += offset
                    working_data.chords = retimed_src + working_data.chords

            elif t == 2:
                shift = input("Pitch shift (semitones, + or -) [0]: ").strip()
                shift = float(shift) if shift else 0.0
                if shift != 0.0:
                    print(f"Applying pitch shift: {shift} semitones")
                    if is_chord_mode:
                        working_data = SpectralTransformer.transform_chords(
                            working_data, SpectralTransformer.pitch_shift, shift
                        )
                    else:
                        working_data = SpectralTransformer.pitch_shift(working_data, shift)

            elif t == 3:
                stretch = input("Spectral stretch factor (1.0 = no change) [1.0]: ").strip()
                stretch = float(stretch) if stretch else 1.0
                if stretch != 1.0:
                    ref = input("Reference point (lowest/mean/center) [lowest]: ").strip()
                    ref = ref if ref in ['lowest', 'mean', 'center'] else 'lowest'
                    print(f"Applying spectral stretch: {stretch}x (reference: {ref})")
                    if is_chord_mode:
                        working_data = SpectralTransformer.transform_chords(
                            working_data, SpectralTransformer.spectral_stretch, stretch, ref
                        )
                    else:
                        working_data = SpectralTransformer.spectral_stretch(working_data, stretch, ref)

            elif t == 4:
                scale = input("Amplitude scale factor [1.0]: ").strip()
                scale = float(scale) if scale else 1.0
                if scale != 1.0:
                    print(f"Scaling amplitude by {scale}")
                    if is_chord_mode:
                        working_data = SpectralTransformer.transform_chords(
                            working_data, SpectralTransformer.amplitude_scale, scale
                        )
                    else:
                        working_data = SpectralTransformer.amplitude_scale(working_data, scale)

            elif t == 5:
                morph_file = input("Path to SDIF file to morph with: ").strip()
                if morph_file:
                    morph_factor = input("Morph factor (0.0=original, 1.0=target) [0.5]: ").strip()
                    morph_factor = float(morph_factor) if morph_factor else 0.5
                    print(f"Morphing with {morph_file} (factor: {morph_factor})")
                    morph_reader = SDIFReader(morph_file)
                    morph_data = morph_reader.read()
                    if is_chord_mode:
                        morph_chords = ChordGrouper.group_to_chords(morph_data, window_size, freq_tolerance)
                        sdif1 = ChordGrouper.chords_to_sdif(working_data)
                        sdif2 = ChordGrouper.chords_to_sdif(morph_chords)
                        morphed = SpectralTransformer.spectral_morph(sdif1, sdif2, morph_factor)
                        working_data = ChordGrouper.group_to_chords(morphed, window_size, freq_tolerance)
                    else:
                        working_data = SpectralTransformer.spectral_morph(working_data, morph_data, morph_factor)

    # Convert to SDIF for output
    if is_chord_mode:
        hold_dur = input("\nChord hold duration in seconds [0.5]: ").strip()
        hold_dur = float(hold_dur) if hold_dur else 0.5
        print(f"Creating sustained chords (hold duration: {hold_dur}s)")
        processed_data = ChordGrouper.chords_to_sdif(working_data, hold_duration=hold_dur)
        export_data = working_data
    else:
        processed_data = working_data
        export_data = working_data

    # Ask about outputs
    print("\nOutput options:")
    base_name = Path(input_path).stem

    save_sdif = input("Save SDIF file? (y/n) [y]: ").strip().lower() != 'n'
    if save_sdif:
        output_path = input(f"  SDIF output path [{base_name}_processed.sdif]: ").strip()
        output_path = output_path if output_path else f"{base_name}_processed.sdif"

    # Write SDIF first so user can audition it in SPEAR
    if save_sdif:
        print(f"\nWriting SDIF to {output_path}...")
        writer = SDIFWriter(output_path)
        writer.write(processed_data)
        print(f"SDIF file written: {output_path}")
        print("\nYou can now audition this file in SPEAR before exporting to MusicXML.")

    # Ask about MusicXML export after SDIF is written
    print()
    save_xml = input("Create MusicXML file? (y/n) [n]: ").strip().lower() == 'y'
    if save_xml:
        xml_path = input(f"  MusicXML output path [{base_name}_notation.xml]: ").strip()
        xml_path = xml_path if xml_path else f"{base_name}_notation.xml"

        # Default to eighth-tone; only ask if user might want semitones
        use_eighth = True
        add_semitone_staff = False
        if juiciest_source_chords:
            # Juiciest mode: skip quantization prompt, auto-add 12-TET staff
            add_semitone_staff = True
            print("  Eighth-tone quantization (default) with 12-TET reduction staff")
        else:
            use_eighth = input("  Use 8th tone microtonal quantization? (y/n) [y]: ").strip().lower() != 'n'

        min_amp = input("  Minimum amplitude threshold [0.01]: ").strip()
        min_amp = float(min_amp) if min_amp else 0.01

        # Voicing refinement options
        print("\n  Voicing refinement:")
        thin_input = input("  Thin pitch clusters — minimum spacing in cents (0=off) [150]: ").strip()
        min_interval_cents = float(thin_input) if thin_input else 150.0

        anchor_input = input("  Anchor bass register (keep lowest partial)? (y/n) [y]: ").strip().lower()
        bass_anchor = anchor_input != 'n'

        continuity_input = input("  Smooth registral discontinuities? (y/n) [y]: ").strip().lower()
        registral_continuity = continuity_input != 'n'

        quantization = "8th tones (12.5 cents)" if use_eighth else "semitones"
        extra = " + 12-TET reduction staff" if add_semitone_staff else ""
        voicing_desc = []
        if min_interval_cents > 0:
            voicing_desc.append(f"cluster thinning={min_interval_cents:.0f}¢")
        if bass_anchor:
            voicing_desc.append("bass anchor")
        if registral_continuity:
            voicing_desc.append("registral continuity")
        voicing_str = f", voicing: {', '.join(voicing_desc)}" if voicing_desc else ""
        print(f"\nExporting to MusicXML: {xml_path} (quantization: {quantization}{extra}{voicing_str})...")
        exporter = MusicXMLExporter(xml_path)
        exporter.export(export_data, min_amplitude=min_amp, eighth_tone=use_eighth,
                        semitone_reduction=add_semitone_staff,
                        min_interval_cents=min_interval_cents,
                        bass_anchor=bass_anchor,
                        registral_continuity=registral_continuity)
        print("MusicXML file written")

    print("\nDone!")


def main():
    """Main CLI interface"""
    parser = argparse.ArgumentParser(
        description='SDIF Spectral Processor - Transform and export spectral data'
    )
    parser.add_argument('input', nargs='?', help='Input file (SDIF, Partiels JSON, or MusicXML). If omitted, will prompt for file path.')
    parser.add_argument('--output-sdif', '-o', help='Output SDIF file')
    parser.add_argument('--output-xml', '-x', help='Output MusicXML file')
    parser.add_argument('--output-midi', help='Output MIDI file (if not specified, auto-generates from SDIF filename)')
    parser.add_argument('--pitch-shift', '-p', type=float, default=0.0,
                        help='Pitch shift in semitones')
    parser.add_argument('--morph-with', '-m', help='SDIF file to morph with')
    parser.add_argument('--morph-factor', '-f', type=float, default=0.5,
                        help='Morph factor (0.0 to 1.0)')
    parser.add_argument('--amplitude-scale', '-a', type=float, default=1.0,
                        help='Amplitude scaling factor')
    parser.add_argument('--spectral-stretch', '-s', type=float, default=1.0,
                        help='Spectral stretch factor for chord spacing (1.0 = no change)')
    parser.add_argument('--stretch-reference', type=str, default='lowest',
                        choices=['lowest', 'mean', 'center'],
                        help='Reference point for spectral stretching')
    parser.add_argument('--min-amplitude', type=float, default=0.0001,
                        help='Minimum amplitude for MusicXML export')
    parser.add_argument('--group-chords', '-g', action='store_true',
                        help='Group partials into chords before processing')
    parser.add_argument('--window-size', '-w', type=float, default=0.1,
                        help='Time window (seconds) for chord duration')
    parser.add_argument('--freq-tolerance', type=float, default=0.05,
                        help='Frequency variation tolerance for chord grouping (0-1)')
    parser.add_argument('--chord-mode', type=str, default='simple',
                        choices=['simple', 'stability', 'transition'],
                        help='Chord grouping mode: simple (distinct per window), '
                             'stability (averaged), or transition (detect by frequency changes)')
    parser.add_argument('--transition-threshold', type=float, default=0.05,
                        help='For transition mode: percentage change threshold (0.05 = 5%%, default: 5%%)')
    parser.add_argument('--transition-gap', type=float, default=0.1,
                        help='For transition mode: gap duration between chords in seconds (default: 0.1)')
    parser.add_argument('--semitone-quantize', action='store_true',
                        help='Quantize to semitones instead of 8th tones in MusicXML')
    parser.add_argument('--chord-hold', type=float, default=0.5,
                        help='Duration in seconds to hold each chord (default: 0.5)')
    parser.add_argument('--kaleidoscope', '-k', action='store_true',
                        help='Apply kaleidoscope reflection to spectral data')
    parser.add_argument('--kaleidoscope-mode', type=str, default='auto',
                        choices=['auto', 'nested_cycles', 'temporal_evolution', 'sequential_evolution', 'interweave', 'morph', 'simple'],
                        help='Kaleidoscope mode: auto (choose based on chord count), '
                             'nested_cycles (true kaleidoscope with anchored reflections), '
                             'temporal_evolution (single chord rotations), '
                             'sequential_evolution (complete rotation for each chord in sequence), '
                             'interweave (alternate two chords), '
                             'morph (morph between two chords), '
                             'simple (basic reflection)')
    parser.add_argument('--kaleidoscope-rotation-duration', type=float, default=0.5,
                        help='Duration for each rotation/morph state in seconds (default: 0.5)')
    parser.add_argument('--kaleidoscope-cyclic-return', action='store_true', default=True,
                        help='Add original chord at start and end of evolution cycle (default: True)')
    parser.add_argument('--kaleidoscope-no-cyclic-return', action='store_true',
                        help='Disable cyclic return (no original chord at start/end)')
    parser.add_argument('--kaleidoscope-reflection-mode', type=str, default='musical',
                        choices=['musical', 'linear'],
                        help='Reflection mode: musical (semitone space, perceptually balanced) or '
                             'linear (Hz space) (default: musical)')
    parser.add_argument('--kaleidoscope-omit-originals', action='store_true',
                        help='Use ONLY reflected partials (omit original partials from rotation states)')
    parser.add_argument('--kaleidoscope-shuffle-order', action='store_true',
                        help='Randomize order of reflection centers to prevent predictable patterns')
    parser.add_argument('--kaleidoscope-smooth-transitions', action='store_true', default=True,
                        help='Use smooth glissando transitions in morph mode (default: True)')
    parser.add_argument('--kaleidoscope-sharp-cuts', action='store_true',
                        help='Use sharp cuts/gaps between morph states instead of smooth transitions')
    parser.add_argument('--kaleidoscope-center', type=str, default='mean',
                        choices=['mean', 'lowest', 'highest', 'median', 'custom'],
                        help='Center frequency mode for simple kaleidoscope reflection')
    parser.add_argument('--kaleidoscope-freq', type=float,
                        help='Custom center frequency for kaleidoscope (used with --kaleidoscope-center custom)')
    parser.add_argument('--kaleidoscope-reflections', type=int, default=1,
                        help='Number of reflections for simple kaleidoscope (1=simple mirror, 2+=multi-fold)')
    parser.add_argument('--kaleidoscope-keep-original', action='store_true', default=True,
                        help='Keep original partials along with reflections (simple mode only)')
    parser.add_argument('--kaleidoscope-invert-amps', action='store_true',
                        help='Invert amplitudes for reflected partials (simple mode only)')
    parser.add_argument('--kaleidoscope-upward-rotation', action='store_true',
                        help='Enable upward rotation sub-mode in nested_cycles for shepard tone illusion')
    parser.add_argument('--kaleidoscope-rise-semitones', type=float, default=24.0,
                        help='Semitones to rise over duration for upward rotation (default: 24)')
    parser.add_argument('--kaleidoscope-fade-low', type=float, default=50.0,
                        help='Low frequency fade point in Hz for upward rotation (default: 50)')
    parser.add_argument('--kaleidoscope-fade-high', type=float, default=2500.0,
                        help='High frequency fade point in Hz for upward rotation (default: 2500)')
    parser.add_argument('--psychoacoustic-spacing', action='store_true',
                        help='Apply psychoacoustic spacing based on critical bandwidth to reduce masking')
    parser.add_argument('--scalar-motion', action='store_true',
                        help='Enable scalar kaleidoscope motion - smooth ascending/descending with cycle-back (like rotating kaleidoscope)')
    parser.add_argument('--select-n', type=int, default=0,
                        help='Auto-select the N most interesting chords for kaleidoscope (0 = use all)')
    parser.add_argument('--interactive', '-i', action='store_true',
                        help='Interactive mode - prompts for options after importing')

    args = parser.parse_args()

    # Prompt for input file if not provided
    if not args.input:
        print("\n" + "="*60)
        print("SDIF Spectral Processor")
        print("="*60)
        args.input = _prompt_text("Input file path (SDIF, Partiels JSON, or MusicXML)", "")
        if not args.input:
            print("Error: No input file specified")
            return

    # Clean up path - remove escape characters and trailing spaces
    args.input = args.input.strip().replace('\\ ', ' ').replace("\\'", "'")

    # Read input file - detect format by type (directory, JSON, MusicXML, or SDIF)
    print(f"\nReading {args.input}...")

    input_path = Path(args.input)

    if input_path.is_dir():
        # Directory of JSON files (like Partiels exports)
        print("  Format: Directory of Partiels JSON files")
        reader = PartielsReader(args.input)
        raw_data = reader.read_directory()
    elif input_path.suffix.lower() == '.json':
        # Single Partiels JSON format
        print("  Format: Partiels JSON export")
        reader = PartielsReader(args.input)
        raw_data = reader.read()
    elif input_path.suffix.lower() in ('.musicxml', '.xml', '.mxl'):
        # MusicXML format
        print("  Format: MusicXML")
        reader = MusicXMLReader(args.input)
        raw_data = reader.read()
    else:
        # SDIF format (default)
        print("  Format: SDIF")
        reader = SDIFReader(args.input)
        raw_data = reader.read()

    print(f"Read {len(raw_data.frames)} frames")

    # Check if user wants command-line mode (any transformation flags provided)
    cli_mode = any([
        args.group_chords, args.pitch_shift != 0.0, args.morph_with,
        args.amplitude_scale != 1.0, args.spectral_stretch != 1.0,
        args.kaleidoscope
    ])

    # Use interactive mode by default unless CLI flags are provided
    if not cli_mode or args.interactive:
        interactive_mode(raw_data, args.input)
        return

    # Group into chords if requested
    cli_juiciest_source_chords = None
    if args.group_chords:
        if args.chord_mode == 'transition':
            print(f"Grouping partials into chords (mode=transition, "
                  f"threshold={args.transition_threshold*100}%, gap={args.transition_gap}s)...")
            chord_seq = ChordGrouper.group_to_chords(
                raw_data,
                mode='transition',
                transition_threshold=args.transition_threshold,
                transition_gap=args.transition_gap
            )
        else:
            print(f"Grouping partials into chords (mode={args.chord_mode}, window={args.window_size}s)...")
            chord_seq = ChordGrouper.group_to_chords(
                raw_data,
                window_size=args.window_size,
                freq_tolerance=args.freq_tolerance,
                mode=args.chord_mode
            )
        print(f"Created {len(chord_seq.chords)} chord groups")

        # Apply transformations on chord sequence
        if args.pitch_shift != 0.0:
            print(f"Applying pitch shift: {args.pitch_shift} semitones")
            chord_seq = SpectralTransformer.transform_chords(
                chord_seq, SpectralTransformer.pitch_shift, args.pitch_shift
            )

        if args.morph_with:
            print(f"Morphing with {args.morph_with} (factor: {args.morph_factor})")
            morph_reader = SDIFReader(args.morph_with)
            morph_data = morph_reader.read()
            morph_chords = ChordGrouper.group_to_chords(
                morph_data, window_size=args.window_size,
                freq_tolerance=args.freq_tolerance, mode=args.chord_mode
            )
            # Convert both to SDIF, morph, then back to chords
            sdif1 = ChordGrouper.chords_to_sdif(chord_seq)
            sdif2 = ChordGrouper.chords_to_sdif(morph_chords)
            morphed = SpectralTransformer.spectral_morph(sdif1, sdif2, args.morph_factor)
            chord_seq = ChordGrouper.group_to_chords(
                morphed, window_size=args.window_size, freq_tolerance=args.freq_tolerance
            )

        if args.amplitude_scale != 1.0:
            print(f"Scaling amplitude by {args.amplitude_scale}")
            chord_seq = SpectralTransformer.transform_chords(
                chord_seq, SpectralTransformer.amplitude_scale, args.amplitude_scale
            )

        if args.spectral_stretch != 1.0:
            print(f"Applying spectral stretch: {args.spectral_stretch}x (reference: {args.stretch_reference})")
            chord_seq = SpectralTransformer.transform_chords(
                chord_seq, SpectralTransformer.spectral_stretch,
                args.spectral_stretch, args.stretch_reference
            )

        # --- Juiciest chord selection (CLI) ---
        if args.select_n > 0 and len(chord_seq.chords) > args.select_n:
            original_count = len(chord_seq.chords)
            chord_seq = ChordGrouper.select_juiciest(chord_seq, n=args.select_n)
            print(f"Selected {len(chord_seq.chords)} juiciest chords (from {original_count}):")
            for si, sc in enumerate(chord_seq.chords):
                sfreqs = sorted([f for f, a, p in sc.partials if f > 0])
                shared_str = ""
                if si > 0:
                    prev_f = sorted([f for f, a, p in chord_seq.chords[si-1].partials if f > 0])
                    shared = ChordGrouper._shared_pitch_count(sfreqs, prev_f)
                    if shared > 0:
                        shared_str = f"  (shares {shared} pitch{'es' if shared > 1 else ''})"
                print(f"  {si+1}. t={sc.start_time:.2f}s  n={len(sfreqs)}  "
                      f"freqs={[round(f,0) for f in sfreqs[:6]]}{shared_str}")
            # Store source chords for prepending before kaleidoscope output
            cli_juiciest_source_chords = [
                Chord(partials=c.partials[:], start_time=c.start_time,
                      end_time=c.end_time, stability_score=c.stability_score)
                for c in chord_seq.chords
            ]

        if args.kaleidoscope:
            if args.kaleidoscope_mode in ['auto', 'nested_cycles', 'temporal_evolution', 'sequential_evolution', 'interweave', 'morph']:
                # Use advanced chord-based kaleidoscope
                # Determine transition style (sharp_cuts overrides smooth_transitions)
                smooth_trans = not args.kaleidoscope_sharp_cuts
                transition_style = "smooth glissando" if smooth_trans else "sharp cuts/gaps"

                # Determine cyclic return (no_cyclic_return overrides cyclic_return)
                cyclic = not args.kaleidoscope_no_cyclic_return

                omit_mode = "reflections only" if args.kaleidoscope_omit_originals else "originals + reflections"
                shuffle_mode = "shuffled" if args.kaleidoscope_shuffle_order else "sequential"
                print(f"Applying kaleidoscope (mode: {args.kaleidoscope_mode}, "
                      f"rotation duration: {args.kaleidoscope_rotation_duration}s, "
                      f"reflection: {args.kaleidoscope_reflection_mode}, "
                      f"cyclic: {cyclic}, {omit_mode}, order: {shuffle_mode}, transitions: {transition_style})")
                chord_seq = ChordGrouper.kaleidoscope_chords(
                    chord_seq,
                    mode=args.kaleidoscope_mode,
                    rotation_duration=args.kaleidoscope_rotation_duration,
                    smooth_transitions=smooth_trans,
                    cyclic_return=cyclic,
                    reflection_mode=args.kaleidoscope_reflection_mode,
                    omit_originals=args.kaleidoscope_omit_originals,
                    shuffle_order=args.kaleidoscope_shuffle_order,
                    max_partials_per_chord=12,
                    upward_rotation=args.kaleidoscope_upward_rotation,
                    rise_semitones=args.kaleidoscope_rise_semitones,
                    fade_low_hz=args.kaleidoscope_fade_low,
                    fade_high_hz=args.kaleidoscope_fade_high,
                    psychoacoustic_spacing=args.psychoacoustic_spacing,
                    scalar_motion=args.scalar_motion
                )
            else:
                # Use simple reflection mode
                print(f"Applying kaleidoscope reflection (center: {args.kaleidoscope_center}, "
                      f"reflections: {args.kaleidoscope_reflections})")
                chord_seq = SpectralTransformer.transform_chords(
                    chord_seq, SpectralTransformer.kaleidoscope_reflect,
                    args.kaleidoscope_center, args.kaleidoscope_freq,
                    args.kaleidoscope_reflections, args.kaleidoscope_keep_original,
                    args.kaleidoscope_invert_amps
                )

        # --- Prepend source chords before kaleidoscope output (CLI) ---
        if cli_juiciest_source_chords and args.kaleidoscope and chord_seq.chords:
            print(f"Prepending {len(cli_juiciest_source_chords)} source chords before kaleidoscope output")
            src_duration = 0.5  # each source chord gets 0.5s
            retimed_src = []
            for si, sc in enumerate(cli_juiciest_source_chords):
                t_start = si * src_duration
                retimed_src.append(Chord(
                    partials=sc.partials[:],
                    start_time=t_start,
                    end_time=t_start + src_duration,
                    stability_score=sc.stability_score
                ))
            # Shift all kaleidoscope chords forward in time
            offset = len(cli_juiciest_source_chords) * src_duration
            for kc in chord_seq.chords:
                kc.start_time += offset
                kc.end_time += offset
            chord_seq.chords = retimed_src + chord_seq.chords

        # Convert back to SDIF for output with sustained chords
        print(f"Creating sustained chords (hold duration: {args.chord_hold}s)")
        processed_data = ChordGrouper.chords_to_sdif(chord_seq, hold_duration=args.chord_hold)
        export_data = chord_seq  # Export chord sequence to MusicXML

    else:
        # Apply transformations on raw SDIF data
        processed_data = raw_data

        if args.pitch_shift != 0.0:
            print(f"Applying pitch shift: {args.pitch_shift} semitones")
            processed_data = SpectralTransformer.pitch_shift(processed_data, args.pitch_shift)

        if args.morph_with:
            print(f"Morphing with {args.morph_with} (factor: {args.morph_factor})")
            morph_reader = SDIFReader(args.morph_with)
            morph_data = morph_reader.read()
            processed_data = SpectralTransformer.spectral_morph(
                processed_data, morph_data, args.morph_factor
            )

        if args.amplitude_scale != 1.0:
            print(f"Scaling amplitude by {args.amplitude_scale}")
            processed_data = SpectralTransformer.amplitude_scale(processed_data, args.amplitude_scale)

        if args.spectral_stretch != 1.0:
            print(f"Applying spectral stretch: {args.spectral_stretch}x (reference: {args.stretch_reference})")
            processed_data = SpectralTransformer.spectral_stretch(
                processed_data, args.spectral_stretch, args.stretch_reference
            )

        if args.kaleidoscope:
            # Frame-based processing only supports simple kaleidoscope
            print(f"Applying kaleidoscope reflection (center: {args.kaleidoscope_center}, "
                  f"reflections: {args.kaleidoscope_reflections})")
            print("Note: Advanced kaleidoscope modes (temporal_evolution, interweave, morph) "
                  "require --group-chords")
            processed_data = SpectralTransformer.kaleidoscope_reflect(
                processed_data, args.kaleidoscope_center, args.kaleidoscope_freq,
                args.kaleidoscope_reflections, args.kaleidoscope_keep_original,
                args.kaleidoscope_invert_amps
            )

        export_data = processed_data  # Export SDIF to MusicXML

    # Write output SDIF
    if args.output_sdif:
        print(f"Writing SDIF to {args.output_sdif}...")
        writer = SDIFWriter(args.output_sdif)
        writer.write(processed_data)
        print("SDIF file written")

    # Export to MusicXML
    if args.output_xml:
        use_eighth_tones = not args.semitone_quantize
        # Auto-add 12-TET reduction staff when juiciest selection was used
        add_semitone_staff = cli_juiciest_source_chords is not None
        quantization = "8th tones (12.5 cents)" if use_eighth_tones else "semitones"
        extra = " + 12-TET reduction staff" if add_semitone_staff else ""
        print(f"Exporting to MusicXML: {args.output_xml} (quantization: {quantization}{extra})...")
        exporter = MusicXMLExporter(args.output_xml)
        exporter.export(export_data, min_amplitude=args.min_amplitude, eighth_tone=use_eighth_tones,
                        semitone_reduction=add_semitone_staff)
        print("MusicXML file written")

    # Export to MIDI (auto-generate filename from SDIF output)
    if args.output_sdif or args.output_midi:
        # Auto-generate MIDI filename if not explicitly provided
        if args.output_midi:
            midi_path = args.output_midi
        elif args.output_sdif:
            midi_path = args.output_sdif.replace('.sdif', '.mid')
        else:
            midi_path = None

        if midi_path:
            print(f"Exporting to MIDI: {midi_path} (microtonal with pitch bends)...")
            midi_exporter = MIDIExporter(midi_path)
            midi_exporter.export(export_data, min_amplitude=args.min_amplitude)
            print("MIDI file written")

    print("Done!")


if __name__ == '__main__':
    main()
