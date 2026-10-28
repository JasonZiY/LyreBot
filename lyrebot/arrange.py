"""Arrange a MIDI file for the 21-key lyre and write it as a LyreBot score.

Run ``python -m lyrebot.arrange song.mid`` (see README.md).  The steps follow
how people arrange for the lyre by hand:

1. Split the file into parts, one per track and channel.  Drums and sound
   effects are left out.
2. Pick the melody part and take its top line.  Bars where it is silent
   borrow the top line of the other parts.
3. Transpose the whole song so that as many notes as possible land on the
   white keys, which are the only notes the lyre has.
4. Put the melody on the middle and high rows and move the few notes still
   on black keys to a neighbouring white key.
5. Find the chord on every beat and rewrite the accompaniment as broken
   chords on the low and middle rows (or keep the lowest line of the source
   accompaniment as a bass line).
6. Snap onsets to the beat and write one line per bar.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from fractions import Fraction
import math
from pathlib import Path
import re
import sys
from typing import Iterable

from .beats import Timing, find_beats
from .midi import DEFAULT_TEMPO, MidiError, MidiFile, load_midi
from .score import ScoreError, parse_score

SCORES_DIR = Path(__file__).resolve().parent.parent / "txt"

LOW, HIGH = 48, 83  # C3 to B5: the lyre's three rows
MIDDLE = 60  # C4: the melody belongs on the middle and high rows
BASS_HIGH = 71  # B4: the bass line keeps to the low and middle rows
WHITE = (0, 2, 4, 5, 7, 9, 11)
ROWS = ("zxcvbnm", "asdfghj", "qwertyu")
KEY_OF = {LOW + 12 * row + step: ROWS[row][degree]
          for row in range(3) for degree, step in enumerate(WHITE)}

NOTE_NAMES = ("C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B")
TONIC_NAMES = ("C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B")
ACCIDENTALS = {1: "♯1", 3: "♭3", 6: "♯4", 8: "♯5", 10: "♭7"}  # in jianpu, after transposing to C
FAMILIES = ("钢琴", "色彩打击", "风琴", "吉他", "贝斯", "弦乐", "合奏", "铜管",
            "簧管", "笛", "合成主音", "合成音色", "合成效果", "民族", "打击", "音效")
DRUM_CHANNEL = 9
FIRST_UNPITCHED_PROGRAM = 115  # woodblock, taiko, toms, synth drum, then sound effects

MELODY_WORDS = re.compile(r"melod|lead|vocal|voice|sing|solo|\brh\b|\bright\b|旋律|主音|人声|右手|主唱", re.I)
OTHER_WORDS = re.compile(r"bass|\blh\b|\bleft\b|accomp|chord|pad|drum|perc|贝斯|低音|左手|伴奏|和弦|鼓", re.I)

QUARTER, HALF = Fraction(1, 4), Fraction(1, 2)
LEGATO = Fraction(1, 8)
SWITCH = 6.0  # what moving the melody to another octave costs, in beats of misplaced notes
HELD = Fraction(1)  # beats a note from a recording is taken to sound, at most, when picking lines
BOTH_HANDS = 0.6  # a melody part with fewer single-note onsets likely holds the left hand too
WRITTEN = (1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 24)  # subdivisions a written score may use
MAX_BARS = 20_000


class ArrangeError(ValueError):
    """The MIDI file cannot be arranged as asked."""


@dataclass(frozen=True)
class Tone:
    start: Fraction  # beats
    end: Fraction
    pitch: int
    part: int


@dataclass
class Part:
    number: int  # 1-based, as shown to the user
    track: int
    channel: int
    name: str
    program: int
    tones: list[Tone]
    role: str = ""
    mean_pitch: float = 0.0
    monophony: float = 0.0  # share of onsets with a single note
    coverage: float = 0.0  # share of bars in which it plays

    @property
    def instrument(self) -> str:
        if self.channel == DRUM_CHANNEL:
            return "鼓"
        return FAMILIES[self.program // 8]

    @property
    def unpitched(self) -> bool:
        return self.channel == DRUM_CHANNEL or self.program >= FIRST_UNPITCHED_PROGRAM

    @property
    def label(self) -> str:
        return f"第 {self.number} 部分（{self.name or self.instrument}）"

    @property
    def span(self) -> str:
        pitches = [tone.pitch for tone in self.tones]
        return f"{note_name(min(pitches))}–{note_name(max(pitches))}"


@dataclass(frozen=True)
class Change:
    """A note moved off a black key."""
    start: Fraction
    voice: int
    before: int
    after: int


@dataclass
class Arrangement:
    text: str
    parts: list[Part]
    shift: int  # semitones applied to the whole song
    white_share: float  # share of the notes (by weight) on white keys after the shift
    exact: bool  # the file was already on a written grid
    bars: int
    changes: list[Change] = field(default_factory=list)
    folded: int = 0  # melody notes moved by an octave to fit the lyre
    strays: int = 0  # top-line notes handed back to the accompaniment
    chords: list[ChordSpan] = field(default_factory=list)
    timing: Timing | None = None  # set when the beat was found in the notes


def note_name(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


# --- Rhythm -----------------------------------------------------------------

def _written(position: Fraction, tolerance: Fraction) -> Fraction | None:
    """The simplest written position within ``tolerance``, if any."""
    for steps in WRITTEN:
        near = Fraction(round(position * steps), steps)
        if abs(near - position) <= tolerance:
            return near
    return None


def _misfit(offsets: list[Fraction], steps: int) -> Fraction:
    return sum((abs(offset - Fraction(round(offset * steps), steps)) for offset in offsets), Fraction(0))


class Grid:
    """Snap beat positions.

    Files exported from notation software already sit on a written grid, so
    their notes are kept where they are, tuplets included.  Recorded
    performances are snapped beat by beat to sixteenths, or to triplets where
    those fit better.  Rests always start on the coarse grid.
    """

    def __init__(self, onsets: list[Fraction], tick: Fraction):
        self.tick = tick
        written = [_written(onset, tick) for onset in onsets]
        self.exact = bool(onsets) and sum(1 for place in written if place is not None) >= 0.9 * len(onsets)
        # A beat mixing unrelated subdivisions (a flourish placed by hand, a
        # glissando) is free time: all of it goes to the nearest 32nd.
        subdivisions: dict[int, int] = defaultdict(lambda: 1)
        for onset, place in zip(onsets, written):
            beat = math.floor(place if place is not None else onset)
            subdivisions[beat] = math.lcm(subdivisions[beat], place.denominator if place is not None else 8)
        self.free = {beat for beat, steps in subdivisions.items() if steps not in WRITTEN or steps > 16}
        by_beat: dict[int, list[Fraction]] = defaultdict(list)
        for onset in onsets:
            beat = math.floor(onset + Fraction(1, 8))
            by_beat[beat].append(onset - beat)
        self.triplets = {beat for beat, offsets in by_beat.items()
                         if _misfit(offsets, 3) < Fraction(3, 5) * _misfit(offsets, 4)}

    def coarse(self, position: Fraction) -> Fraction:
        beat = math.floor(position + Fraction(1, 8))
        steps = 3 if beat in self.triplets else 4
        return beat + Fraction(round((position - beat) * steps), steps)

    def onset(self, position: Fraction) -> Fraction:
        if self.exact:
            # Fast runs and glissandi written in odd tuplets go to the nearest 32nd.
            written = _written(position, self.tick)
            if written is not None and math.floor(written) not in self.free:
                return written
            return Fraction(round(position * 8), 8)
        return self.coarse(position)

    def end(self, position: Fraction) -> Fraction:
        return self.onset(position)


def _bars(midi: MidiFile, end: Fraction) -> list[tuple[Fraction, Fraction]]:
    """(start, length) of every bar up to ``end``, from the time signatures."""
    lengths: dict[Fraction, Fraction] = {}
    for tick, numerator, denominator in midi.time_signatures:
        lengths[Fraction(tick, midi.ticks_per_beat)] = Fraction(4 * numerator, denominator)
    starts = sorted(lengths)
    bars: list[tuple[Fraction, Fraction]] = []
    position = Fraction(0)
    while position < end:
        if len(bars) >= MAX_BARS:
            raise ArrangeError(f"曲子超过 {MAX_BARS} 小节")
        index = bisect_right(starts, position) - 1
        length = lengths[starts[index]] if index >= 0 else Fraction(4)
        stop = position + length
        if index + 1 < len(starts) and starts[index + 1] < stop:
            stop = starts[index + 1]  # a new time signature cuts this bar short
        bars.append((position, stop - position))
        position = stop
    return bars


class _Clock:
    """Seconds at a beat position, following the MIDI tempo changes."""

    def __init__(self, midi: MidiFile):
        tempos = {Fraction(0): DEFAULT_TEMPO}
        for tick, tempo in midi.tempos:
            tempos[Fraction(tick, midi.ticks_per_beat)] = tempo
        self.beats = sorted(tempos)
        self.tempos = [tempos[beat] for beat in self.beats]
        self.elapsed = [0.0]
        for index in range(1, len(self.beats)):
            span = float(self.beats[index] - self.beats[index - 1])
            self.elapsed.append(self.elapsed[-1] + span * self.tempos[index - 1] / 1e6)

    def seconds(self, beat: Fraction) -> float:
        index = bisect_right(self.beats, beat) - 1
        return self.elapsed[index] + float(beat - self.beats[index]) * self.tempos[index] / 1e6

    def bpm(self, start: Fraction, length: Fraction) -> float:
        """The steady tempo that plays this stretch in the same time."""
        seconds = self.seconds(start + length) - self.seconds(start)
        return min(1000.0, max(1.0, 60.0 * float(length) / seconds)) if seconds > 0 else 120.0


# --- Parts and lines --------------------------------------------------------

def split_parts(midi: MidiFile, grid: Grid) -> list[Part]:
    groups: dict[tuple[int, int], dict[tuple[Fraction, int], Tone]] = defaultdict(dict)
    numbers: dict[tuple[int, int], int] = {}
    for key in sorted({(note.track, note.channel) for note in midi.notes}):
        numbers[key] = len(numbers) + 1
    for note in midi.notes:
        key = (note.track, note.channel)
        start = grid.onset(Fraction(note.start, midi.ticks_per_beat))
        end = grid.end(Fraction(note.end, midi.ticks_per_beat))
        tone = Tone(start, end, note.pitch, numbers[key])
        known = groups[key].get((start, note.pitch))
        if known is None or known.end < end:  # the same note twice: keep the longer
            groups[key][(start, note.pitch)] = tone
    parts = []
    for (track, channel), number in numbers.items():
        name = midi.track_names[track] if track < len(midi.track_names) else ""
        tones = sorted(groups[(track, channel)].values(), key=lambda tone: (tone.start, tone.pitch))
        parts.append(Part(number, track, channel, name, midi.programs.get((track, channel), 0), tones))
    return parts


def _describe(parts: list[Part], bars: list[tuple[Fraction, Fraction]]) -> None:
    starts = [start for start, _ in bars]
    for part in parts:
        part.mean_pitch = sum(tone.pitch for tone in part.tones) / len(part.tones)
        onsets = Counter(tone.start for tone in part.tones)
        part.monophony = sum(1 for count in onsets.values() if count == 1) / len(onsets)
        played = {bisect_right(starts, tone.start) - 1 for tone in part.tones}
        part.coverage = len(played) / len(bars)


def pick_melody(parts: list[Part]) -> Part:
    """The part most likely to carry the tune: high, one note at a time,
    playing through most of the song, or named like a melody."""
    total = sum(len(part.tones) for part in parts)
    sizeable = [part for part in parts if len(part.tones) >= max(8, 0.05 * total)] or parts
    low = min(part.mean_pitch for part in sizeable)
    high = max(part.mean_pitch for part in sizeable)

    def score(part: Part) -> float:
        height = (part.mean_pitch - low) / (high - low) if high > low else 0.5
        named = 2.0 if MELODY_WORDS.search(part.name) else -2.0 if OTHER_WORDS.search(part.name) else 0.0
        return height + part.monophony + part.coverage + named

    return max(sizeable, key=score)


def outer_line(tones: Iterable[Tone], top: bool, hold: Fraction | None = None) -> list[Tone]:
    """The highest (or lowest) line: at each onset, the outermost note, unless
    a note further out is still sounding from before.  A note that overlaps
    the next one by at most an eighth of a beat is legato, not still sounding.
    With ``hold``, no note counts as sounding longer than that many beats:
    lengths heard in a recording include the ring of the instrument."""
    sign = 1 if top else -1
    ordered = sorted(tones, key=lambda tone: (tone.start, -sign * tone.pitch))
    line: list[Tone] = []
    sounding: list[Tone] = []
    index = 0
    while index < len(ordered):
        start = ordered[index].start
        stop = index
        while stop < len(ordered) and ordered[stop].start == start:
            stop += 1
        sounding = [tone for tone in sounding
                    if tone.end > start + LEGATO and (hold is None or start - tone.start < hold)]
        best = ordered[index]
        if all(sign * best.pitch >= sign * tone.pitch for tone in sounding):
            line.append(best)
        sounding.extend(ordered[index:stop])
        index = stop
    return line


def drop_strays(line: list[Tone], window: Fraction = Fraction(2), reach: int = 12) -> tuple[list[Tone], list[Tone]]:
    """Split off top-line notes far below the line around them.

    In a piano part the top line falls into the left hand whenever the right
    hand pauses.  Such a note lies more than ``reach`` semitones under the
    median of the top line within ``window`` beats either side, so it goes
    back to the accompaniment.
    """
    starts = [tone.start for tone in line]
    kept: list[Tone] = []
    strays: list[Tone] = []
    for tone in line:
        nearby = sorted(other.pitch for other in
                        line[bisect_left(starts, tone.start - window):bisect_right(starts, tone.start + window)])
        median = nearby[(len(nearby) - 1) // 2]
        (strays if tone.pitch < median - reach else kept).append(tone)
    return kept, strays


def fill_silences(melody: list[Tone], spare: list[Tone], bars: list[tuple[Fraction, Fraction]]) -> list[Tone]:
    """The top line of ``spare`` (notes neither melody nor bass) in bars where
    the melody is silent, so a countermelody or the upper accompaniment can
    carry them."""
    starts = [start for start, _ in bars]
    busy: set[int] = set()
    for tone in melody:
        first = bisect_right(starts, tone.start) - 1
        last = bisect_left(starts, tone.end) - 1 if tone.end > tone.start else first
        busy.update(range(first, max(first, last) + 1))
    return outer_line([tone for tone in spare if bisect_right(starts, tone.start) - 1 not in busy], top=True)


# --- Pitch ------------------------------------------------------------------

def _weight(tone: Tone) -> Fraction:
    return min(max(tone.end - tone.start, QUARTER), Fraction(2))


def choose_shift(melody: list[Tone], others: list[Tone]) -> tuple[int, float]:
    """The transposition putting the most weight on white keys.

    Melody notes count three times.  When several shifts fit equally well,
    leave the song where it is if that is one of them.  Otherwise prefer the
    shift that uses F and B least: pentatonic tunes then sit on C, D, E, G
    and A, the way they are written in jianpu.
    """
    classes = [Fraction(0)] * 12
    for tone in melody:
        classes[tone.pitch % 12] += 3 * _weight(tone)
    for tone in others:
        classes[tone.pitch % 12] += _weight(tone)
    total = sum(classes)

    def white(shift: int) -> Fraction:
        return sum((classes[(step - shift) % 12] for step in WHITE), Fraction(0))

    def rank(shift: int) -> tuple:
        avoided = classes[(5 - shift) % 12] + classes[(11 - shift) % 12]
        return -white(shift), shift != 0, avoided, abs(shift), -shift

    shift = min(range(-5, 7), key=rank)
    return shift, float(white(shift) / total) if total else 1.0


def white_share(melody: list[Tone], others: list[Tone], shift: int) -> float:
    weights = [(tone.pitch, 3 * _weight(tone)) for tone in melody] + [(tone.pitch, _weight(tone)) for tone in others]
    total = sum(weight for _, weight in weights)
    inside = sum(weight for pitch, weight in weights if (pitch + shift) % 12 in WHITE)
    return float(inside / total) if total else 1.0


def place_octave(line: list[Tone], shift: int, preferred: tuple[int, int], allowed: tuple[int, int],
                 centre: int) -> int:
    """The octave shift for a whole line.

    The line moves as one piece so that its shape survives.  Notes in the
    preferred range count most; notes that would have to be folded back by an
    octave count against it.  Ties go to the shift closest to ``centre``.
    """
    if not line:
        return 0
    total = sum(_weight(tone) for tone in line)
    mean = float(sum(tone.pitch * _weight(tone) for tone in line) / total) + shift

    def rank(octave: int) -> tuple:
        score = Fraction(0)
        for tone in line:
            pitch = tone.pitch + shift + octave
            if preferred[0] <= pitch <= preferred[1]:
                score += 2 * _weight(tone)
            elif allowed[0] <= pitch <= allowed[1]:
                score += _weight(tone)
            else:
                score -= 3 * _weight(tone)
        return -score, abs(mean + octave - centre)

    return min((12 * step for step in range(-6, 7)), key=rank)


def melody_octaves(line: list[Tone], shift: int, bars: list[tuple[Fraction, Fraction]]) -> list[int]:
    """An octave shift for each melody note, chosen bar by bar.

    A bar costs nothing for notes on the middle and high rows, 1 per beat of
    notes on the low row and 3 per beat of notes that would have to be folded
    back.  Changing octave between bars costs ``SWITCH``, so a whole passage
    moves together (an intro doubled an octave higher, say) while a single
    stray note stays where it is.  Ties go to the octave nearest the centre.
    """
    if not line:
        return []
    starts = [start for start, _ in bars]
    octaves = [12 * step for step in range(-6, 7)]
    by_bar: dict[int, list[Tone]] = defaultdict(list)
    for tone in line:
        by_bar[bisect_right(starts, tone.start) - 1].append(tone)
    order = sorted(by_bar)

    def cost(tones: list[Tone], octave: int) -> float:
        total = 0.0
        for tone in tones:
            pitch = tone.pitch + shift + octave
            misplaced = 0 if MIDDLE <= pitch <= HIGH else 1 if LOW <= pitch < MIDDLE else 3
            total += float(_weight(tone)) * (misplaced + 0.001 * abs(pitch - 71.5))
        return total

    best = {octave: cost(by_bar[order[0]], octave) for octave in octaves}
    steps: list[dict[int, int]] = []
    for bar in order[1:]:
        came_from = {octave: min(octaves, key=lambda before: best[before] + SWITCH * (before != octave))
                     for octave in octaves}
        best = {octave: best[came_from[octave]] + SWITCH * (came_from[octave] != octave) + cost(by_bar[bar], octave)
                for octave in octaves}
        steps.append(came_from)
    octave = min(octaves, key=lambda candidate: best[candidate])
    chosen = [octave]
    for came_from in reversed(steps):
        octave = came_from[octave]
        chosen.append(octave)
    per_bar = dict(zip(order, reversed(chosen)))
    return [per_bar[bisect_right(starts, tone.start) - 1] for tone in line]


def _fold(pitch: int, low: int, high: int) -> int:
    while pitch < low:
        pitch += 12
    while pitch > high:
        pitch -= 12
    return pitch


def snap_line(pitches: list[int]) -> list[int]:
    """Move black-key notes to a neighbouring white key.

    Prefer the neighbour that does not repeat the next note, then the one that
    does not repeat the previous note, so the line keeps moving: G♯ before A
    becomes G, B♭ before A becomes B.  Otherwise take the lower neighbour.
    """
    snapped: list[int] = []
    for index, pitch in enumerate(pitches):
        if pitch % 12 in WHITE:
            snapped.append(pitch)
            continue
        following = pitches[index + 1] if index + 1 < len(pitches) else None
        previous = snapped[-1] if snapped else None
        snapped.append(min((pitch - 1, pitch + 1),
                           key=lambda option: (option == following, option == previous, option > pitch)))
    return snapped


# --- Harmony ----------------------------------------------------------------

CHORDS = (("C", (0, 4, 7)), ("Dm", (2, 5, 9)), ("Em", (4, 7, 11)), ("F", (5, 9, 0)),
          ("G", (7, 11, 2)), ("Am", (9, 0, 4)), ("Bdim", (11, 2, 5)))  # the triads on the white keys
FAST = 150  # above this tempo the accompaniment moves in quarters instead of eighths


@dataclass(frozen=True)
class ChordSpan:
    start: Fraction
    end: Fraction
    chord: int  # index into CHORDS

    @property
    def name(self) -> str:
        return CHORDS[self.chord][0]


def _beats(bars: list[tuple[Fraction, Fraction]]) -> list[tuple[Fraction, Fraction, float]]:
    """(start, end, cost of changing chord there) for every beat."""
    beats = []
    for start, length in bars:
        position = Fraction(0)
        while position < length:
            step = min(Fraction(1), length - position)
            if position == 0:
                change = 0.15  # the downbeat
            elif length >= 4 and position == length / 2:
                change = 0.3  # the middle of a four-beat bar
            else:
                change = 0.6
            beats.append((start + position, start + position + step, change))
            position += step
    return beats


def chord_fit(profile: list[float], bass: list[float], chord: int) -> float:
    """How well a beat's notes (weight per pitch class) match a triad."""
    total = sum(profile)
    if not total:
        return 0.0
    tones = CHORDS[chord][1]
    inside = sum(profile[step] for step in tones) / total
    fit = inside - 0.6 * (1 - inside)
    lowest = sum(bass)
    if lowest:  # a bass on the root makes the chord much more likely; on the third or fifth, a little
        fit += (0.4 * bass[tones[0]] + 0.1 * (bass[tones[1]] + bass[tones[2]])) / lowest
    return fit - 0.15 * (CHORDS[chord][0] == "Bdim")  # rarely a chord of its own


