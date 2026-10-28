"""Read Standard MIDI Files with the standard library only.

Only what arranging needs is kept: notes (with track and channel), tempo
changes, time signatures, track names and the first program of each channel.
Times stay in ticks; ``ticks_per_beat`` converts them to quarter-note beats.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

MAX_MIDI_BYTES = 16 * 1024 * 1024
DEFAULT_TEMPO = 500_000  # microseconds per quarter note, i.e. 120 BPM


class MidiError(ValueError):
    """The file is not a Standard MIDI File that can be read."""


@dataclass(frozen=True)
class Note:
    start: int  # ticks
    end: int
    pitch: int
    velocity: int
    track: int
    channel: int  # 0-based; 9 is the General MIDI drum channel


@dataclass(frozen=True)
class MidiFile:
    ticks_per_beat: int
    notes: tuple[Note, ...]
    tempos: tuple[tuple[int, int], ...]  # (tick, microseconds per quarter note)
    time_signatures: tuple[tuple[int, int, int], ...]  # (tick, numerator, denominator)
    track_names: tuple[str, ...]
    programs: dict[tuple[int, int], int]  # (track, channel) -> first program


def _varlen(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("MIDI 文件不完整")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MidiError("MIDI 文件里有无效的长度字段")


def _text(payload: bytes) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return payload.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return payload.decode("latin-1").strip()


class _Reader:
    def __init__(self) -> None:
        self.notes: list[Note] = []
        self.tempos: list[tuple[int, int]] = []
        self.signatures: list[tuple[int, int, int]] = []
        self.names: list[str] = []
        self.programs: dict[tuple[int, int], int] = {}

    def track(self, chunk: bytes, index: int) -> None:
        pos = tick = 0
        status: int | None = None
        name = ""
        sounding: dict[tuple[int, int], deque[tuple[int, int]]] = defaultdict(deque)
        while pos < len(chunk):
            delta, pos = _varlen(chunk, pos)
            tick += delta
            if pos >= len(chunk):
                break
            byte = chunk[pos]
            if byte == 0xFF:  # meta event
                if pos + 1 >= len(chunk):
                    break
                kind = chunk[pos + 1]
                size, pos = _varlen(chunk, pos + 2)
                payload = chunk[pos:pos + size]
                pos += size
                if kind == 0x2F:
                    break
                if kind == 0x51 and len(payload) == 3:
                    tempo = int.from_bytes(payload, "big")
                    if tempo > 0:
                        self.tempos.append((tick, tempo))
                elif kind == 0x58 and len(payload) >= 2:
                    if payload[0] > 0 and payload[1] <= 6:
                        self.signatures.append((tick, payload[0], 2 ** payload[1]))
                elif kind == 0x03 and not name:
                    name = _text(payload)
                continue
            if byte in (0xF0, 0xF7):  # system exclusive
                size, pos = _varlen(chunk, pos + 1)
                pos += size
                continue
            if byte & 0x80:
                if byte >= 0xF0:
                    raise MidiError(f"MIDI 文件里有无法识别的事件 0x{byte:02X}")
                status = byte
                pos += 1
            elif status is None:
                raise MidiError("MIDI 文件里有缺少状态字节的事件")
            kind, channel = status & 0xF0, status & 0x0F
            size = 1 if kind in (0xC0, 0xD0) else 2
            if pos + size > len(chunk):
                break
            first = chunk[pos]
            second = chunk[pos + 1] if size == 2 else 0
            pos += size
            if kind == 0x90 and second > 0:
                sounding[(channel, first)].append((tick, second))
            elif kind == 0x80 or kind == 0x90:
                started = sounding.get((channel, first))
                if started:
                    begin, velocity = started.popleft()
                    self.notes.append(Note(begin, tick, first, velocity, index, channel))
            elif kind == 0xC0:
                self.programs.setdefault((index, channel), first)
        for (channel, pitch), started in sounding.items():  # never released
            for begin, velocity in started:
                self.notes.append(Note(begin, max(tick, begin), pitch, velocity, index, channel))
        self.names.append(name)


def parse_midi(data: bytes) -> MidiFile:
    if data[:4] != b"MThd" or len(data) < 14:
        raise MidiError("这不是 MIDI 文件")
    header = int.from_bytes(data[4:8], "big")
    if header < 6:
        raise MidiError("MIDI 文件头不完整")
    tracks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise MidiError("不支持按 SMPTE 时间码计时的 MIDI")
    if division == 0:
        raise MidiError("MIDI 文件的时间精度无效")
    reader = _Reader()
    pos = 8 + header
    index = 0
    while index < tracks and pos + 8 <= len(data):
        kind = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "big")
        chunk = data[pos + 8:pos + 8 + size]
        pos += 8 + size
        if kind == b"MTrk":
            reader.track(chunk, index)
            index += 1
    if index == 0:
        raise MidiError("MIDI 文件里没有音轨")
    reader.notes.sort(key=lambda note: (note.start, note.pitch, note.track, note.channel))
    reader.tempos.sort(key=lambda item: item[0])
    reader.signatures.sort(key=lambda item: item[0])
    return MidiFile(division, tuple(reader.notes), tuple(reader.tempos), tuple(reader.signatures),
                    tuple(reader.names), reader.programs)


def load_midi(path: str | Path) -> MidiFile:
    with Path(path).open("rb") as source:
        data = source.read(MAX_MIDI_BYTES + 1)
    if len(data) > MAX_MIDI_BYTES:
        raise MidiError("MIDI 文件太大（上限 16 MiB）")
    return parse_midi(data)
