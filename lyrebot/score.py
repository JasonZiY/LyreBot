"""Read LyreBot text scores into exact, beat-based events.

The notation is documented in README.md.  Everything here works in quarter-note
beats using exact fractions; seconds appear only through ``TempoMap``.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
from fractions import Fraction
import math
from pathlib import Path
import re


KEYS = frozenset("qwertyuasdfghjzxcvbnm")
SYMBOLS = {"-": Fraction(1), ".": Fraction(1, 2), "~": Fraction(1, 4), "*": Fraction(1, 8)}
DEFAULT_ROLL_MS = 30.0
DEFAULT_GRACE_MS = 60.0
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 24 * 60 * 60

_NUMBER = r"[0-9]+(?:\.[0-9]+)?"
_FRACTION = re.compile(r"[0-9]+(?:/[0-9]+)?")
_DIRECTIVE = re.compile(r"([a-z]+)=(.*)")


class ScoreError(ValueError):
    """The text cannot be played without guessing what it means."""


@dataclass(frozen=True)
class Strike:
    """Keys pressed together, ``delay_ms`` after their event's onset."""
    keys: tuple[str, ...]
    delay_ms: float = 0.0


@dataclass(frozen=True)
class Event:
    start: Fraction
    duration: Fraction
    strikes: tuple[Strike, ...]  # empty for a rest
    part: int
    line: int

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for strike in self.strikes for key in strike.keys)


@dataclass(frozen=True)
class TempoMark:
    """From ``beat`` on, play at ``bpm``; with ``target``, glide linearly to it
    by the next mark (or the end of the score)."""
    beat: Fraction
    bpm: float
    target: float | None = None


class TempoMap:
    """Convert beat positions to seconds, including gradual tempo changes."""

    def __init__(self, marks: tuple[TempoMark, ...], total_beats: Fraction, speed: float = 1.0):
        if not marks or marks[0].beat != 0:
            raise ScoreError("曲谱开头必须有 speed=")
        if not (math.isfinite(speed) and speed > 0):
            raise ValueError("速度倍率必须是正数")
        self._segments: list[tuple[Fraction, float, float, float, float]] = []
        elapsed = 0.0
        for index, mark in enumerate(marks):
            end = marks[index + 1].beat if index + 1 < len(marks) else max(total_beats, mark.beat)
            begin = mark.bpm * speed
            finish = begin if mark.target is None else mark.target * speed
            length = float(end - mark.beat)
            self._segments.append((mark.beat, elapsed, begin, finish, length))
            elapsed += self._within(begin, finish, length, length)
        self._starts = [segment[0] for segment in self._segments]

    @staticmethod
    def _within(begin: float, finish: float, length: float, beats: float) -> float:
        if beats <= 0:
            return 0.0
        if length <= 0 or abs(finish - begin) < 1e-12:
            return 60.0 * beats / begin
        # Tempo rises or falls linearly in beats; integrate 60 / tempo.
        slope = (finish - begin) / length
        return 60.0 / slope * math.log1p(slope * beats / begin)

    def seconds(self, beat: Fraction) -> float:
        index = bisect_right(self._starts, beat) - 1
        start, elapsed, begin, finish, length = self._segments[max(index, 0)]
        beats = float(beat - start)
        if length <= 0 or beats <= length:
            return elapsed + self._within(begin, finish, length, beats)
        # Beyond the end of the score: continue at the arrival tempo.
        return elapsed + self._within(begin, finish, length, length) + 60.0 * (beats - length) / finish


@dataclass(frozen=True)
class Score:
    events: tuple[Event, ...]
    total_beats: Fraction
    tempo: tuple[TempoMark, ...]
    measures: int
    parts: int

    @property
    def bpm(self) -> float:
        return self.tempo[0].bpm

    @property
    def tempo_changes(self) -> int:
        return len(self.tempo) - 1

    def tempo_map(self, speed: float = 1.0) -> TempoMap:
        return TempoMap(self.tempo, self.total_beats, speed)

    @property
    def duration_seconds(self) -> float:
        return self.tempo_map().seconds(self.total_beats)

    @property
    def notes(self) -> int:
        return sum(1 for event in self.events if event.strikes)


def _where(line: int, part: int | None) -> str:
    return f"第 {line} 行" + (f"第 {part + 1} 声部" if part is not None else "")