def detect_chords(melody: list[Tone], others: list[Tone], bass: list[Tone], shift: int,
                  bars: list[tuple[Fraction, Fraction]]) -> list[ChordSpan]:
    """One white-key triad per stretch of the song, found beat by beat.

    Each beat's notes, weighted by how long they sound in it, are compared
    with the seven triads.  Melody notes count half, since the melody carries
    passing notes; the bass favours the chord built on it.  A Viterbi pass
    then settles where the chord changes: cheaply on a downbeat, a little
    more in the middle of a four-beat bar, dearly anywhere else.
    """
    beats = _beats(bars)
    starts = [beat[0] for beat in beats]
    profiles = [[0.0] * 12 for _ in beats]
    lows = [[0.0] * 12 for _ in beats]

    def add(tones: list[Tone], weight: float, into: list[list[float]]) -> None:
        for tone in tones:
            end = tone.end if tone.end > tone.start else tone.start + QUARTER
            index = max(bisect_right(starts, tone.start) - 1, 0)
            while index < len(beats) and beats[index][0] < end:
                overlap = min(end, beats[index][1]) - max(tone.start, beats[index][0])
                if overlap > 0:
                    into[index][(tone.pitch + shift) % 12] += weight * float(overlap)
                index += 1

    add(melody, 0.5, profiles)
    add(others, 1.0, profiles)
    add(bass, 1.0, lows)
    chords = range(len(CHORDS))
    cost = [-chord_fit(profiles[0], lows[0], chord) for chord in chords]
    steps: list[list[int]] = []
    for index in range(1, len(beats)):
        change = beats[index][2]
        cheapest = min(chords, key=lambda chord: cost[chord])
        came_from = [chord if cost[chord] <= cost[cheapest] + change else cheapest for chord in chords]
        cost = [cost[came_from[chord]] + change * (came_from[chord] != chord)
                - chord_fit(profiles[index], lows[index], chord) for chord in chords]
        steps.append(came_from)
    chord = min(chords, key=lambda candidate: cost[candidate])
    path = [chord]
    for came_from in reversed(steps):
        chord = came_from[chord]
        path.append(chord)
    path.reverse()
    spans: list[ChordSpan] = []
    for (start, end, _), chord in zip(beats, path):
        if spans and spans[-1].chord == chord:
            spans[-1] = ChordSpan(spans[-1].start, end, chord)
        else:
            spans.append(ChordSpan(start, end, chord))
    return spans


