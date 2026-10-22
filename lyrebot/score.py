"""Read compact Qin Xian scores into an exact, beat-based timeline.

The legacy punctuation durations remain supported.  LyreBot additionally accepts
explicit beat fractions such as ``a{1/3}``, so tuplets need not be approximated.
A ``speed=<BPM>`` line between measures changes the tempo from that point on.
Parts are zero-based in events and one-based in error messages.
Tempo is limited to 1–1000 BPM, playback to 24 hours, and loaded files to 2 MiB.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
import math
from pathlib import Path
import re


KEYS = frozenset("qwertyuasdfghjzxcvbnm")
MIN_BPM = 1.0
MAX_BPM = 1000.0
MAX_DURATION_SECONDS = 24 * 60 * 60
MAX_FILE_BYTES = 2 * 1024 * 1024
DURATIONS = {
    "-": Fraction(1),
    ".": Fraction(1, 2),
    "~": Fraction(1, 4),
    "*": Fraction(1, 8),
}
_TEMPO = re.compile(r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_FRACTION = re.compile(r"[0-9]+(?:/[0-9]+)?\Z")


class ScoreError(ValueError):
    """A score cannot be interpreted without changing its contents."""


@dataclass(frozen=True)
class NoteEvent:
    start: Fraction
    duration: Fraction
    keys: tuple[str, ...]
    part: int
    line: int


@dataclass(frozen=True)
class Score:
    bpm: float
    events: tuple[NoteEvent, ...]
    total_beats: Fraction
    measures: int
    parts: int
    # Tempo changes after the start, as (beat position, BPM), in beat order.
    tempo_changes: tuple[tuple[Fraction, float], ...] = ()

    def tempo_map(self) -> tuple[tuple[Fraction, float], ...]:
        """All tempo segments, starting with the header tempo at beat 0."""
        return ((Fraction(0), self.bpm),) + tuple(self.tempo_changes)

    @property
    def duration_seconds(self) -> float:
        tempos = self.tempo_map()
        for _, bpm in tempos:
            if not math.isfinite(bpm) or not MIN_BPM <= bpm <= MAX_BPM:
                raise ScoreError("速度必须在 1 到 1000 BPM 之间")
        if self.total_beats < 0:
            raise ScoreError("曲谱时长必须在 0 到 24 小时之间")
        # Exact arithmetic first: converting an enormous Fraction into a float
        # can overflow before its final playback length is validated.
        seconds = Fraction(0)
        for index, (start, bpm) in enumerate(tempos):
            end = tempos[index + 1][0] if index + 1 < len(tempos) else self.total_beats
            end = min(end, self.total_beats)
            if end > start:
                seconds += (end - start) * 60 / Fraction(bpm)
            if seconds > MAX_DURATION_SECONDS:
                raise ScoreError("曲谱时长必须在 0 到 24 小时之间")
        return float(seconds)


def _error(message: str, line: int, part: int | None = None) -> ScoreError:
    location = f"第 {line} 行"
    if part is not None:
        location += f"，第 {part + 1} 声部"
    return ScoreError(f"{location}：{message}")


def _parse_tempo(content: str, line: int) -> float:
    tempo = content[len("speed=") :]
    message = "speed 必须是 1 到 1000 之间的有限数字（每分钟拍数）"
    if not _TEMPO.fullmatch(tempo):
        raise _error(message, line)
    try:
        bpm = float(tempo)
    except (ValueError, OverflowError) as exc:
        raise _error("无效的速度", line) from exc
    if not math.isfinite(bpm) or not MIN_BPM <= bpm <= MAX_BPM:
        raise _error(message, line)
    return bpm


def _read_duration(text: str, position: int, line: int, part: int) -> tuple[Fraction, int]:
    """Read one punctuation group or one explicit fraction, never both."""
    if position >= len(text):
        raise _error("每个音符、和弦或休止符都需要时值", line, part)

    if text[position] in DURATIONS:
        duration = Fraction(0)
        while position < len(text) and text[position] in DURATIONS:
            duration += DURATIONS[text[position]]
            position += 1
        if position < len(text) and text[position] == "{":
            raise _error("同一个音符不能混用符号时值和 {拍数} 时值", line, part)
        return duration, position

    if text[position] == "{":
        closing = text.find("}", position + 1)
        if closing < 0:
            raise _error("{拍数} 时值缺少右括号", line, part)
        value = text[position + 1 : closing]
        if not _FRACTION.fullmatch(value):
            raise _error("{拍数} 时值必须是正整数或分数，例如 {1/3}", line, part)
        try:
            duration = Fraction(value)
        except ZeroDivisionError as exc:
            raise _error("{拍数} 时值的分母必须为正数", line, part) from exc
        except ValueError as exc:
            raise _error("{拍数} 时值过大或无效，请使用如 {1/3} 的正整数或分数", line, part) from exc
        if duration <= 0:
            raise _error("时值必须大于 0", line, part)
        position = closing + 1
        if position < len(text) and (text[position] in DURATIONS or text[position] == "{"):
            raise _error("每个音符只能有一个 {拍数} 时值，且不能再跟符号", line, part)
        return duration, position

    raise _error(f"此处应为时值，实际为 {text[position]!r}", line, part)


def _parse_part(
    text: str,
    line: int,
    part: int,
    measure_start: Fraction,
    events: list[NoteEvent],
    previous: list[int | None],
) -> Fraction:
    if not text:
        raise _error("声部为空；请用带时值的休止符 0", line, part)

    position = 0
    offset = Fraction(0)
    if text[0] in DURATIONS or text[0] == "{":
        extension, position = _read_duration(text, 0, line, part)
        prior_index = previous[part]
        if prior_index is None:
            raise _error("开头的延长时值前面必须有同一声部的音符", line, part)
        event = events[prior_index]
        events[prior_index] = replace(event, duration=event.duration + extension)
        offset += extension

    while position < len(text):
        start = position
        if text[position] == "0":
            keys: tuple[str, ...] = ()
            position += 1
        elif text[position] in KEYS:
            while position < len(text) and text[position] in KEYS:
                position += 1
            keys = tuple(text[start:position])
            if len(set(keys)) != len(keys):
                raise _error(f"和弦 {''.join(keys)!r} 中有重复的键", line, part)
        else:
            raise _error(f"不支持的字符 {text[position]!r}；只能使用小写琴键字母或 0", line, part)

        duration, position = _read_duration(text, position, line, part)
        events.append(NoteEvent(measure_start + offset, duration, keys, part, line))
        previous[part] = len(events) - 1
        offset += duration

    return offset


def parse_score(text: str) -> Score:
    """Parse a score or raise :class:`ScoreError` with a source location.

    Each measure has one or two parts.  Corresponding parts must cover the same
    number of beats in each measure.  Blank lines and ``#`` comments are ignored.
    Durations at the beginning of a later measure extend the last event in that
    part; they do not produce another keyboard attack.  A later ``speed=`` line
    changes the tempo for the following measures.
    """
    if text.startswith("﻿"):
        text = text[1:]
    lines: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        content = "".join(raw.split("#", 1)[0].split())
        if content:
            lines.append((number, content))
    if not lines:
        raise ScoreError("曲谱为空；第一行应为 speed=<每分钟拍数>")

    header_line, header = lines[0]
    if not header.startswith("speed="):
        raise _error("第一行有效内容必须是 speed=<每分钟拍数>", header_line)
    bpm = _parse_tempo(header, header_line)

    measure_lines = [item for item in lines[1:] if not item[1].startswith("speed=")]
    if not measure_lines:
        raise _error("曲谱中没有小节", header_line)
    part_count = len(measure_lines[0][1].split("|"))
    if part_count > 2:
        raise _error("最多支持两个同时演奏的声部", measure_lines[0][0])

    events: list[NoteEvent] = []
    tempo_changes: list[tuple[Fraction, float]] = []
    previous: list[int | None] = [None] * part_count
    measure_start = Fraction(0)
    for line, measure in lines[1:]:
        if measure.startswith("speed="):
            change = _parse_tempo(measure, line)
            if tempo_changes and tempo_changes[-1][0] == measure_start:
                tempo_changes[-1] = (measure_start, change)
            else:
                tempo_changes.append((measure_start, change))
            continue
        parts = measure.split("|")
        if len(parts) != part_count:
            raise _error(f"应为 {part_count} 个声部，实际为 {len(parts)} 个", line)
        lengths = [
            _parse_part(part_text, line, part, measure_start, events, previous)
            for part, part_text in enumerate(parts)
        ]
        if any(length != lengths[0] for length in lengths[1:]):
            detail = "，".join(f"声部 {part + 1} = {length} 拍" for part, length in enumerate(lengths))
            raise _error(f"同时演奏的声部时值不相等（{detail}）", line)
        measure_start += lengths[0]

    # A change at beat 0 replaces the header tempo.
    if tempo_changes and tempo_changes[0][0] == 0:
        bpm = tempo_changes.pop(0)[1]
    events.sort(key=lambda event: (event.start, event.part))
    score = Score(bpm, tuple(events), measure_start, len(measure_lines), part_count, tuple(tempo_changes))
    try:
        score.duration_seconds
    except ScoreError as exc:
        raise _error(str(exc), lines[-1][0]) from exc
    return score


def load_score(path: str | Path) -> Score:
    """Load a UTF-8 score of at most 2 MiB, with an optional Windows BOM."""
    with Path(path).open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ScoreError("曲谱文件过大；最大支持 2 MiB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ScoreError("曲谱文件必须使用 UTF-8 编码") from exc
    return parse_score(text)
