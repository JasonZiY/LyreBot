"""Write key presses down as a LyreBot score: playing, the other way round.

Presses come as (seconds, key), from the keyboard (see ``listen.py``) or from
anything else.  What counts is that the score, played back, presses the same
keys at the same moments; voices and the written tempo are only a means.

1. Presses close together are one onset: a chord, a roll or grace notes.
2. The beat is found in those onsets, as for a recording (``beats.py``).
3. Onsets snap to sixteenths or triplets of that beat (``arrange.Grid``);
   one that lands audibly off where it was pressed takes a finer
   subdivision.  Onsets that land on the same place become one.
4. Inside an onset, presses a few milliseconds apart are a chord; presses
   further apart are written as a roll (``^``) or grace notes (``'``), whose
   spacing is milliseconds, so it comes back as played at any tempo.
5. Speeds: a clock is fitted to when the onsets were pressed, and the
   fewest pieces of constant tempo follow it (``arrange.speed_lines``).
6. The score is played back against the presses.  The beat is read in a
   few ways, more or less ready to call it steady, and the reading that
   plays back most faithfully is kept; if that is still short, speeds may
   also change inside a bar.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
import math
from statistics import median
from typing import Iterable

from .arrange import KEY_OF, Grid, _bars, _bpm_text, describe_timing, duration_text, speed_lines
from . import beats
from .beats import PPQ, Timing, find_beats
from .midi import MidiFile, Note
from .score import DEFAULT_GRACE_MS, DEFAULT_ROLL_MS, KEYS, ScoreError, parse_score
from .timeline import compile_timeline

PITCH_OF = {key: pitch for pitch, key in KEY_OF.items()}
CLUSTER = 0.05  # seconds: a press this soon after the one before belongs to the same onset
SPAN = 0.25  # seconds an onset may take at most, so a fast run is not one long ornament
CHORD = 0.02  # seconds: presses this close are struck together
STEADY = 0.1  # a pulse wobbling less than this (relative) may be one tempo: hands jitter more than recordings
MATCH = 0.05  # seconds within which a press played back counts as faithful
SPLIT_BELOW = 0.99  # a score less faithful than this also tries changing speed inside bars
DRIFT = 0.02  # seconds the written speeds may let a beat run ahead of or behind the playing
REACH = 2.0  # beats either side of a beat whose onsets set its time
ONE_SIDED = 2.0  # how much better a one-sided fit must be to win over a centred one
REFINE = 0.035  # seconds an onset may be written off from where it was pressed before it moves
SUBDIVISIONS = (1, 2, 4, 3, 6, 8, 12, 16, 24)  # of a beat, coarsest first
SOURCE_TICKS = 1000  # per quarter note at 120 BPM: half a millisecond
TITLE = "# 由键盘演奏记录"


class NotateError(ValueError):
    pass


@dataclass(frozen=True)
class Notation:
    text: str
    presses: int
    bars: int
    timing: Timing | None
    faithful: float  # share of the presses played back within MATCH seconds


def _clusters(presses: list[tuple[float, str]]) -> list[list[tuple[float, str]]]:
    clusters: list[list[tuple[float, str]]] = []
    for at, key in presses:
        last = clusters[-1] if clusters else None
        if (last and at - last[-1][0] <= CLUSTER and at - last[0][0] <= SPAN
                and key not in {other for _, other in last}):
            last.append((at, key))
        else:
            clusters.append([(at, key)])
    return clusters


def _positions(clusters: list[list[tuple[float, str]]],
               steady: float) -> tuple[MidiFile, list[Fraction], Timing | None]:
    """The beat position of every onset, and the MIDI file whose tempo map and
    bars they are measured against."""
    starts = [cluster[0][0] for cluster in clusters]
    notes = []
    for index, cluster in enumerate(clusters):
        end = starts[index + 1] if index + 1 < len(starts) else starts[index] + 0.5
        for _, key in cluster:  # the track is the onset's number, to find it again after rewriting
            notes.append(Note(round(starts[index] * 2 * SOURCE_TICKS), round(end * 2 * SOURCE_TICKS),
                              PITCH_OF[key], 64, index, 0))
    notes.sort(key=lambda note: (note.start, note.pitch, note.track))
    source = MidiFile(SOURCE_TICKS, tuple(notes), ((0, 500_000),), (), (), {})
    found = find_beats(source, steady=steady)
    if found:
        midi, timing = found
        where: dict[int, Fraction] = {}
        for note in midi.notes:
            where.setdefault(note.track, Fraction(note.start, midi.ticks_per_beat))
        return midi, [where[index] for index in range(len(clusters))], timing
    # Too few onsets to find a beat: write them at 120 BPM to the nearest 32nd.
    midi = MidiFile(PPQ, (), ((0, 500_000),), ((0, 4, 4),), (), {})
    return midi, [Fraction(round((start - starts[0]) * 16), 8) for start in starts], None


class _Fitted:
    """Seconds at a beat position, fitted to when the onsets were pressed.

    At every onset a line is fitted through the onsets within ``REACH``
    beats of it, nearer ones weighing more, so the clock follows the tempo of
    the playing but not the jitter of single presses.  (The tracked beat
    alone would follow both: it is pulled onto onsets near it.)  Between
    onsets the clock runs straight from one fitted point to the next, and
    always forwards.
    """

    def __init__(self, points: list[tuple[Fraction, float]]):
        self.beats = [float(beat) for beat, _ in points]
        self.times = [at for _, at in points]
        self.knots = sorted(set(self.beats))
        self.values: list[float] = []
        for beat in self.knots:
            value = self._fit(beat)
            if self.values:
                value = max(value, self.values[-1] + 0.06 * (beat - self.knots[len(self.values) - 1]))
            self.values.append(value)

    def seconds(self, beat: Fraction) -> float:
        at = float(beat)
        knots, values = self.knots, self.values
        if len(knots) == 1:
            return values[0] + 0.5 * (at - knots[0])
        index = min(max(bisect_right(knots, at) - 1, 0), len(knots) - 2)
        slope = (values[index + 1] - values[index]) / (knots[index + 1] - knots[index])
        return values[index] + slope * (at - knots[index])

    def _fit(self, beat: float) -> float:
        """The line through the onsets just before, around or just after the
        beat, whichever fits them best: a sudden change of tempo then bends
        the clock at the change instead of smearing it over both sides."""
        best: tuple[float, float] | None = None
        for before, after, bias in ((REACH, REACH, 1.0), (2 * REACH, 0.0, ONE_SIDED), (0.0, 2 * REACH, ONE_SIDED)):
            found = self._line(beat, before, after)
            if found and (best is None or found[1] * bias < best[1]):
                best = (found[0], found[1] * bias)
        if best is None:  # too few onsets near: widen until there are some
            reach = 2 * REACH
            while best is None:
                reach *= 2
                best = self._line(beat, reach, reach, wide=reach > 1e4)
        return best[0]

    def _line(self, beat: float, before: float, after: float, wide: bool = False) -> tuple[float, float] | None:
        """(seconds at ``beat``, mean squared residual) of a weighted line
        through the onsets from ``before`` beats before it to ``after`` after."""
        low, high = bisect_left(self.beats, beat - before - 1e-9), bisect_right(self.beats, beat + after + 1e-9)
        pairs = list(zip(self.beats[low:high], self.times[low:high]))
        if len({b for b, _ in pairs}) < (2 if wide else 3):
            if not wide or not pairs:
                return None
        reach = max(before, after)
        weights = [1 + reach - abs(at - beat) for at, _ in pairs]
        total = sum(weights)
        mean_beat = sum(w * b for w, (b, _) in zip(weights, pairs)) / total
        mean_time = sum(w * t for w, (_, t) in zip(weights, pairs)) / total
        spread = sum(w * (b - mean_beat) ** 2 for w, (b, _) in zip(weights, pairs))
        slope = (sum(w * (b - mean_beat) * (t - mean_time) for w, (b, t) in zip(weights, pairs)) / spread
                 if spread > 1e-9 else 0.5)
        slope = max(slope, 0.06)  # 1000 BPM at most
        # Two points always lie on a line: the residual counts only the rest.
        residual = (sum(w * (mean_time + slope * (b - mean_beat) - t) ** 2 for w, (b, t) in zip(weights, pairs))
                    / total * len(pairs) / max(len(pairs) - 2, 0.5))
        return mean_time + slope * (beat - mean_beat), residual

    def bpm(self, start: Fraction, length: Fraction) -> float:
        seconds = self.seconds(start + length) - self.seconds(start)
        return min(1000.0, max(1.0, 60.0 * float(length) / seconds)) if seconds > 0 else 120.0


def _refine(raw: list[Fraction], snapped: list[Fraction], times: list[float]) -> list[Fraction]:
    """Move onsets that the grid put audibly off where they were pressed.

    The grid holds a beat to sixteenths or to triplets; a 32nd, or a triplet
    in a beat of sixteenths, lands too far off.  Such an onset takes the
    coarsest subdivision (``SUBDIVISIONS``) of its beat that plays it within
    ``REFINE`` of when it was pressed, between its neighbours.
    """
    clock = _Fitted(list(zip(snapped, times)))
    moved = list(snapped)
    for index, (position, at) in enumerate(zip(raw, times)):
        if abs(clock.seconds(moved[index]) - at) <= REFINE:
            continue
        low = moved[index - 1] if index else Fraction(-10**9)
        high = snapped[index + 1] if index + 1 < len(snapped) else Fraction(10**9)
        options = [Fraction(round(position * steps), steps) for steps in SUBDIVISIONS]
        options = [option for option in options if low <= option <= high] or [moved[index]]
        fitting = [option for option in options if abs(clock.seconds(option) - at) <= REFINE]
        moved[index] = fitting[0] if fitting else min(options, key=lambda option: abs(clock.seconds(option) - at))
    return moved


def _ornament(presses: list[tuple[float, str]]) -> tuple[str, str | None, float]:
    """(text, kind, spacing in ms): a chord, a roll (``^``) of three or more
    single keys, or else grace notes (``'``)."""
    origin = presses[0][0]
    strikes: list[tuple[float, list[str]]] = []
    for at, key in presses:
        if strikes and at - strikes[-1][0] <= CHORD and key not in strikes[-1][1]:
            strikes[-1][1].append(key)
        else:
            strikes.append((at, [key]))
    keys = ["".join(sorted(group, key=PITCH_OF.get)) for _, group in strikes]
    if len(strikes) == 1:
        return keys[0], None, 0.0
    spacing = (strikes[-1][0] - origin) * 1000 / (len(strikes) - 1)
    if len(keys) > 2 and all(len(group) == 1 for group in keys) and len(set(keys)) == len(keys):
        return "^" + "".join(keys), "roll", spacing
    return "'".join(keys), "grace", spacing


def notate(presses: Iterable[tuple[float, str]], title: str = "") -> Notation:
    """The score that plays ``presses`` back: (seconds, key) pairs, keys from ``score.KEYS``.

    The beat can be read as one steady tempo more or less readily; both
    readings are written out and the one that plays the presses back more
    faithfully is kept (the strict one on a tie: its tempo follows the playing).
    """
    presses = sorted((float(at), key) for at, key in presses if key in KEYS)
    if not presses:
        raise NotateError("没有记下诗琴的按键")
    clusters = _clusters(presses)
    best: Notation | None = None
    for steady in (beats.STEADY, STEADY, 0.0):
        found = _write(presses, clusters, title, steady)
        if best is None or found.faithful > best.faithful + 0.005:
            best = found
    return best


def faithfulness(text: str, presses: list[tuple[float, str]]) -> float:
    """The share of ``presses`` that the score plays within ``MATCH`` seconds
    of when they were pressed, with the score's start lined up on the
    presses as well as it goes."""
    played: dict[str, list[float]] = defaultdict(list)
    strikes = []
    for event in compile_timeline(parse_score(text)).events:
        if event.down:
            for key in event.keys:
                played[key].append(event.at)
                strikes.append(event.at)
    wanted: dict[str, list[float]] = defaultdict(list)
    for at, key in presses:
        wanted[key].append(at)

    def matched(offset: float, until: float = math.inf) -> list[float]:
        found = []
        for key, times in wanted.items():
            unused = sorted(time - offset for time in played.get(key, ()))
            for at in times:
                if at > until:
                    break
                index = bisect_left(unused, at)
                near = [i for i in (index - 1, index) if 0 <= i < len(unused)]
                if near:
                    pick = min(near, key=lambda i: abs(unused[i] - at))
                    if abs(unused[pick] - at) <= MATCH:
                        found.append(unused.pop(pick) - at)
        return found

    # The offset that lines up the most of the first few seconds, then refined on everything.
    strikes.sort()
    until = presses[min(len(presses) - 1, 60)][0]
    candidates = {round(strike - at, 3) for at, _ in presses[:8] for strike in strikes[:16]}
    offset = max(sorted(candidates), key=lambda candidate: len(matched(candidate, until)))
    errors = matched(offset)
    if errors:
        errors = matched(offset + median(errors))
    return len(errors) / len(presses)


def _write(presses: list[tuple[float, str]], clusters: list[list[tuple[float, str]]], title: str,
           steady: float) -> Notation:
    midi, positions, timing = _positions(clusters, steady)
    starts_at = [cluster[0][0] for cluster in clusters]
    if timing:
        grid = Grid(sorted(positions), Fraction(1, midi.ticks_per_beat))
        snapped = [grid.onset(position) for position in positions]
        for _ in range(2):
            snapped = _refine(positions, snapped, starts_at)
        positions = snapped
    # Onsets that snap to the same place are one, ornamented by its later presses.
    events: list[tuple[Fraction, list[tuple[float, str]]]] = []
    for position, cluster in zip(positions, clusters):
        if events and events[-1][0] == position:
            events[-1][1].extend(cluster)
        else:
            events.append((position, list(cluster)))

    first = events[0][0]
    bars = _bars(midi, events[-1][0] + Fraction(1, 4))
    starts = [start for start, _ in bars]
    first_bar = bisect_right(starts, first) - 1
    clock = _Fitted([(position, group[0][0]) for position, group in events])
    written = [(position, *_ornament(group)) for position, group in events]
    header = [f"{TITLE}：{title}" if title else TITLE]
    if timing:
        header.append(f"# {describe_timing(timing).replace('从音符', '从按键')}")
    signature = midi.time_signatures[0] if midi.time_signatures else (0, 4, 4)
    header.append(f"# 拍号 {signature[1]}/{signature[2]}；共 {len(presses)} 次按键")
    text = _text(header, written, bars[first_bar:], clock, split=False)
    faithful = faithfulness(text, presses)
    if faithful < SPLIT_BELOW:  # perhaps the tempo changes inside a bar
        split = _text(header, written, bars[first_bar:], clock, split=True)
        again = faithfulness(split, presses)
        if again > faithful + 0.005:
            text, faithful = split, again
    return Notation(text, len(presses), len(bars) - first_bar, timing, faithful)


def _text(header: list[str], written: list[tuple[Fraction, str, str | None, float]],
          bars: list[tuple[Fraction, Fraction]], clock: _Fitted, split: bool) -> str:
    """The score: one line per bar, with speeds set at bar lines.  With
    ``split`` a speed may change on any beat, and the bar is written as two
    lines there."""
    units: list[tuple[Fraction, Fraction]] = []
    owner: list[int] = []
    for number, (begin, length) in enumerate(bars):
        steps = [(begin, length)]
        if split:
            steps = [(begin + i, min(Fraction(1), length - i)) for i in range(math.ceil(length))]
        units += steps
        owner += [number] * len(steps)
    speeds = speed_lines(units, 0, clock, DRIFT)
    segments: list[tuple[Fraction, Fraction, float | None]] = []  # (begin, end, speed set before it)
    bpm = None
    for index, (begin, length) in enumerate(units):
        change = speeds[index] if index in speeds and speeds[index] != bpm else None
        if change is not None:
            bpm = change
        if segments and owner[index] == owner[index - 1] and change is None:
            segments[-1] = (segments[-1][0], begin + length, segments[-1][2])
        else:
            segments.append((begin, begin + length, change))
    total = units[-1][0] + units[-1][1]

    lines = list(header)
    settings = {"roll": DEFAULT_ROLL_MS, "grace": DEFAULT_GRACE_MS}
    cursor = 0
    for begin, end, change in segments:
        if change is not None:
            lines.append(f"speed={_bpm_text(change)}")
        inside = []
        while cursor < len(written) and written[cursor][0] < end:
            inside.append(cursor)
            cursor += 1
        for kind in settings:
            spacings = [written[item][3] for item in inside if written[item][2] == kind]
            if spacings:
                value = min(1000, max(1, round(median(spacings))))
                if value != settings[kind]:
                    settings[kind] = value
                    lines.append(f"{kind}={value}")
        pieces = []
        opening = inside[0] if inside else cursor  # the first onset at or after this line
        if (written[opening][0] if opening < len(written) else total) > begin:
            lead = min(written[opening][0] if opening < len(written) else end, end) - begin
            pieces.append(("0" if opening == 0 else "") + duration_text(lead))  # rest, or the last note held on
        for item in inside:
            position, text = written[item][0], written[item][1]
            following = written[item + 1][0] if item + 1 < len(written) else total
            pieces.append(text + duration_text(min(following, end) - position))
        lines.append("".join(pieces))
    text = "\n".join(lines) + "\n"
    try:
        parse_score(text)
    except ScoreError as error:  # a bug here, not a problem with the presses
        raise NotateError(f"生成的曲谱无法解析：{error}") from error
    return text