def voicing(chord: int) -> list[int]:
    """Root and fifth on the low row, then the next three chord tones above
    them: F is F3 C3 A3 C4 F4, C is C3 G3 C4 E4 G4."""
    tones = CHORDS[chord][1]
    root, fifth = LOW + tones[0], LOW + tones[2]
    upper = [pitch for pitch in range(max(root, fifth) + 1, HIGH + 1) if pitch % 12 in tones]
    return [root, fifth, *upper[:3]]


def mountain(count: int) -> list[int]:
    """Indices into a voicing for ``count`` notes: up from the root and back
    down, 0 1 2 3 4 3 2 1 for a bar of eighths in 4/4, 0 1 2 3 2 1 in 3/4."""
    if count > 8:
        cycle = (0, 1, 2, 3, 4, 3, 2, 1)
        return [cycle[index % 8] for index in range(count)]
    peak = count // 2 + 1
    return list(range(peak)) + list(range(peak - 2, peak - 2 - (count - peak), -1))


def accompany(spans: list[ChordSpan], bars: list[tuple[Fraction, Fraction]], others: list[Tone],
              melody: list[tuple[Fraction, int]], unit: Fraction) -> list[tuple[Fraction, Fraction, str]]:
    """Broken chords, one note per ``unit``, wherever the source accompaniment plays.

    The pattern restarts at every bar and every chord change.  A note that
    would reach within a minor third of the melody drops an octave, or is left
    out when it cannot.
    """
    covered: list[list[Fraction]] = []
    for start, end in sorted((tone.start, max(tone.end, tone.start + QUARTER)) for tone in others):
        if covered and start <= covered[-1][1]:
            covered[-1][1] = max(covered[-1][1], end)
        else:
            covered.append([start, end])
    covered_starts = [start for start, _ in covered]

    def busy(begin: Fraction, end: Fraction) -> bool:
        index = bisect_left(covered_starts, end) - 1
        return index >= 0 and covered[index][1] > begin

    melody_starts = [start for start, _ in melody]

    def under_melody(at: Fraction, pitch: int) -> int | None:
        index = bisect_right(melody_starts, at) - 1
        if index < 0 or at - melody[index][0] >= 4:
            return pitch
        ceiling = melody[index][1] - 3
        while pitch > ceiling and pitch - 12 >= LOW:
            pitch -= 12
        return pitch if pitch <= ceiling else None

    line: list[tuple[Fraction, Fraction, str]] = []
    first = 0
    for bar_start, length in bars:
        bar_end = bar_start + length
        while first < len(spans) and spans[first].end <= bar_start:
            first += 1
        index = first
        while index < len(spans) and spans[index].start < bar_end:
            begin, end = max(spans[index].start, bar_start), min(spans[index].end, bar_end)
            voiced = voicing(spans[index].chord)
            for step, which in enumerate(mountain(math.ceil((end - begin) / unit))):
                at = begin + step * unit
                stop = min(at + unit, end)
                pitch = under_melody(at, voiced[which]) if busy(at, stop) else None
                if pitch is not None:
                    line.append((at, stop, KEY_OF[pitch]))
            index += 1
    return line


