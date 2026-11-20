"""Write a LyreBot score out as 简谱 for the lyre, laid out like the usual
风物之诗琴简谱: an HTML page of A4 pages drawn in SVG, to read or print.

The lyre's 21 keys are the white keys of three octaves, so every score is
in 1=C: a key is its digit, with a dot above it for the high row and below
it for the low row.  Each part is a staff; a score played as one part (as
the keyboard writes them) is split into an upper and a lower hand.  Chords
stand in a column, highest on top.  A note held past a beat or a bar line
goes on in grey under a tie, as it is not struck again.  Rolls get a wavy
line, grace notes small digits before their note.

Scores LyreBot wrote from a recording or from the keyboard keep the sway
of the playing in their note values and tempo lines: that plays back as it
was played, but reads badly.  For reading, their notes move to the nearest
sixteenth or triplet (a note squeezed out becomes a grace note), and one
tempo is printed.  The score itself is not changed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from fractions import Fraction
from html import escape
import math
from pathlib import Path
import re
import sys

from .score import Event, Score, ScoreError, load_score

ROWS = ("zxcvbnm", "asdfghj", "qwertyu")
DIGIT = {key: (index + 1, row - 1) for row, keys in enumerate(ROWS) for index, key in enumerate(keys)}
PITCH = {key: row * 7 + index for row, keys in enumerate(ROWS) for index, key in enumerate(keys)}
SUBTITLE = "风物之诗琴简谱"
KINDS = {"recording": "LyreBot 由录音编配", "keyboard": "LyreBot 由键盘演奏记录", "midi": "LyreBot 由 MIDI 编配"}

# --- Reading the score into staves of bars ----------------------------------------------


@dataclass(frozen=True)
class Segment:
    """A stretch of one staff: a note, a chord or a rest."""
    start: Fraction
    end: Fraction
    keys: tuple[str, ...]  # low to high; () for a rest
    struck: bool = True  # False: it goes on from the note before (grey, under a tie)
    graces: tuple[tuple[str, ...], ...] = ()
    roll: int = 0  # 1 rolled upwards, -1 downwards


@dataclass
class Symbol:
    """One written note, chord or rest of a bar."""
    start: Fraction  # beats from the start of the bar
    length: Fraction  # beats it lasts
    keys: tuple[str, ...]
    struck: bool = True
    lines: int = 0  # underlines
    dot: bool = False  # augmentation dot
    dashes: int = 0  # dashes after it
    step: Fraction = Fraction(1)  # beats from one dash to the next
    tied: bool = False  # tied from the symbol before
    graces: tuple[tuple[str, ...], ...] = ()
    roll: int = 0
    tuplet: int | None = None  # index into the bar's tuplets
    x: float = 0.0

    def dash_times(self) -> list[Fraction]:
        return [self.start + self.step * (index + 1) for index in range(self.dashes)]


@dataclass
class Bar:
    number: int
    start: Fraction
    length: Fraction
    staves: list[list[Symbol]]
    tuplets: list[list[tuple[Fraction, Fraction]]]  # per staff: (start, end) spans of triplets
    meter: tuple[int, int] | None = None  # printed when the meter changes here
    tempo: str | None = None  # printed above the bar
    group: Fraction = Fraction(1)  # beats beamed together
    x: float = 0.0
    width: float = 0.0
    times: dict[Fraction, float] = field(default_factory=dict)  # beat in bar -> x from the bar's left


@dataclass
class Sheet:
    title: str
    meter: tuple[int, int]
    tempo: str  # ♩=95 or ♩≈176
    kind: str  # hand, midi, recording, keyboard
    notes: list[str]  # lines printed under the composer's corner
    bars: list[Bar]
    staves: int


def kind_of(text: str, name: str) -> str:
    first = text.removeprefix("﻿").lstrip().split("\n", 1)[0]
    if first.startswith("# 由键盘演奏记录"):
        return "keyboard"
    if first.startswith("# 由 MIDI 自动编配"):
        return "recording" if name.endswith("-录音") else "midi"
    return "hand"


def meter_of(text: str, score: Score) -> tuple[int, int]:
    """The meter a header comment names (拍号 3/4, or 6/8 拍), if the bars agree; else
    the one the most common bar length gives."""
    lengths = [end - start for start, end in zip(score.bars, (*score.bars[1:], score.total_beats))]
    common = max(set(lengths), key=lambda length: (lengths.count(length), length))
    header = "\n".join(line for line in text.splitlines()[:12] if line.lstrip().startswith("#"))
    for pattern in (r"拍号\s*(\d+)/(\d+)", r"(\d+)/(\d+)\s*拍"):
        found = re.search(pattern, header)
        if found:
            beats, unit = int(found.group(1)), int(found.group(2))
            if unit in (2, 4, 8, 16) and Fraction(beats * 4, unit) == common:
                return beats, unit
    return meter_for(common)


def meter_for(length: Fraction) -> tuple[int, int]:
    for unit in (4, 8, 16):
        beats = length * unit / 4
        if beats.denominator == 1:
            return int(beats), unit
    return int(length * 4), 4


def group_of(meter: tuple[int, int]) -> Fraction:
    """Beats beamed together: a quarter, or a dotted quarter in 6/8, 9/8 and 12/8."""
    beats, unit = meter
    return Fraction(3, 2) if unit == 8 and beats % 3 == 0 else Fraction(1)


def _main(event: Event) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...], int]:
    """An event's main keys (low to high), its grace notes, and which way it is rolled."""
    graces = tuple(tuple(sorted(strike.keys, key=PITCH.get)) for strike in event.strikes[:event.graces])
    main = event.strikes[event.graces:]
    keys = tuple(sorted({key for strike in main for key in strike.keys}, key=PITCH.get))
    roll = 0
    if event.rolled and len(main) >= 2:
        roll = -1 if PITCH[main[-1].keys[0]] < PITCH[main[-2].keys[0]] else 1
    return keys, graces, roll