class _Part:
    """Reads one part of one measure line (whitespace already removed)."""

    def __init__(self, text: str, line: int, part: int, roll_ms: float, grace_ms: float):
        self.text, self.line, self.part = text, line, part
        self.roll_ms, self.grace_ms = roll_ms, grace_ms
        self.pos = 0

    def error(self, message: str) -> ScoreError:
        before = self.text[max(0, self.pos - 8):self.pos]
        after = self.text[self.pos:self.pos + 8]
        return ScoreError(f"{_where(self.line, self.part)}：{message}（位置：{before}▸{after}）")

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def duration(self) -> Fraction | None:
        """Sum every duration symbol and {fraction} at the cursor, if any."""
        total = Fraction(0)
        found = False
        while True:
            char = self.peek()
            if char and char in SYMBOLS:
                total += SYMBOLS[char]
                self.pos += 1
            elif char == "{":
                close = self.text.find("}", self.pos)
                value = self.text[self.pos + 1:close] if close >= 0 else ""
                if close < 0 or not _FRACTION.fullmatch(value):
                    raise self.error("{拍数} 应为正整数或分数，例如 {2} 或 {1/3}")
                numerator, _, denominator = value.partition("/")
                if int(numerator) == 0 or (denominator and int(denominator) == 0):
                    raise self.error("{拍数} 必须大于 0")
                total += Fraction(value)
                self.pos = close + 1
            else:
                return total if found else None
            found = True

    def keys(self) -> tuple[str, ...]:
        start = self.pos
        while self.peek() and self.peek() in KEYS:
            self.pos += 1
        keys = tuple(self.text[start:self.pos])
        if len(set(keys)) != len(keys):
            self.pos = start
            raise self.error("和弦里有重复的键")
        return keys

    def rolled(self) -> tuple[str, ...]:
        """Read the chord after ``^``."""
        self.pos += 1
        keys = self.keys()
        if len(keys) < 2:
            raise self.error("^ 琶音后面应是至少两个音的和弦")
        return keys

    def event(self) -> tuple[Fraction, tuple[Strike, ...]]:
        graces: list[tuple[str, ...]] = []
        held: tuple[str, ...] = ()  # keys struck plainly at the start of a roll
        roll = False
        while True:
            if self.peek() == "^":
                roll, keys = True, self.rolled()
                break
            if self.peek() == "0":
                if graces:
                    raise self.error("倚音后面必须是音符，不能是休止符")
                self.pos += 1
                keys = ()
                break
            keys = self.keys()
            if not keys:
                raise self.error(f"无法识别的字符 {self.peek()!r}")
            if self.peek() == "^":  # e^bfj: e sounds with the first note of the roll
                held, keys, roll = keys, self.rolled(), True
                if set(held) & set(keys):
                    raise self.error("同一个键不能既在琶音里又在琶音外")
                break
            if self.peek() != "'":
                break
            self.pos += 1
            graces.append(keys)
        duration = self.duration()
        if duration is None:
            raise self.error("缺少时值；每个音符、和弦和休止符后面都要写时值")
        strikes = [Strike(grace, index * self.grace_ms) for index, grace in enumerate(graces)]
        main = len(graces) * self.grace_ms
        if held:
            strikes.append(Strike(held, main))
        if roll:
            strikes += [Strike((key,), main + index * self.roll_ms) for index, key in enumerate(keys)]
        elif keys:
            strikes.append(Strike(keys, main))
        return duration, tuple(strikes)

    def tie(self) -> Fraction:
        """Read ``+<duration>``: extend the previous note without a new press."""
        self.pos += 1
        extra = self.duration()
        if extra is None:
            raise self.error("+ 后面要写延长的时值，例如 +-")
        return extra

    def items(self, closing: str) -> tuple[list[tuple[Fraction, Fraction, tuple[Strike, ...]]], Fraction]:
        """Events relative to the start, and their written total length."""
        found: list[tuple[Fraction, Fraction, tuple[Strike, ...]]] = []
        offset = Fraction(0)
        while self.peek() != closing:
            if not self.peek():
                raise self.error("括号组缺少右括号 )")
            if self.peek() == "(":
                self.pos += 1
                inner, written = self.items(")")
                self.pos += 1
                if not inner:
                    raise self.error("括号组是空的")
                total = self.duration()
                if total is None:
                    raise self.error("括号组后面要写时值，表示整组一共占多少拍")
                scale = total / written
                found += [(offset + at * scale, length * scale, strikes) for at, length, strikes in inner]
                offset += total
            elif self.peek() == ")":
                raise self.error("多余的右括号 )")
            elif self.peek() == "+":
                extra = self.tie()
                if not found:
                    raise self.error("+ 前面没有可延长的音")
                at, length, strikes = found[-1]
                found[-1] = (at, length + extra, strikes)
                offset += extra
            else:
                length, strikes = self.event()
                found.append((offset, length, strikes))
                offset += length
        return found, offset