# --- Writing ----------------------------------------------------------------

def duration_text(length: Fraction) -> str:
    whole, rest = divmod(length, 1)
    text = "-" * int(whole)
    if rest and (rest * 8).denominator == 1:
        for symbol, value in ((".", HALF), ("~", QUARTER), ("*", Fraction(1, 8))):
            if rest >= value:
                text += symbol
                rest -= value
    elif rest:
        text += f"{{{rest.numerator}/{rest.denominator}}}"
    return text


@dataclass(frozen=True)
class _Segment:
    start: Fraction
    end: Fraction
    keys: str  # empty for a rest


def _segments(line: list[tuple[Fraction, Fraction, str]], total: Fraction, grid: Grid) -> list[_Segment]:
    """Cover [0, total) with notes and rests.

    A lyre note cannot be held, so a note simply lasts until the next one.  A
    rest is written where the source falls silent for a beat or more, or for
    at least half a beat and longer than the note before it sounded.
    """
    segments: list[_Segment] = []
    position = Fraction(0)
    for index, (start, end, keys) in enumerate(line):
        following = line[index + 1][0] if index + 1 < len(line) else total
        if start > position:
            segments.append(_Segment(position, start, ""))
        sounding = min(end, following) if end > start else following
        tidy = grid.coarse(sounding)
        if start < tidy < following:
            sounding = tidy
        gap = following - sounding
        if gap >= 1 or (gap >= HALF and gap > sounding - start):
            segments += [_Segment(start, sounding, keys), _Segment(sounding, following, "")]
        else:
            segments.append(_Segment(start, following, keys))
        position = following
    if position < total:
        segments.append(_Segment(position, total, ""))
    return segments


