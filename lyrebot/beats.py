"""Find the beat in notes that were played or transcribed without one.

A MIDI file transcribed from audio (MuScriptor writes them this way) keeps
each note at its real time but writes it against a made-up tempo, with no
time signature, so its beats and bars mean nothing.  This module finds the
music's own beat from the notes alone:

1. Tempo: how often pairs of onsets lie a given time apart, favouring tempos
   near 100 BPM (the range lyre scores are written in) and tempos whose
   beat the notes divide plainly rather than in triplets.
2. Pulse: the eighth notes of that beat, followed by dynamic programming over
   an onset envelope (Ellis 2007), twice: the second pass measures each step
   against the local tempo the first found, so tempo changes are followed.
   When the pulse turns out steady, one tempo is fitted through it instead.
3. Meter: how the eighths group, two to a beat (2/4, 3/4, 4/4) or three
   (6/8, 12/8), and how many beats make a bar, from where bass notes, long
   notes and chord changes come round again.
4. The notes are rewritten against that pulse as an ordinary MIDI file with
   a tempo map and a time signature, for the arranger to read as usual.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
import math

from .midi import DEFAULT_TEMPO, MidiFile

FPS = 50  # onset envelope frames per second
SLOWEST, FASTEST = 40.0, 220.0  # BPM of the quarter-note beat
PREFERRED = 100.0  # BPM the tempo choice leans towards
TIGHTNESS = 100.0  # how strongly the pulse keeps its tempo
STEADY = 0.04  # pulse intervals varying less than this (relative) count as one tempo
MEND = 6.0  # what adding or dropping a pulse costs, in standardized accents
PPQ = 960


@dataclass(frozen=True)
class Timing:
    """What was found: the quarter-note tempo, how steady it is, and the bar."""
    bpm: float
    steady: bool
    numerator: int
    denominator: int

    @property
    def signature(self) -> str:
        return f"{self.numerator}/{self.denominator}"


def _seconds_of(midi: MidiFile):
    """Tick -> seconds under the file's own tempo map."""
    points = sorted({0: DEFAULT_TEMPO, **dict(midi.tempos)}.items())
    ticks = [tick for tick, _ in points]
    elapsed = [0.0]
    for (tick, tempo), (next_tick, _) in zip(points, points[1:]):
        elapsed.append(elapsed[-1] + (next_tick - tick) * tempo / 1e6 / midi.ticks_per_beat)

    def seconds(tick: int) -> float:
        index = bisect_right(ticks, tick) - 1
        return elapsed[index] + (tick - ticks[index]) * points[index][1] / 1e6 / midi.ticks_per_beat

    return seconds


def _accent(note: tuple[float, float, int], middle: int) -> float:
    """How much a note marks a beat: every note counts, a bass note and a long
    note more, since off-beat accompaniment is usually short and high."""
    start, end, pitch = note
    return 1.0 + 0.5 * (pitch < middle) + min(end - start, 1.0)