def _parse_speed(value: str, line: int, beat: Fraction, current: float | None) -> TempoMark:
    """``120`` jumps, ``120>80`` glides from 120, ``>80`` glides from ``current``."""
    left, glide, right = value.partition(">")
    numbers = [text for text in (left, right) if text]
    if not numbers or not all(re.fullmatch(_NUMBER, text) for text in numbers) or (glide and not right):
        raise ScoreError(f"{_where(line, None)}：speed 应写成 speed=120、speed=120>80 或 speed=>80")
    if glide and not left and current is None:
        raise ScoreError(f"{_where(line, None)}：speed=>… 前面需要已有速度")
    begin = float(left) if left else current
    target = float(right) if glide else None
    for bpm in (begin, target):
        if bpm is not None and not 1 <= bpm <= 1000:
            raise ScoreError(f"{_where(line, None)}：速度必须在 1 到 1000 BPM 之间")
    return TempoMark(beat, begin, target)


def _parse_ms(name: str, value: str, line: int) -> float:
    try:
        number = float(value)
    except ValueError:
        number = -1.0
    if not (math.isfinite(number) and 0 <= number <= 1000) or not re.fullmatch(_NUMBER, value):
        raise ScoreError(f"{_where(line, None)}：{name}= 应为 0 到 1000 之间的毫秒数")
    return number


def parse_score(text: str) -> Score:
    """Parse a score, or raise ScoreError naming the line and part at fault."""
    text = text.removeprefix("﻿")
    events: list[Event] = []
    marks: list[TempoMark] = []
    last_in_part: list[int | None] = []
    parts = 0
    measures = 0
    position = Fraction(0)
    roll_ms, grace_ms = DEFAULT_ROLL_MS, DEFAULT_GRACE_MS

    for number, raw in enumerate(text.splitlines(), start=1):
        content = "".join(raw.split("#", 1)[0].split())
        if not content:
            continue
        directive = _DIRECTIVE.fullmatch(content)
        if directive:
            name, value = directive.groups()
            if name == "speed":
                # A later mark at the same position replaces the earlier one.
                marks = [mark for mark in marks if mark.beat < position]
                current = None
                if marks:  # a glide arrives at its target exactly here
                    current = marks[-1].bpm if marks[-1].target is None else marks[-1].target
                marks.append(_parse_speed(value, number, position, current))
            elif name == "roll":
                roll_ms = _parse_ms("roll", value, number)
            elif name == "grace":
                grace_ms = _parse_ms("grace", value, number)
            else:
                raise ScoreError(f"{_where(number, None)}：未知的指令 {name}=")
            continue

        if not marks:
            raise ScoreError(f"{_where(number, None)}：第一个小节之前需要 speed=<每分钟拍数>")
        texts = content.split("|")
        if not parts:
            parts = len(texts)
            last_in_part = [None] * parts
        elif len(texts) != parts:
            raise ScoreError(f"{_where(number, None)}：应有 {parts} 个声部，这里有 {len(texts)} 个")

        lengths = []
        for part, part_text in enumerate(texts):
            if not part_text:
                raise ScoreError(f"{_where(number, part)}：声部是空的；不出声请写休止符，如 0-")
            reader = _Part(part_text, number, part, roll_ms, grace_ms)
            length = Fraction(0)
            lead = reader.tie() if reader.peek() == "+" else reader.duration()
            if lead is not None:
                index = last_in_part[part]
                if index is None:
                    raise reader.error("行首的延长时值前面没有可延长的音")
                events[index] = replace(events[index], duration=events[index].duration + lead)
                length += lead
            found, written = reader.items("")
            for at, duration, strikes in found:
                events.append(Event(position + length + at, duration, strikes, part, number))
                last_in_part[part] = len(events) - 1
            lengths.append(length + written)
        if any(length != lengths[0] for length in lengths):
            detail = "，".join(f"声部 {index + 1} 是 {_beats(length)} 拍" for index, length in enumerate(lengths))
            raise ScoreError(f"{_where(number, None)}：各声部长度不一致（{detail}）")
        position += lengths[0]
        measures += 1

    if not measures:
        raise ScoreError("曲谱里没有小节")
    events.sort(key=lambda event: (event.start, event.part))
    score = Score(tuple(events), position, tuple(marks), measures, parts)
    if score.duration_seconds > MAX_SECONDS:
        raise ScoreError("曲谱时长超过 24 小时")
    return score


def _beats(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def load_score(path: str | Path) -> Score:
    with Path(path).open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ScoreError("曲谱文件太大（上限 2 MiB）")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ScoreError("曲谱必须是 UTF-8 编码") from error
    return parse_score(text)