def _segments(events: list[Event]) -> list[Segment]:
    found = []
    for event in sorted(events, key=lambda event: event.start):
        keys, graces, roll = _main(event)
        found.append(Segment(event.start, event.start + event.duration, keys, True, graces, roll))
    return found


def split_hands(events: list[Event], bars: tuple[Fraction, ...], total: Fraction) -> list[list[Segment]]:
    """Split one part into an upper and a lower hand.  The top key of a chord
    is the upper hand's; any other key, and a key played alone, goes to the
    hand whose last few notes lie nearer, the upper one unless the lower is
    clearly nearer.  A note lasts until that hand plays again, a rest or the
    bar line."""
    recent: list[list[int]] = [[PITCH["q"]], [PITCH["b"]]]

    def lower(pitch: int) -> bool:
        upper_at, lower_at = (sum(notes) / len(notes) for notes in recent)
        return abs(pitch - lower_at) + 1 < abs(pitch - upper_at)

    def played(hand: int, pitches: list[int]) -> None:
        recent[hand] = (recent[hand] + pitches)[-4:]

    onsets: list[list[tuple[Fraction, tuple[str, ...], tuple, int]]] = [[], []]
    stops = sorted({event.start for event in events if not event.strikes} | set(bars) | {total})
    for event in sorted(events, key=lambda event: event.start):
        if not event.strikes:
            continue
        keys, graces, roll = _main(event)
        if graces or roll:  # an ornament stays whole, with the hand nearer its middle
            hand = 1 if lower((PITCH[keys[0]] + PITCH[keys[-1]]) // 2) else 0
            onsets[hand].append((event.start, keys, graces, roll))
            played(hand, [PITCH[keys[-1]]])
            continue
        if len(keys) == 1:
            hands = ((1, keys),) if lower(PITCH[keys[0]]) else ((0, keys),)
        else:
            below = tuple(key for key in keys[:-1] if lower(PITCH[key]) or key != keys[-2])
            above = tuple(key for key in keys if key not in below)
            hands = ((0, above), (1, below))
        for hand, chosen in hands:
            if chosen:
                onsets[hand].append((event.start, chosen, (), 0))
                played(hand, [PITCH[key] for key in chosen])
    staves = []
    for hand in onsets:
        found: list[Segment] = []
        for index, (start, keys, graces, roll) in enumerate(hand):
            following = hand[index + 1][0] if index + 1 < len(hand) else total
            stop = min([following] + [at for at in stops if at > start])
            found.append(Segment(start, stop, keys, True, graces, roll))
        staves.append(_filled(found, total))
    return staves


def _filled(segments: list[Segment], total: Fraction) -> list[Segment]:
    """Rests in every gap."""
    found, at = [], Fraction(0)
    for segment in segments:
        if segment.start > at:
            found.append(Segment(at, segment.start, ()))
        found.append(segment)
        at = segment.end
    if at < total:
        found.append(Segment(at, total, ()))
    return found


# --- Moving notes to a readable grid -------------------------------------------------------

def _snap(x: Fraction, grids: dict[int, int]) -> Fraction:
    """x on its beat's grid (sixteenths or triplets), the coarser point when halfway."""
    beat = math.floor(x)
    steps = grids.get(beat, 4)
    offset = x - beat
    candidates = [Fraction(step, steps) for step in range(steps + 1)]
    return beat + min(candidates, key=lambda point: (abs(point - offset), point.denominator))


def _grid(offsets: list[Fraction]) -> int:
    """Triplets for a beat that already has more notes on its thirds than on
    its odd sixteenths (as the arranger writes them); sixteenths otherwise.
    A note between the two grids is the swing of the playing, not a triplet."""
    thirds = sum(1 for offset in offsets if (offset * 6).denominator == 1 and (offset * 4).denominator != 1)
    sixteenths = sum(1 for offset in offsets if (offset * 4).denominator == 1 and (offset * 2).denominator != 1)
    return 3 if thirds > sixteenths else 4


def moved(segments: list[Segment], move) -> tuple[list[Segment], int]:
    """Segments with every edge moved by ``move``, and how many notes it moved.
    A note squeezed to nothing leads the next note as a grace note, or takes
    the place of the rest or held note after it."""
    found: list[Segment] = []
    waiting: list[tuple[str, ...]] = []
    count = 0
    for segment in segments:
        start, end = move(segment.start), move(segment.end)
        struck = bool(segment.keys) and segment.struck
        if struck and start != segment.start:
            count += 1
        if start == end:
            if struck:
                waiting += [*segment.graces, segment.keys]
                count += start == segment.start
            continue
        if waiting:
            if struck:
                segment = replace(segment, graces=tuple(waiting) + segment.graces)
            else:
                segment = replace(segment, keys=waiting[-1], graces=tuple(waiting[:-1]), struck=True, roll=0)
            waiting = []
        found.append(replace(segment, start=start, end=end))
    return found, count


def quantize(staves: list[list[Segment]]) -> tuple[list[list[Segment]], int]:
    """Every staff on sixteenths or triplets of each beat (both staves alike),
    and how many notes moved."""
    points = {x for staff in staves for segment in staff for x in (segment.start, segment.end)}
    by_beat: dict[int, list[Fraction]] = {}
    for x in points:
        by_beat.setdefault(math.floor(x), []).append(x - math.floor(x))
    grids = {beat: _grid(offsets) for beat, offsets in by_beat.items()}
    result, total = [], 0
    for staff in staves:
        found, count = moved(staff, lambda x: _snap(x, grids))
        result.append(found)
        total += count
    return result, total


# --- Writing one bar ----------------------------------------------------------------------

LINES = {Fraction(1, 2 ** k): k for k in range(5)}
DOTTED = {Fraction(3, 2 ** (k + 1)): k for k in range(4)}


def _dyadic(x: Fraction) -> bool:
    return (x * 16).denominator == 1


def _tuplets(pieces: list[Segment], length: Fraction) -> list[tuple[Fraction, Fraction]]:
    """Spans of the bar that move in triplets: of the quarter beats, half
    beats, beats and two beats inside which every edge falls on a sixth, the
    one the fewest notes cross (one held out of a triplet is tied at its
    edge), then one split in plain thirds, then the one holding most of the
    triplet's edges, then the smallest."""
    edges = sorted({x for piece in pieces for x in (piece.start, piece.end)})
    spans: list[tuple[Fraction, Fraction]] = []
    for x in edges:
        if _dyadic(x) or any(a < x < b for a, b in spans):
            continue
        fits = []
        for size in (Fraction(1, 4), Fraction(1, 2), Fraction(1), Fraction(2)):
            starts = {math.floor(x / size) * size} if size <= 1 else {Fraction(math.floor(x)) - 1, Fraction(math.floor(x))}
            for a in sorted(starts):
                b = a + size
                if a < 0 or b > length or any(a2 < b and a < b2 for a2, b2 in spans):
                    continue
                inside = [y for y in edges if a < y < b]
                if all(((y - a) * 6 / size).denominator == 1 for y in inside):
                    crossed = sum(1 for p in pieces for edge in (a, b) if p.start < edge < p.end)
                    if crossed > 1:  # a triplet is entered or left by a held note, not both
                        continue
                    thirds = all(((y - a) * 3 / size).denominator == 1 for y in inside)
                    covered = sum(1 for y in inside if not _dyadic(y))
                    fits.append((crossed, not thirds, -covered, size, (a, b)))
        if fits:
            spans.append(min(fits)[-1])
    return sorted(spans)


def _cut(pieces: list[Segment], at: list[Fraction]) -> list[Segment]:
    """Pieces cut at these points; the part after a cut goes on from the part before."""
    found = []
    for piece in pieces:
        for x in sorted(set(at)):
            if piece.start < x < piece.end:
                found.append(replace(piece, end=x))
                piece = replace(piece, start=x, struck=False, graces=(), roll=0)
        found.append(piece)
    return found


def _loose(x: Fraction) -> Fraction:
    """An edge no triplet explains, written on the nearest sixth of a beat if
    it is that close, else on the nearest sixty-fourth note."""
    sixth = Fraction(round(x * 6), 6)
    return sixth if abs(sixth - x) <= Fraction(1, 16) else Fraction(round(x * 16), 16)


def _value(length: Fraction) -> tuple[int, bool, int] | None:
    """(underlines, dot, dashes) for a length written as one symbol, if it can be."""
    if length in LINES:
        return LINES[length], False, 0
    if length in DOTTED:
        return DOTTED[length], True, 0
    if length.denominator == 1 and length > 1:
        return 0, False, int(length) - 1
    return None


def _pieces(start: Fraction, length: Fraction, origin: Fraction) -> list[tuple[Fraction, Fraction]]:
    """Cut a length that no one symbol writes into ones that do, largest first,
    each on its own grid (from origin)."""
    found = []
    while length > 0:
        if _value(length) and length <= 1:
            found.append((start, length))
            break
        size = Fraction(1)
        while size > Fraction(1, 16) and (size > length or ((start - origin) / size).denominator != 1):
            size /= 2
        if size * 3 / 2 <= length and ((start - origin) / size).denominator == 1 and size * 3 / 2 in DOTTED:
            size = size * 3 / 2
        size = min(size, length)
        found.append((start, size))
        start, length = start + size, length - size
    return found


def notate_bar(pieces: list[Segment], length: Fraction, group: Fraction) -> tuple[list[Symbol], list[tuple]]:
    """The symbols one staff writes for a bar, and its triplet spans."""
    spans = _tuplets(pieces, length)
    loose = {x for piece in pieces for x in (piece.start, piece.end)
             if not _dyadic(x) and not any(a <= x <= b for a, b in spans)}
    if loose:  # rare (an ornament in a MIDI file): move the odd edges of those beats, then look again
        beats = {math.floor(x) for x in loose}
        pieces, _ = moved(pieces, lambda x: x if _dyadic(x) or math.floor(x) not in beats else _loose(x))
        spans = _tuplets(pieces, length)
        left = {x for piece in pieces for x in (piece.start, piece.end)
                if not _dyadic(x) and not any(a <= x <= b for a, b in spans)}
        if left:
            pieces, _ = moved(pieces, lambda x: Fraction(round(x * 16), 16) if x in left else x)
    pieces = _cut(pieces, [edge for span in spans for edge in span])
    symbols: list[Symbol] = []
    for piece in pieces:
        span = next((index for index, (a, b) in enumerate(spans) if a <= piece.start and piece.end <= b), None)
        parts = _tuplet_parts(piece, spans[span]) if span is not None else _plain_parts(piece, group)
        for index, (start, size, lines, dot, dashes, step) in enumerate(parts):
            first = index == 0
            symbols.append(Symbol(start, size, piece.keys, piece.struck and first, lines, dot, dashes, step,
                                  tied=bool(piece.keys) and not (piece.struck and first),
                                  graces=piece.graces if first and piece.struck else (),
                                  roll=piece.roll if first and piece.struck else 0, tuplet=span))
    return symbols, spans


def _plain_parts(piece: Segment, group: Fraction) -> list[tuple]:
    found = []
    a, b = piece.start, piece.end
    while a < b:
        length = b - a
        on_group = (a / group).denominator == 1
        if piece.keys:
            if on_group and length == Fraction(3, 2) and group == 1:
                found.append((a, length, 0, True, 0, Fraction(1)))
                break
            if (on_group or (group == 1 and a % 1 == Fraction(1, 2))) and length >= 1 and length.denominator == 1:
                found.append((a, length, 0, False, int(length) - 1, Fraction(1)))  # held, or a syncopation
                break
            if on_group and group == 1 and length > 1:
                whole = Fraction(math.floor(length))
                found.append((a, whole, 0, False, int(whole) - 1, Fraction(1)))
                a += whole
                continue
        end = min(b, (math.floor(a / group) + 1) * group)
        origin = math.floor(a / group) * group
        for start, size in _pieces(a, end - a, origin):
            lines, dot, dashes = _value(size) or (4, False, 0)
            found.append((start, size, lines, dot, dashes, Fraction(1)))
        a = end
    return found


def _tuplet_parts(piece: Segment, span: tuple[Fraction, Fraction]) -> list[tuple]:
    """Inside a triplet span the written values are 3/2 of the played ones."""
    origin = span[0]
    found = []
    a, length = (piece.start - origin) * Fraction(3, 2), (piece.end - piece.start) * Fraction(3, 2)
    if piece.keys and _value(length) and length.denominator == 1:
        written = [(a, length)]
    else:
        written = _pieces(a, length, Fraction(0))
    for start, size in written:
        lines, dot, dashes = _value(size) or (4, False, 0)
        found.append((origin + start * Fraction(2, 3), size * Fraction(2, 3), lines, dot, dashes, Fraction(2, 3)))
    return found


# --- The whole sheet ----------------------------------------------------------------------

def _tempo_text(bpm: float, target: float | None = None, about: bool = False) -> str:
    text = f"♩{'≈' if about else '='}{bpm:g}"
    return text + (f"→{target:g}" if target is not None else "")


def sheet_of(text: str, name: str) -> Sheet:
    """Read a score's text into a sheet of bars, ready to draw."""
    from .score import parse_score
    score = parse_score(text)
    kind = kind_of(text, name)
    meter = meter_of(text, score)
    group = group_of(meter)
    if score.parts == 1:
        staves = split_hands(list(score.events), score.bars, score.total_beats)
    else:
        staves = [_filled(_segments([e for e in score.events if e.part == part]), score.total_beats)
                  for part in range(score.parts)]
    notes = [KINDS[kind]] if kind in KINDS else []
    if kind in ("recording", "keyboard"):
        staves, moved = quantize(staves)
        if moved:
            notes.append("节奏为读谱整理过，演奏以曲谱为准")
    swaying = kind in ("recording", "keyboard") and len(score.tempo) > 1
    if swaying:
        weights: dict[float, Fraction] = {}
        marks = list(score.tempo)
        for mark, following in zip(marks, [*marks[1:], None]):
            end = following.beat if following else score.total_beats
            weights[mark.bpm] = weights.get(mark.bpm, Fraction(0)) + end - mark.beat
        ordered = sorted(weights)
        half, running, middle = sum(weights.values()) / 2, Fraction(0), ordered[0]
        for bpm in ordered:
            running += weights[bpm]
            if running >= half:
                middle = bpm
                break
        tempo = _tempo_text(round(middle), about=True)
        notes.append("速度随演奏起伏")
    else:
        first = score.tempo[0]
        tempo = _tempo_text(first.bpm, first.target)
    marks_at = {} if swaying else {mark.beat: _tempo_text(mark.bpm, mark.target) for mark in score.tempo[1:]}

    ends = (*score.bars[1:], score.total_beats)
    bars: list[Bar] = []
    current = Fraction(meter[0] * 4, meter[1])
    for number, (start, end) in enumerate(zip(score.bars, ends), 1):
        length = end - start
        shown = None
        last = number == len(score.bars)
        if length != current and number > 1 and not last:
            shown, current = meter_for(length) if length != Fraction(meter[0] * 4, meter[1]) else meter, length
        symbols, spans = [], []
        for staff in staves:
            pieces = [Segment(max(s.start, start) - start, min(s.end, end) - start, s.keys,
                              s.struck and s.start >= start, s.graces if s.start >= start else (),
                              s.roll if s.start >= start else 0)
                      for s in staff if s.start < end and s.end > start]
            written, found = notate_bar(pieces, length, group)
            symbols.append(written)
            spans.append(found)
        bars.append(Bar(number, start, length, symbols, spans, shown, marks_at.get(start), group))
    title = re.sub(r"-(录音|演奏|MIDI)$", "", name) if kind != "hand" else name  # the corner says where it came from
    return Sheet(title, meter, tempo, kind, notes, bars, len(staves))


# --- Drawing ------------------------------------------------------------------------------

PAGE_W, PAGE_H = 1000, 1414
LEFT, RIGHT = 96, 930  # where systems start (after the brace) and end
UNIT = 40  # width of a quarter beat before justifying
DIGIT_W = 12
STACK = 25  # from one note of a chord to the next
CAP = 15  # digit height
FONT = "Arial, 'Helvetica Neue', Helvetica, sans-serif"
CJK = "'Microsoft YaHei', 'PingFang SC', 'Noto Sans CJK SC', 'Source Han Sans SC', sans-serif"
GREY = "#a3a3a3"


def _extent(symbol: Symbol) -> tuple[float, float]:
    """Room a symbol needs left and right of its time point."""
    left = DIGIT_W / 2 + 9 * len(symbol.graces) + (9 if symbol.roll else 0)
    right = DIGIT_W / 2 + (7 if symbol.dot else 0)
    return left, right


def _bar_points(bar: Bar) -> list[tuple[Fraction, float, float, bool]]:
    """Time points of a bar: (beat, room left, room right, beamed with the point before)."""
    room: dict[Fraction, list[float]] = {}
    beamed: dict[Fraction, bool] = {}
    for staff in bar.staves:
        previous = None
        for symbol in staff:
            left, right = _extent(symbol)
            entry = room.setdefault(symbol.start, [0.0, 0.0])
            entry[0], entry[1] = max(entry[0], left), max(entry[1], right)
            together = previous is not None and previous.lines and symbol.lines and \
                _beam_key(previous, bar) == _beam_key(symbol, bar)
            beamed[symbol.start] = beamed.get(symbol.start, True) and bool(together)
            for at in symbol.dash_times():
                entry = room.setdefault(at, [0.0, 0.0])
                entry[0], entry[1] = max(entry[0], 6), max(entry[1], 6)
                beamed[at] = False
            previous = symbol
    return [(at, left, right, beamed.get(at, False)) for at, (left, right) in sorted(room.items())]


def _beam_key(symbol: Symbol, bar: Bar) -> tuple:
    if symbol.tuplet is not None:
        return ("t", symbol.tuplet)
    return ("g", math.floor(symbol.start / bar.group))


def _natural(points, length: Fraction) -> tuple[list[float], list[Fraction]]:
    """Gaps between a bar's points (and the bar lines), and the time each spans."""
    gaps, spans = [], []
    previous_at, previous_right = Fraction(0), 14.0  # room after the bar line
    for index, (at, left, right, beamed) in enumerate(points):
        pad = 3 if beamed else 7
        if index == 0:
            gaps.append(max(previous_right + left, UNIT * float(at - previous_at)))
        else:
            gaps.append(max(previous_right + left + pad, UNIT * float(at - previous_at)))
        spans.append(at - previous_at)
        previous_at, previous_right = at, right
    gaps.append(max(previous_right + 12, UNIT * float(length - previous_at)))
    spans.append(length - previous_at)
    return gaps, spans


def bar_width(bar: Bar) -> float:
    """How wide ``bar`` is laid out before being spread to a line."""
    return sum(_natural(_bar_points(bar), bar.length)[0])


def layout_rows(sheet: Sheet, left: float = LEFT, right: float = RIGHT, most: int | None = None) -> list[list[Bar]]:
    """Bars packed into systems (at most ``most``: four, or six short ones) and spread from ``left`` to
    ``right``."""
    natural = []
    for bar in sheet.bars:
        gaps, spans = _natural(_bar_points(bar), bar.length)
        natural.append((gaps, spans))
    width = right - left
    most = most or (6 if sheet.meter[0] * 4 / sheet.meter[1] <= 2 else 4)
    rows: list[list[int]] = []
    row: list[int] = []
    used = 0.0
    for index, (gaps, _) in enumerate(natural):
        size = sum(gaps)
        if row and (used + size > width or len(row) >= most):
            rows.append(row)
            row, used = [], 0.0
        row.append(index)
        used += size
    if row:
        rows.append(row)
    ratios = []
    systems = []
    for number, indexes in enumerate(rows):
        total = sum(sum(natural[i][0]) for i in indexes)
        target = width
        if number == len(rows) - 1 and len(rows) > 1 and len(indexes) < most:
            mean = sum(ratios) / len(ratios) if ratios else 1.0
            target = min(width, total * mean)
        ratios.append(target / total if total else 1.0)
        time = sum(float(sum(natural[i][1])) for i in indexes) or 1.0
        extra = max(0.0, target - total)
        x = float(left)
        for i in indexes:
            bar = sheet.bars[i]
            gaps, spans = natural[i]
            bar.x = x
            points = [at for at, *_ in _bar_points(bar)]
            at_x = 0.0
            bar.times = {}
            for gap, span, at in zip(gaps, spans, [*points, None]):
                at_x += gap + extra * float(span) / time
                if at is not None:
                    bar.times[at] = at_x
            bar.width = at_x
            x += at_x
        systems.append([sheet.bars[i] for i in indexes])
    return systems


class Canvas:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, item: str) -> None:
        self.items.append(item)

    # Each page sets the usual digit's font, size, anchor and colour once (see ROOT); an item names only
    # what differs, which keeps a long song's page a third of the size.
    def text(self, x: float, y: float, value: str, size: float, anchor: str = "middle", family: str = FONT,
             weight: str = "bold", fill: str = "#111", style: str = "") -> None:
        extra = "".join(f' {name}="{value}"' for name, value, usual in (
            ("font-size", f"{size:g}", "21"), ("text-anchor", anchor, "middle"), ("font-family", family, FONT),
            ("font-weight", weight, "bold"), ("fill", fill, "#111"), ("font-style", style, "")) if value != usual)
        self.add(f'<text x="{_n(x)}" y="{_n(y)}"{extra}>{escape(value)}</text>')

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 1.3, stroke: str = "#111") -> None:
        self.add(f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" stroke="{stroke}" '
                 f'stroke-width="{width:g}"/>')

    def dot(self, x: float, y: float, r: float = 1.9, fill: str = "#111") -> None:
        colour = f' fill="{fill}"' if fill != "#111" else ""
        self.add(f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{r:.2g}"{colour}/>')

    def path(self, d: str, width: float = 1.1, stroke: str = "#111") -> None:
        self.add(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{width:g}"/>')


def _n(value: float) -> str:
    """A coordinate to a tenth, without a trailing .0."""
    return f"{value:.1f}".removesuffix(".0")


ROOT = (f'font-family="{FONT}" font-size="21" font-weight="bold" text-anchor="middle" fill="#111" '
        'stroke-linecap="round"')


def _key(canvas: Canvas, x: float, baseline: float, key: str, size: float = 21, colour: str = "#111",
         low: float = 5.0) -> None:
    """A digit with its octave dot; ``low`` is how far below the baseline a low dot goes."""
    digit, octave = DIGIT[key]
    canvas.text(x, baseline, str(digit), size, fill=colour)
    scale = size / 21
    if octave > 0:
        canvas.dot(x, baseline - CAP * scale - 4.5 * scale, 1.9 * scale, colour)
    elif octave < 0:
        canvas.dot(x, baseline + low * scale, 1.9 * scale, colour)


def _stack_top(symbol: Symbol, baseline: float) -> float:
    count = max(1, len(symbol.keys))
    top = baseline - CAP - (count - 1) * STACK
    if symbol.keys and DIGIT[symbol.keys[-1]][1] > 0:
        top -= 8
    return top


@dataclass
class StaffBox:
    baseline: float
    top: float
    bottom: float
    lines: int


def _measure_staff(bars: list[Bar], staff: int, has_marks: bool) -> tuple[float, float, int]:
    """Room above and below a staff's baseline in one system."""
    stack, lines, low = 1, 0, False
    ties = False
    for bar in bars:
        for symbol in bar.staves[staff]:
            stack = max(stack, len(symbol.keys))
            lines = max(lines, symbol.lines)
            if symbol.keys and DIGIT[symbol.keys[0]][1] < 0:
                low = True
            ties = ties or symbol.tied or symbol.tuplet is not None or bool(symbol.graces)
    above = CAP + (stack - 1) * STACK + 8 + (12 if ties else 4) + (20 if has_marks else 0)
    below = 4 + 4 * lines + (6 if low else 2) + 4
    return above, below, lines


def _draw_bar_staff(canvas: Canvas, bar: Bar, staff: int, box: StaffBox, row: int,
                    previous: dict[int, tuple[int, Symbol]]) -> None:
    symbols = bar.staves[staff]
    baseline = box.baseline
    note = getattr(canvas, "note", None)  # a canvas may want to know which symbol each thing belongs to
    for symbol in symbols:
        x = bar.x + bar.times[symbol.start]
        symbol.x = x
        if note:
            note(bar, staff, symbol)
        colour = "#111" if (symbol.struck or not symbol.keys) else GREY
        if symbol.keys:
            for height, key in enumerate(symbol.keys):
                low = 4 + 4 * symbol.lines + 2.5 if height == 0 and symbol.lines else 5.0
                _key(canvas, x, baseline - height * STACK, key, colour=colour, low=low)
        else:
            canvas.text(x, baseline, "0", 21)
        if symbol.dot:
            canvas.dot(x + DIGIT_W / 2 + 4, baseline - 6, 2.0, colour)
        for at in symbol.dash_times():
            dx = bar.x + bar.times[at]
            canvas.line(dx - 5, baseline - 6, dx + 5, baseline - 6, 1.6, colour)
        for index, grace in enumerate(reversed(symbol.graces)):
            gx = x - DIGIT_W / 2 - 5 - index * 9 - (9 if symbol.roll else 0)
            for height, key in enumerate(grace):
                _key(canvas, gx, baseline - 8 - height * 12, key, size=12)
        if symbol.graces:
            gx = x - DIGIT_W / 2 - 5 - (9 if symbol.roll else 0)
            top = _stack_top(symbol, baseline)
            canvas.path(f"M{gx - 9 * (len(symbol.graces) - 1) - 3:.1f},{top - 2:.1f} "
                        f"Q{(gx + x) / 2:.1f},{top - 9:.1f} {x + 2:.1f},{top - 2:.1f}", 0.9)
        if symbol.roll:
            _roll(canvas, x - DIGIT_W / 2 - 5, baseline + 3, baseline - CAP - (len(symbol.keys) - 1) * STACK - 2,
                  symbol.roll)
        if symbol.tied:
            before = previous.get(staff)
            top = _stack_top(symbol, baseline)
            if before and before[0] == row:
                start_x = before[1].x
                top = min(top, _stack_top(before[1], baseline))
            else:
                start_x = x - 26
            canvas.path(f"M{start_x + 2:.1f},{top - 3:.1f} Q{(start_x + x) / 2:.1f},{top - 13:.1f} "
                        f"{x - 2:.1f},{top - 3:.1f}", 1.1)
        previous[staff] = (row, symbol)
    if note:
        note(bar, staff, None)
    _beams(canvas, bar, symbols, baseline)
    for index, (a, b) in enumerate(bar.tuplets[staff]):
        inside = [s for s in symbols if s.tuplet == index]
        if not inside:
            continue
        left, right = inside[0].x - 5, inside[-1].x + 5
        top = min(_stack_top(s, baseline) for s in inside) - 4
        canvas.path(f"M{left:.1f},{top:.1f} Q{left:.1f},{top - 6:.1f} {left + 6:.1f},{top - 6:.1f} "
                    f"L{right - 6:.1f},{top - 6:.1f} Q{right:.1f},{top - 6:.1f} {right:.1f},{top:.1f}", 0.8)
        canvas.text((left + right) / 2, top - 3.5, "3", 10, style="italic", weight="normal")


def _beams(canvas: Canvas, bar: Bar, symbols: list[Symbol], baseline: float) -> None:
    runs: list[list[Symbol]] = []
    for symbol in symbols:
        if symbol.lines and runs and runs[-1][-1].lines and _beam_key(runs[-1][-1], bar) == _beam_key(symbol, bar) \
                and runs[-1][-1].start + runs[-1][-1].length == symbol.start:
            runs[-1].append(symbol)
        elif symbol.lines:
            runs.append([symbol])
    for run in runs:
        for level in range(1, max(s.lines for s in run) + 1):
            stretch: list[Symbol] = []
            for symbol in [*run, None]:
                if symbol is not None and symbol.lines >= level:
                    stretch.append(symbol)
                    continue
                if stretch:
                    left = stretch[0].x - DIGIT_W / 2 - 1
                    right = stretch[-1].x + DIGIT_W / 2 + 1 + (7 if stretch[-1].dot else 0)
                    y = baseline + 3.5 + (level - 1) * 4
                    canvas.line(left, y, right, y, 1.3)
                stretch = []


def _roll(canvas: Canvas, x: float, bottom: float, top: float, direction: int) -> None:
    points = [f"M{x:.1f},{bottom:.1f}"]
    y, side = bottom, 1
    while y - 4 > top:
        y -= 4
        points.append(f"L{x + 2.2 * side:.1f},{y:.1f}")
        side = -side
    canvas.path(" ".join(points), 1.0)
    if direction < 0:
        canvas.path(f"M{x - 3:.1f},{bottom - 3:.1f} L{x:.1f},{bottom + 2:.1f} L{x + 3:.1f},{bottom - 3:.1f}", 1.0)


def _brace(canvas: Canvas, x: float, top: float, bottom: float, stroke: str = "#111") -> None:
    middle = (top + bottom) / 2
    w = 8
    canvas.path(f"M{x:.1f},{top:.1f} C{x - w:.1f},{top + 6:.1f} {x:.1f},{middle - 10:.1f} {x - w:.1f},{middle:.1f} "
                f"C{x:.1f},{middle + 10:.1f} {x - w:.1f},{bottom - 6:.1f} {x:.1f},{bottom:.1f}", 2.2, stroke)


def _meter(canvas: Canvas, x: float, y: float, meter: tuple[int, int], size: float = 13) -> None:
    canvas.text(x, y - size * 0.15, str(meter[0]), size)
    canvas.text(x, y + size * 0.85, str(meter[1]), size)


def _sizes(sheet: Sheet, system: list[Bar]) -> tuple[bool, list[tuple[float, float, int]]]:
    marks = any(bar.tempo or bar.meter for bar in system)
    return marks, [_measure_staff(system, staff, marks and staff == 0) for staff in range(sheet.staves)]


def system_height(sheet: Sheet, system: list[Bar]) -> float:
    """How tall a system is drawn, from its top to the foot of its last staff."""
    _, sizes = _sizes(sheet, system)
    return 18 + sum(above + below for above, below, _ in sizes) + 10 * (sheet.staves - 1)


def draw_system(canvas, sheet: Sheet, system: list[Bar], row: int, y: float,
                previous: dict[int, tuple[int, Symbol]], rule: str = "#111") -> float:
    """Draw one system with its top at ``y``: its first bar's number, the brace, the staves and the bar
    lines (in ``rule``), and the meter and tempo marks above.  Returns where the next system may begin.
    ``previous`` carries each staff's last symbol on to the next system, for the ties."""
    marks, sizes = _sizes(sheet, system)
    left = system[0].x
    number = getattr(canvas, "bar_number", None)  # a canvas may number every bar itself, at its bar line
    if number is None:  # (printed: the first bar's number before the line)
        canvas.text(left - 26, y + 12, f"({system[0].number})", 12, anchor="start", weight="normal")
    top = y + 18
    boxes = []
    for above, below, lines in sizes:
        baseline = top + above
        boxes.append(StaffBox(baseline, top, baseline + below, lines))
        top = baseline + below + 10
    line_top = [box.top + (20 if marks and index == 0 else 0) + 4 for index, box in enumerate(boxes)]
    line_bottom = [box.bottom - 2 for box in boxes]
    if sheet.staves > 1:
        _brace(canvas, left - 10, line_top[0], line_bottom[-1], rule)
    canvas.line(left, line_top[0], left, line_bottom[-1], 1.4, rule)
    for bar in system:
        if number:
            number(bar, bar.x, y + 12)
        for staff, box in enumerate(boxes):
            _draw_bar_staff(canvas, bar, staff, box, row, previous)
        end = bar.x + bar.width
        final = bar is sheet.bars[-1]
        for staff in range(sheet.staves):
            if final:
                canvas.line(end - 5, line_top[staff], end - 5, line_bottom[staff], 1.3, rule)
                canvas.line(end, line_top[staff], end, line_bottom[staff], 3.5, rule)
            else:
                canvas.line(end, line_top[staff], end, line_bottom[staff], 1.3, rule)
        above = boxes[0].top + 14
        label_x = bar.x + 8
        if bar.meter:
            _meter(canvas, label_x + 4, above, bar.meter, 12)
            label_x += 18
        if bar.tempo:
            first = bar.staves[0][0] if bar.staves[0] else None
            at = bar.x + (bar.times[first.start] if first else 20)
            canvas.text(max(at - 6, label_x), above + 4, bar.tempo, 14, anchor="start", weight="normal")
    return top + 20


def render_pages(sheet: Sheet) -> list[str]:
    """The sheet as SVG pages."""
    systems = layout_rows(sheet)
    pages: list[Canvas] = [Canvas()]
    head = pages[0]
    head.text(PAGE_W / 2, 112, sheet.title, 30, family=CJK)
    head.text(PAGE_W / 2, 146, SUBTITLE, 16, family=CJK, weight="normal", fill="#444")
    head.text(70, 196, "1=C", 17, anchor="start", weight="normal")
    _meter(head, 113, 190, sheet.meter, 12)
    head.text(130, 196, sheet.tempo, 17, anchor="start", weight="normal")
    for index, note in enumerate(sheet.notes):
        head.text(RIGHT, 182 + index * 20, note, 13, anchor="end", family=CJK, weight="normal", fill="#444")
    y = 236.0
    previous: dict[int, tuple[int, Symbol]] = {}
    for row, system in enumerate(systems):
        if y + system_height(sheet, system) > PAGE_H - 70:
            pages.append(Canvas())
            y = 70.0
        y = draw_system(pages[-1], sheet, system, row, y, previous)
    count = len(pages)
    svgs = []
    for number, page in enumerate(pages, 1):
        page.text(70, PAGE_H - 40, "LyreBot", 12, anchor="start", weight="normal", fill="#777")
        page.text(RIGHT, PAGE_H - 40, f"第 {number} 页 / 共 {count} 页", 12, anchor="end", family=CJK,
                  weight="normal", fill="#777")
        svgs.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {PAGE_W} {PAGE_H}" '
                    f'width="{PAGE_W}" height="{PAGE_H}" {ROOT}>'
                    f'<rect width="{PAGE_W}" height="{PAGE_H}" fill="#fff"/>{"".join(page.items)}</svg>')
    return svgs


PAGE_STYLE = """
@page { size: A4; margin: 0; }
html { background: #e9e7e2; }
body { margin: 0; padding: 16px 0; }
svg { display: block; width: min(100% - 32px, 820px); height: auto; margin: 0 auto 16px;
      box-shadow: 0 1px 6px rgba(0, 0, 0, .18); }
@media print {
  html { background: #fff; }
  body { padding: 0; }
  svg { width: 210mm; height: 297mm; margin: 0; box-shadow: none; break-after: page; }
}
"""


def render_html(sheet: Sheet) -> str:
    pages = "\n".join(render_pages(sheet))
    return (f'<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{escape(sheet.title)} · 诗琴简谱</title>\n<style>{PAGE_STYLE}</style>\n</head>\n'
            f'<body>\n{pages}\n</body>\n</html>\n')


def export(score: Path, target: Path) -> Path:
    """Write the score at ``score`` as 简谱 to ``target`` (an .html file)."""
    text = score.read_text(encoding="utf-8")
    sheet = sheet_of(text, score.stem)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(sheet), encoding="utf-8")
    return target