def _bar_text(segments: list[_Segment], cursor: int, begin: Fraction, end: Fraction) -> tuple[str, int]:
    pieces = []
    while cursor < len(segments) and segments[cursor].end <= begin:
        cursor += 1
    index = cursor
    while index < len(segments) and segments[index].start < end:
        segment = segments[index]
        length = duration_text(min(segment.end, end) - max(segment.start, begin))
        if segment.keys and segment.start < begin:
            pieces.append(length)  # a note carried over from the previous bar
        else:
            pieces.append((segment.keys or "0") + length)
        index += 1
    return "".join(pieces), cursor


def _bpm_text(bpm: float) -> str:
    return f"{bpm:.1f}".rstrip("0").rstrip(".")


def _numbers(parts: list[Part]) -> str:
    return "、".join(str(part.number) for part in parts)


def describe_timing(timing: Timing) -> str:
    pace = "速度平稳" if timing.steady else "速度有起伏，逐拍跟随"
    return f"节拍是从音符推断的：约每分钟 {timing.bpm:.0f} 拍（四分音符），{pace}，{timing.signature} 拍"


def _header(title: str, parts: list[Part], shift: int, share: float, signature: str, voices: int,
            accompaniment: str, timing: Timing | None = None) -> list[str]:
    tonic = (-shift) % 12
    if shift:
        moved = f"整体{'升' if shift > 0 else '降'} {abs(shift)} 个半音"
    else:
        moved = "不用移调"
    lines = [f"# 由 MIDI 自动编配：{title}" if title else "# 由 MIDI 自动编配",
             f"# 原曲的音阶相当于 {TONIC_NAMES[tonic]} 大调 / {TONIC_NAMES[(tonic + 9) % 12]} 小调，"
             f"{moved}，{share:.0%} 的音落在白键上"]
    roles: dict[str, list[Part]] = defaultdict(list)
    for part in parts:
        roles[part.role].append(part)
    described = []
    for role in ("旋律", "伴奏"):
        if roles[role]:
            described.append(f"{role}：第 {_numbers(roles[role])} 部分")
    unused = [part for part in parts if part.role not in ("旋律", "伴奏")]
    if unused:
        described.append(f"未使用：第 {_numbers(unused)} 部分")
    lines.append("# " + "；".join(described))
    second = "按和弦重写的分解和弦" if accompaniment == "chords" else "伴奏的低音线"
    layout = f"第 1 声部是旋律，第 2 声部是{second}" if voices > 1 else "只有旋律一个声部"
    lines.append(f"# 拍号 {signature}；{layout}")
    if timing:
        lines.append(f"# {describe_timing(timing)}")
    return lines


