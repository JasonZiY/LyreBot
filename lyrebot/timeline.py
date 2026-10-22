"""Turn a score into timed key-down / key-up events (in seconds)."""

from __future__ import annotations

from dataclasses import dataclass

from .score import Score

TAP_MS = 35.0


@dataclass(frozen=True)
class KeyEvent:
    at: float
    keys: tuple[str, ...]
    down: bool


@dataclass(frozen=True)
class Timeline:
    events: tuple[KeyEvent, ...]
    duration: float


def compile_timeline(score: Score, speed: float = 1.0, tap_ms: float = TAP_MS) -> Timeline:
    """Every strike becomes a short tap; written durations only place them in time.

    A tap lasts ``tap_ms`` at most, never outlasts its note, and always ends
    before the next strike of the same key.  The same key struck at the same
    moment by several parts is pressed once.  Ornament delays (rolls, grace
    notes) are milliseconds, squeezed if they would take more than half the note.
    """
    tempo = score.tempo_map(speed)
    tap = tap_ms / 1000.0
    strikes: dict[str, list[tuple[float, float]]] = {}  # key -> [(at, note end)]
    for event in score.events:
        if not event.strikes:
            continue
        begin = tempo.seconds(event.start)
        end = tempo.seconds(event.start + event.duration)
        longest = max(strike.delay_ms for strike in event.strikes) / 1000.0
        squeeze = 1.0 if longest <= (end - begin) / 2 else (end - begin) / 2 / longest
        for strike in event.strikes:
            at = begin + strike.delay_ms / 1000.0 * squeeze
            for key in strike.keys:
                strikes.setdefault(key, []).append((at, end))

    batches: dict[tuple[float, bool], list[str]] = {}
    for key, found in strikes.items():
        found.sort()
        merged: list[tuple[float, float]] = []
        for at, end in found:
            if merged and abs(merged[-1][0] - at) < 1e-9:
                merged[-1] = (merged[-1][0], min(merged[-1][1], end))
            else:
                merged.append((at, end))
        for index, (at, end) in enumerate(merged):
            length = min(tap, end - at)
            if index + 1 < len(merged):
                length = min(length, (merged[index + 1][0] - at) / 2)
            batches.setdefault((at, True), []).append(key)
            batches.setdefault((at + length, False), []).append(key)

    # Sorting (time, down) puts releases before presses at the same instant.
    events = tuple(KeyEvent(at, tuple(sorted(keys)), down) for (at, down), keys in sorted(batches.items()))
    duration = tempo.seconds(score.total_beats)
    if events:
        duration = max(duration, events[-1].at)
    return Timeline(events, duration)