# --- For checking: a symbol as text ---------------------------------------------------------

def describe(symbols: list[Symbol]) -> str:
    """Symbols as text: 5 quarter, 5_ eighth, 5= sixteenth, 1' high, 1, low, 1+3 chord,
    ~5 tied grey, 5. dotted, 5 - - held, 0 rest."""
    words = []
    for symbol in symbols:
        if symbol.keys:
            word = "+".join(str(DIGIT[key][0]) + {1: "'", -1: ",", 0: ""}[DIGIT[key][1]] for key in symbol.keys)
        else:
            word = "0"
        word = ("~" if symbol.tied else "") + word + ("", "_", "=", "≡", "≣")[symbol.lines] + ("." if symbol.dot else "")
        word += " -" * symbol.dashes
        words.append(word)
    return " ".join(words)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="python -m lyrebot.jianpu", description="把 LyreBot 曲谱导出成诗琴简谱（HTML）")
    parser.add_argument("scores", nargs="+", type=Path, help="曲谱文件，可以一次给多个")
    parser.add_argument("-o", "--output", type=Path, help="保存位置（只给一个曲谱时可用）；默认存进 jianpu_output 文件夹")
    args = parser.parse_args(argv)
    if args.output and len(args.scores) > 1:
        parser.error("一次给多个曲谱时不能用 -o")
    failed = 0
    for path in args.scores:
        target = args.output or Path(__file__).resolve().parent.parent / "jianpu_output" / f"{path.stem}.html"
        try:
            export(path, target)
        except (OSError, ScoreError) as error:
            print(f"{path.name} 无法导出：{error}")
            failed += 1
            continue
        print(f"{path.name} → {target}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