# --- The whole arrangement --------------------------------------------------

def needs_beats(midi: MidiFile, grid: Grid) -> bool:
    """Whether the file's own beat means nothing: notes off any written grid,
    no time signature, and at most one tempo, as transcribed MIDI comes."""
    return not grid.exact and not midi.time_signatures and len({tempo for _, tempo in midi.tempos}) <= 1


def _grid(midi: MidiFile) -> Grid:
    onsets = [Fraction(note.start, midi.ticks_per_beat) for note in midi.notes if note.channel != DRUM_CHANNEL]
    return Grid(onsets, Fraction(1, midi.ticks_per_beat))


def arrange(midi: MidiFile, *, title: str = "", melody: Iterable[int] = (), drop: Iterable[int] = (),
            transpose: int | None = None, fill: bool = True, accompaniment: str = "chords",
            beats: str = "auto", meter: int | None = None) -> Arrangement:
    """``accompaniment`` is "chords" (broken chords rewritten from the
    harmony) or "bass" (the lowest line of the source accompaniment).
    ``beats`` is "auto" (find the beat in the notes when the file has none of
    its own), "midi" (always trust the file) or "notes" (always find it);
    ``meter`` fixes the beats per bar."""
    if accompaniment not in ("chords", "bass"):
        raise ArrangeError(f"未知的伴奏方式 {accompaniment!r}")
    if beats not in ("auto", "midi", "notes"):
        raise ArrangeError(f"未知的节拍来源 {beats!r}")
    if meter is not None and not 1 <= meter <= 12:
        raise ArrangeError("每小节的拍数应在 1 到 12 之间")
    grid = _grid(midi)
    timing: Timing | None = None
    if beats == "notes" or (beats == "auto" and needs_beats(midi, grid)):
        found = find_beats(midi, meter)
        if found:
            midi, timing = found
            grid = _grid(midi)
    elif meter:
        midi = replace(midi, time_signatures=((0, meter, 4),))
    parts = split_parts(midi, grid)
    if not parts:
        raise ArrangeError("MIDI 文件里没有音符")

    wanted, dropped = set(melody), set(drop)
    unknown = sorted((wanted | dropped) - {part.number for part in parts})
    if unknown:
        raise ArrangeError(f"没有第 {'、'.join(map(str, unknown))} 部分；共有 {len(parts)} 个部分")
    if wanted & dropped:
        raise ArrangeError("同一个部分不能既是旋律又不使用")
    for part in parts:
        if part.number in dropped:
            part.role = "未使用"
        elif part.unpitched and part.number not in wanted:
            part.role = "未使用（鼓）" if part.channel == DRUM_CHANNEL else "未使用（打击/音效）"
    usable = [part for part in parts if not part.role]
    if not usable:
        raise ArrangeError("除了鼓和音效以外没有可用的音符")

    end = max(max(tone.end, tone.start + QUARTER) for part in usable for tone in part.tones)
    bars = _bars(midi, end)
    _describe(usable, bars)
    chosen = [part for part in usable if part.number in wanted] or [pick_melody(usable)]
    for part in usable:
        part.role = "旋律" if part in chosen else "伴奏"

    hold = HELD if timing else None  # a recording's note lengths include the ring
    top = outer_line([tone for part in chosen for tone in part.tones], top=True, hold=hold)
    strays: list[Tone] = []
    if min(part.monophony for part in chosen) < BOTH_HANDS:
        top, strays = drop_strays(top)
    taken = set(top)
    others = [tone for part in usable for tone in part.tones if tone not in taken]
    bass = outer_line(others, top=False, hold=hold)
    lowest = set(bass)
    borrowed = fill_silences(top, [tone for tone in others if tone not in lowest], bars) if fill else []

    if transpose is None:
        shift, share = choose_shift(top, others)
    else:
        shift, share = transpose, white_share(top, others, transpose)
    octaves = melody_octaves(top, shift, bars)
    borrowed_octave = place_octave(borrowed, shift, (MIDDLE, HIGH), (MIDDLE, HIGH), 72)
    bass_octave = place_octave(bass, shift, (LOW, MIDDLE - 1), (LOW, BASS_HIGH), 55)

    folded = 0
    melody_notes: list[tuple[Tone, int]] = []
    for tone, octave in zip(top, octaves):
        pitch = tone.pitch + shift + octave
        placed = _fold(pitch, LOW, HIGH)
        folded += placed != pitch
        melody_notes.append((tone, placed))
    melody_notes += [(tone, _fold(tone.pitch + shift + borrowed_octave, MIDDLE, HIGH)) for tone in borrowed]
    melody_notes.sort(key=lambda item: item[0].start)
    bass_notes = [(tone, _fold(tone.pitch + shift + bass_octave, LOW, BASS_HIGH)) for tone in bass]

    clock = _Clock(midi)
    total = bars[-1][0] + bars[-1][1]
    starts = [start for start, _ in bars]
    spans: list[ChordSpan] = []
    changes: list[Change] = []
    lines = []
    voices = (melody_notes,) if accompaniment == "chords" else (melody_notes, bass_notes)
    for voice, placed in enumerate(voices):
        tones = [tone for tone, _ in placed]
        pitches = [pitch for _, pitch in placed]
        snapped = snap_line(pitches)
        changes += [Change(tone.start, voice, before, after)
                    for tone, before, after in zip(tones, pitches, snapped) if before != after]
        lines.append([(tone.start, tone.end, KEY_OF[pitch]) for tone, pitch in zip(tones, snapped)])
        if voice == 0 and accompaniment == "chords":
            spans = detect_chords(top, others, bass, shift, bars)
            unit = Fraction(1) if clock.bpm(Fraction(0), total) > FAST else HALF
            lines.append(accompany(spans, bars, others,
                                   [(tone.start, pitch) for tone, pitch in zip(tones, snapped)], unit))
    lines = [line for line in lines if line]

    segments = [_segments(line, total, grid) for line in lines]
    first_onset = min(line[0][0] for line in lines)
    first_bar = bisect_right(starts, first_onset) - 1
    remarks: dict[int, list[str]] = defaultdict(list)
    for change in changes:
        voice = "" if change.voice == 0 else "低音"
        remarks[bisect_right(starts, change.start) - 1].append(
            f"{voice}{ACCIDENTALS[change.before % 12]}→{KEY_OF[change.after]}")
    harmony: dict[int, list[str]] = defaultdict(list)
    if accompaniment == "chords" and len(lines) > 1:
        played = {bisect_right(starts, start) - 1 for start, _, _ in lines[-1]}
        for span in spans:
            for index in range(bisect_right(starts, span.start) - 1, bisect_left(starts, span.end)):
                if index in played and (not harmony[index] or harmony[index][-1] != span.name):
                    harmony[index].append(span.name)

    numerator, denominator = 4, 4
    for tick, top_number, bottom_number in midi.time_signatures:
        if tick == 0:
            numerator, denominator = top_number, bottom_number
    text_lines = _header(title, parts, shift, share, f"{numerator}/{denominator}",
                         len(lines), accompaniment, timing)
    cursors = [0] * len(segments)
    last_bpm: float | None = None
    # A followed beat wobbles a little from bar to bar; only real tempo changes get a speed= line.
    tolerance = 0.03 if timing and not timing.steady else 0.01
    for index in range(first_bar, len(bars)):
        begin, length = bars[index]
        bpm = round(clock.bpm(begin, length), 1)
        if last_bpm is None or abs(bpm - last_bpm) > tolerance * last_bpm:
            text_lines.append(f"speed={_bpm_text(bpm)}")
            last_bpm = bpm
        pieces = []
        for voice, voice_segments in enumerate(segments):
            piece, cursors[voice] = _bar_text(voice_segments, cursors[voice], begin, begin + length)
            pieces.append(piece)
        line = "|".join(pieces)
        notes = []
        if harmony.get(index):
            notes.append("和弦 " + " ".join(harmony[index]))
        if remarks.get(index):
            notes.append("改动 " + " ".join(remarks[index]))
        if notes:
            line += "  # " + "；".join(notes)
        text_lines.append(line)
    text = "\n".join(text_lines) + "\n"
    try:
        parse_score(text)
    except ScoreError as error:  # a bug here, not a problem with the MIDI file
        raise ArrangeError(f"生成的曲谱无法解析：{error}") from error
    return Arrangement(text, parts, shift, share, grid.exact, len(bars) - first_bar, changes, folded, len(strays),
                       spans, timing)