def _onsets(notes: list[tuple[float, float, int]]) -> tuple[list[float], list[float]]:
    """Distinct onset times (notes within 30 ms merged) and their weight."""
    middle = sorted(pitch for _, _, pitch in notes)[len(notes) // 2]
    times: list[float] = []
    weights: list[float] = []
    for note in sorted(notes):
        if times and note[0] - times[-1] < 0.03:
            weights[-1] += _accent(note, middle)
        else:
            times.append(note[0])
            weights.append(_accent(note, middle))
    return times, [math.log1p(weight) for weight in weights]


def _duple_fit(times: list[float], weights: list[float], period: float) -> float:
    """How much of the music moves in plain subdivisions of this beat.

    Gaps between nearby onsets should be whole sixteenths of the beat.  A beat
    that is really two thirds or four thirds of the right one needs triplets
    to explain ordinary eighths, and scores badly here.
    """
    sixteenth = period / 4
    # Allow at least 50 ms of timing slop, but never a third of a sixteenth (a triplet).
    tolerance = min(0.3, max(0.2, 0.05 / sixteenth))
    good = total = 0.0
    for index in range(len(times) - 1):
        for other in (index + 1, index + 2):
            if other >= len(times):
                break
            gap = times[other] - times[index]
            if gap > 2 * period:
                break
            weight = weights[index] * weights[other]
            ratio = gap / sixteenth
            total += weight
            if abs(ratio - round(ratio)) < tolerance:
                good += weight
    return good / total if total else 0.0


def _tempo(times: list[float], weights: list[float]) -> float | None:
    """The quarter-note period in seconds: a peak of the histogram of onset
    distances, weighed by the preferred tempo and by how plainly the notes
    divide the beat."""
    shortest, longest = 60.0 / FASTEST, 60.0 / SLOWEST
    bins = int(longest * FPS) + 3
    histogram = [0.0] * bins
    for index, (time, weight) in enumerate(zip(times, weights)):
        for other in range(index + 1, len(times)):
            gap = times[other] - time
            if gap > longest + 0.05:
                break
            position = gap * FPS
            low = int(position)
            if low + 1 < bins:  # split between the two nearest bins
                histogram[low] += weight * weights[other] * (low + 1 - position)
                histogram[low + 1] += weight * weights[other] * (position - low)
    smooth = [sum(histogram[min(max(index + offset, 0), bins - 1)] * factor
                  for offset, factor in ((-1, 0.5), (0, 1.0), (1, 0.5)))
              for index in range(bins)]
    best, period = 0.0, None
    for index in range(max(1, int(shortest * FPS)), min(bins - 1, int(longest * FPS) + 1)):
        if smooth[index] < smooth[index - 1] or smooth[index] < smooth[index + 1]:
            continue  # only peaks
        # Refine the peak between bins, then weigh it.
        left, middle, right = smooth[index - 1], smooth[index], smooth[index + 1]
        curve = left - 2 * middle + right
        shift = 0.5 * (left - right) / curve if curve < 0 else 0.0
        seconds = (index + shift) / FPS
        prior = math.exp(-0.5 * (math.log2(60.0 / seconds / PREFERRED) / 0.8) ** 2)
        score = middle * prior * _duple_fit(times, weights, seconds) ** 2
        if score > best:
            best, period = score, seconds
    return period


def _envelope(times: list[float], weights: list[float], frames: int) -> list[float]:
    envelope = [0.0] * frames
    for time, weight in zip(times, weights):
        frame = round(time * FPS)
        for offset, factor in ((-2, 0.14), (-1, 0.61), (0, 1.0), (1, 0.61), (2, 0.14)):
            if 0 <= frame + offset < frames:
                envelope[frame + offset] += weight * factor
    mean = sum(envelope) / frames
    spread = math.sqrt(sum((value - mean) ** 2 for value in envelope) / frames) or 1.0
    return [value / spread for value in envelope]


def _local_steps(pulses: list[float], frames: int) -> list[float]:
    """The pulse length around every frame, in frames: the median of the nine
    gaps nearest it, so a lost or doubled pulse does not throw it."""
    gaps = [later - earlier for earlier, later in zip(pulses, pulses[1:])]
    local = []
    for index in range(len(gaps)):
        window = sorted(gaps[max(0, index - 4):index + 5])
        local.append(window[len(window) // 2])
    steps = []
    for frame in range(frames):
        index = min(max(bisect_right(pulses, frame / FPS) - 1, 0), len(local) - 1)
        steps.append(local[index] * FPS)
    return steps


def _track(times: list[float], weights: list[float], period: float, length: float,
           steps: list[float] | None = None) -> list[float]:
    """Pulse times following the onsets, by dynamic programming.  Each step is
    measured against ``steps`` (the local pulse length) when given."""
    frames = int(length * FPS) + 2
    envelope = _envelope(times, weights, frames)
    steps = steps or [period * FPS] * frames
    logs = [0.0] + [math.log(gap) for gap in range(1, int(max(steps) * 2) + 3)]
    score = [0.0] * frames
    back = [-1] * frames
    for frame in range(frames):
        step = steps[frame]
        center = math.log(step)
        best, before = 0.0, -1
        for earlier in range(frame - max(1, round(step / 2)), max(frame - max(2, round(step * 2)), 0) - 1, -1):
            value = score[earlier] - TIGHTNESS * (logs[frame - earlier] - center) ** 2
            if before < 0 or value > best:
                best, before = value, earlier
        score[frame] = envelope[frame] + (best if before >= 0 else 0.0)
        back[frame] = before
    last = max(range(max(0, frames - round(steps[-1]) - 1), frames), key=lambda frame: score[frame])
    chain = [last]
    while back[chain[-1]] >= 0:
        chain.append(back[chain[-1]])
    pulses = [frame / FPS for frame in reversed(chain)]
    # Snap each pulse onto an onset that lies within 40 ms of it.
    refined = []
    for pulse in pulses:
        index = bisect_right(times, pulse)
        near = [times[i] for i in (index - 1, index) if 0 <= i < len(times) and abs(times[i] - pulse) <= 0.04]
        refined.append(min(near, key=lambda time: abs(time - pulse)) if near else pulse)
    return refined


def _strengths(pulses: list[float], notes: list[tuple[float, float, int]], reach: float) -> list[float]:
    """The accent of the notes starting at each pulse (within ``reach`` of a gap)."""
    middle = sorted(pitch for _, _, pitch in notes)[len(notes) // 2]
    starts = [start for start, _, _ in notes]
    found = []
    for index, pulse in enumerate(pulses):
        gap = (pulses[index + 1] - pulse) if index + 1 < len(pulses) else (pulse - pulses[index - 1])
        first, last = bisect_right(starts, pulse - reach * gap), bisect_right(starts, pulse + reach * gap)
        found.append(sum(_accent(note, middle) for note in notes[first:last]))
    return found


def _on_the_pulse(pulses: list[float], notes: list[tuple[float, float, int]]) -> list[float]:
    """The pulses, or the points halfway between them if those carry more of
    the accents, as when the tracker settled on the off-beats."""
    halves = [(earlier + later) / 2 for earlier, later in zip(pulses, pulses[1:])]
    halves.append(halves[-1] + (pulses[-1] - pulses[-2]))
    if sum(_strengths(halves, notes, 0.2)) > 1.15 * sum(_strengths(pulses, notes, 0.2)):
        return halves
    return pulses


def _steady(pulses: list[float]) -> list[float] | None:
    """One tempo fitted through the pulses, if they keep one."""
    gaps = sorted(later - earlier for earlier, later in zip(pulses, pulses[1:]))
    if len(gaps) < 4:
        return None
    middle = gaps[len(gaps) // 2]
    deviation = sorted(abs(gap - middle) for gap in gaps)[len(gaps) // 2]
    if deviation > STEADY * middle:
        return None
    kept = list(enumerate(pulses))
    for _ in range(3):  # least squares, dropping pulses far off the line
        count = len(kept)
        mean_index = sum(index for index, _ in kept) / count
        mean_time = sum(time for _, time in kept) / count
        slope = (sum((index - mean_index) * (time - mean_time) for index, time in kept)
                 / sum((index - mean_index) ** 2 for index, _ in kept))
        start = mean_time - slope * mean_index
        kept = [(index, time) for index, time in kept if abs(start + slope * index - time) < 0.15 * slope] or kept
    # A ritardando or a pause leaves a stretch of pulses off the line: then the tempo is not one.
    residuals = sorted(abs(start + slope * index - time) for index, time in enumerate(pulses))
    if residuals[int(0.97 * (len(residuals) - 1))] > 0.15 * slope:
        return None
    return [start + slope * index for index in range(len(pulses))]


def _standard(values: list[float]) -> list[float]:
    mean = sum(values) / len(values)
    spread = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)) or 1.0
    return [(value - mean) / spread for value in values]


def _align(accent: list[float], span: int, group: int, mend: bool) -> list[int]:
    """The place in the bar of every pulse, by a Viterbi pass.

    Downbeats should carry strong accents, the other beats some, the eighths
    between them little.  Normally each pulse moves one eighth on; with
    ``mend``, a pulse may also stand still (the tracker put in one too many)
    or move two (it lost one), at a cost only a few bars of accents in the
    new place can pay.
    """
    def fit(place: int, value: float) -> float:
        return value * (1.0 if place == 0 else 0.3 if place % group == 0 else -0.3)

    steps = ((1, 0.0), (0, MEND), (2, MEND)) if mend else ((1, 0.0),)
    places = range(span)
    cost = [-fit(place, accent[0]) for place in places]
    back: list[list[int]] = []
    for value in accent[1:]:
        came, new = [], []
        for place in places:
            before, extra = min((((place - step) % span, price) for step, price in steps),
                                key=lambda option: cost[option[0]] + option[1])
            came.append(before)
            new.append(cost[before] + extra - fit(place, value))
        back.append(came)
        cost = new
    place = min(places, key=lambda candidate: cost[candidate])
    path = [place]
    for came in reversed(back):
        place = came[place]
        path.append(place)
    return path[::-1]


def _meter(pulses: list[float], notes: list[tuple[float, float, int]],
           meter: int | None = None, mend: bool = True) -> tuple[int, int, int, list[float]]:
    """(eighths per beat, beats per bar, index of the first downbeat, pulses).

    Each eighth gets an accent from the notes starting on it (bass and long
    notes count more) and from how much the harmony changes there.  The bar
    is the span after which that pattern repeats best: six eighths (3/4 or
    6/8) or eight (4/4).  Six eighths are 6/8 when the accents recur every
    three eighths more than every two.  Where in the bar each pulse falls
    comes from ``_align``, which also mends a pulse the tracker added or
    lost; the pulses come back with that mended.
    """
    starts = [start for start, _, _ in notes]
    change = []
    previous = [0.0] * 12
    for index, pulse in enumerate(pulses):
        end = pulses[index + 1] if index + 1 < len(pulses) else pulse + (pulse - pulses[index - 1])
        profile = [0.0] * 12
        for start, stop, pitch in notes[bisect_right(starts, pulse - 8.0):bisect_right(starts, end)]:
            overlap = min(stop, end) - max(start, pulse)
            if overlap > 0:
                profile[pitch % 12] += overlap
        norms = math.sqrt(sum(value * value for value in profile)) * math.sqrt(sum(value * value for value in previous))
        change.append(1.0 - sum(a * b for a, b in zip(profile, previous)) / norms if norms else 0.0)
        if any(profile):
            previous = profile
    accent = [a + b for a, b in zip(_standard(_strengths(pulses, notes, 0.25)), _standard(change))]

    def repeats(lag: int) -> float:
        pairs = len(accent) - lag
        return sum(accent[index] * accent[index + lag] for index in range(pairs)) / pairs if pairs > 0 else -math.inf

    if meter:
        span = 2 * meter
    else:
        span = 6 if repeats(6) + repeats(12) > repeats(8) + repeats(16) else 8
    group = 3 if span == 6 and not meter and repeats(3) > repeats(2) else 2
    places = _align(accent, span, group, mend)
    mended = [pulses[0]]
    for index in range(1, len(pulses)):
        step = (places[index] - places[index - 1]) % span
        if step == 0:
            continue  # one pulse too many
        if step == 2:
            mended.append((pulses[index - 1] + pulses[index]) / 2)  # one pulse lost
        mended.append(pulses[index])
    return group, span // group, (span - places[0]) % span, mended


def find_beats(midi: MidiFile, meter: int | None = None) -> tuple[MidiFile, Timing] | None:
    """The file rewritten against the beat found in its notes, or None when
    there are too few notes to tell.  ``meter`` fixes the quarter-note beats
    per bar."""
    seconds = _seconds_of(midi)
    pitched = [note for note in midi.notes if note.channel != 9]
    if len(pitched) < 8:
        return None
    notes = sorted((seconds(note.start), seconds(max(note.end, note.start)), note.pitch) for note in pitched)
    times, weights = _onsets(notes)
    period = _tempo(times, weights)
    if period is None:
        return None
    length = notes[-1][1] + period
    pulses = _track(times, weights, period / 2, length)
    if len(pulses) < 8:
        return None
    pulses = _track(times, weights, period / 2, length, _local_steps(pulses, int(length * FPS) + 2))
    if len(pulses) < 8:
        return None
    pulses = _on_the_pulse(pulses, notes)
    fitted = _steady(pulses)
    steady = fitted is not None
    pulses = fitted or pulses
    group, per_bar, phase, pulses = _meter(pulses, notes, meter, mend=not steady)

    first_gap, last_gap = pulses[1] - pulses[0], pulses[-1] - pulses[-2]

    def position(time: float) -> float:
        """In eighths from the first pulse."""
        if time < pulses[0]:
            return (time - pulses[0]) / first_gap
        if time >= pulses[-1]:
            return len(pulses) - 1 + (time - pulses[-1]) / last_gap
        index = bisect_right(pulses, time) - 1
        return index + (time - pulses[index]) / (pulses[index + 1] - pulses[index])

    span = group * per_bar
    offset = (span - phase) % span  # eighths before the first pulse, so bars start on downbeats
    while position(notes[0][0]) + offset < 0:
        offset += span

    def tick(time: float) -> int:
        return round((position(time) + offset) * PPQ / 2)

    # The tempo of a quarter note is that of two eighths.
    tempos = [(0, round(2 * first_gap * 1e6))]
    for index in range(len(pulses) - 1):
        tempo = round(2 * (pulses[index + 1] - pulses[index]) * 1e6)
        if tempo != tempos[-1][1]:
            tempos.append((round((index + offset) * PPQ / 2), tempo))
    rewritten = [replace(note, start=tick(seconds(note.start)), end=tick(seconds(max(note.end, note.start))))
                 for note in midi.notes]
    rewritten.sort(key=lambda note: (note.start, note.pitch, note.track, note.channel))
    signature = (group * per_bar, 8) if group == 3 else (per_bar, 4)
    gaps = sorted(later - earlier for earlier, later in zip(pulses, pulses[1:]))
    timing = Timing(30.0 / gaps[len(gaps) // 2], steady, *signature)
    return (MidiFile(PPQ, tuple(rewritten), tuple(tempos), ((0, *signature),), midi.track_names, midi.programs),
            timing)