# --- Command line -----------------------------------------------------------

def _part_table(parts: list[Part]) -> str:
    rows = ["编号  轨道  通道  音符数  音域       单音  覆盖  乐器      角色      名称"]
    for part in parts:
        stats = (f"{part.monophony:4.0%}  {part.coverage:4.0%}" if part.mean_pitch else "    -     -")
        rows.append(f"{part.number:>4}  {part.track + 1:>4}  {part.channel + 1:>4}  {len(part.tones):>6}  "
                    f"{part.span:<9}  {stats}  {part.instrument:<8}  {part.role or '-':<8}  {part.name}")
    return "\n".join(rows)


def _numbers_argument(text: str | None) -> list[int]:
    if not text:
        return []
    try:
        return [int(item) for item in re.split(r"[,，\s]+", text.strip()) if item]
    except ValueError:
        raise SystemExit(f"部分编号应写成 2 或 2,3，而不是 {text!r}") from None


def save_score(result: Arrangement, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.text, encoding="utf-8", newline="\n")


def arrange_file(source: Path, target: Path) -> Arrangement:
    """Arrange a MIDI file with the automatic choices and save the score."""
    result = arrange(load_midi(source), title=source.name)
    save_score(result, target)
    return result


def _free_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise ArrangeError(f"{path.name} 的同名文件太多了")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="python -m lyrebot.arrange",
                                     description="把 MIDI 文件编配成 LyreBot 曲谱（旋律 + 伴奏两个声部）")
    parser.add_argument("midi", nargs="+", type=Path, help="MIDI 文件，可以一次给多个")
    parser.add_argument("-o", "--output", type=Path, help="曲谱保存位置（只给一个 MIDI 时可用）；默认存进 txt 文件夹")
    parser.add_argument("--list", action="store_true", help="只列出各个部分和自动判断的角色，不生成曲谱")
    parser.add_argument("--melody", help="指定旋律所在的部分编号，可写多个，如 2,3")
    parser.add_argument("--drop", help="不使用的部分编号，如 4,5")
    parser.add_argument("--transpose", type=int, help="手动指定整体移调的半音数，如 -3")
    parser.add_argument("--bass-line", action="store_true", help="第 2 声部用原曲伴奏的低音线，不按和弦重写")
    parser.add_argument("--beats", choices=("auto", "midi", "notes"), default="auto",
                        help="节拍从哪来：auto 只在 MIDI 没有拍号和速度信息时（如音频转录的）从音符推断，"
                             "midi 一律用 MIDI 自带的，notes 一律从音符推断")
    parser.add_argument("--meter", type=int, help="指定每小节几拍，如 3 或 4")
    parser.add_argument("--force", action="store_true", help="覆盖同名曲谱，而不是另存为「名字 (2).txt」")
    args = parser.parse_args(argv)
    if args.output and len(args.midi) > 1:
        parser.error("一次给多个 MIDI 文件时不能用 -o")

    failed = 0
    for path in args.midi:
        print(f"== {path.name}")
        try:
            midi = load_midi(path)
            result = arrange(midi, title=path.name, melody=_numbers_argument(args.melody),
                             drop=_numbers_argument(args.drop), transpose=args.transpose,
                             accompaniment="bass" if args.bass_line else "chords",
                             beats=args.beats, meter=args.meter)
        except (OSError, MidiError, ArrangeError) as error:
            print(f"   无法编配：{error}")
            failed += 1
            continue
        print(_part_table(result.parts))
        moved = f"{'升' if result.shift > 0 else '降'} {abs(result.shift)} 个半音" if result.shift else "不移调"
        print(f"   整体{moved}，{result.white_share:.0%} 的音落在白键上；"
              f"{'按原谱的拍点' if result.exact else '已按拍子量化'}；共 {result.bars} 小节")
        if result.timing:
            print(f"   {describe_timing(result.timing)}")
        if result.changes:
            print(f"   {len(result.changes)} 个黑键音改到了相邻白键，曲谱里对应小节末尾有注释")
        if result.strays:
            print(f"   {result.strays} 个最高音比周围旋律低一个八度以上（多半是左手），交给了伴奏")
        if result.folded:
            print(f"   {result.folded} 个旋律音超出音域，移了八度")
        if args.list:
            continue
        target = args.output or SCORES_DIR / f"{path.stem}.txt"
        if not args.force:
            target = _free_path(target)
        save_score(result, target)
        print(f"   已保存到 {target}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
