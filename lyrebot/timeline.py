"""Turn a score into timed key-down / key-up events (in seconds)."""

from __future__ import annotations

from dataclasses import dataclass

from .score import Score

TAP_MS = 35.0
RING = 2.0  # seconds a note sounds on the computer (``compile_ringing``), as the lyre's rings on


@dataclass(frozen=True)
class KeyEvent:
    at: float
    keys: tuple[str, ...]
    down: bool


@dataclass(frozen=True)
class Timeline:
    events: tuple[KeyEvent, ...]
    duration: float


def _strikes(score: Score, speed: float) -> tuple[dict[str, list[tuple[float, float]]], float]:
    """Each key's strikes as (when, its note's written end) in seconds, in order, with strikes of one key
    at the same moment (by several parts) made one; and the score's length in seconds."""
    tempo = score.tempo_map(speed)
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
    for key, found in strikes.items():
        found.sort()
        merged: list[tuple[float, float]] = []
        for at, end in found:
            if merged and abs(merged[-1][0] - at) < 1e-9:
                merged[-1] = (merged[-1][0], min(merged[-1][1], end))
            else:
                merged.append((at, end))
        strikes[key] = merged
    return strikes, tempo.seconds(score.total_beats)


def _events(batches: dict[tuple[float, bool], list[str]]) -> tuple[KeyEvent, ...]:
    # Sorting (time, down) puts releases before presses at the same instant.
    return tuple(KeyEvent(at, tuple(sorted(keys)), down) for (at, down), keys in sorted(batches.items()))


def compile_timeline(score: Score, speed: float = 1.0, tap_ms: float = TAP_MS) -> Timeline:
    """Every strike becomes a short tap; written durations only place them in time.

    A tap lasts ``tap_ms`` at most, never outlasts its note, and always ends
    before the next strike of the same key.  The same key struck at the same
    moment by several parts is pressed once.  Ornament delays (rolls, grace
    notes) are milliseconds, squeezed if they would take more than half the note.
    """
    tap = tap_ms / 1000.0
    strikes, length = _strikes(score, speed)
    batches: dict[tuple[float, bool], list[str]] = {}
    for key, merged in strikes.items():
        for index, (at, end) in enumerate(merged):
            tapped = min(tap, end - at)
            if index + 1 < len(merged):
                tapped = min(tapped, (merged[index + 1][0] - at) / 2)
            batches.setdefault((at, True), []).append(key)
            batches.setdefault((at + tapped, False), []).append(key)
    events = _events(batches)
    return Timeline(events, max(length, events[-1].at) if events else length)


def compile_ringing(score: Score, speed: float = 1.0, ring: float = RING) -> Timeline:
    """The same strikes, timed as ``compile_timeline`` times them, for hearing on the computer rather than
    pressing keys: a key is let go only when its note should stop sounding.  That is after its written
    length or ``ring`` seconds, whichever is longer, or at the very moment the key is struck again (the
    release sorts before the press).  The timeline lasts as long as the tapped one: the last notes ring on
    past its end.
    """
    strikes, length = _strikes(score, speed)
    batches: dict[tuple[float, bool], list[str]] = {}
    for key, merged in strikes.items():
        for index, (at, end) in enumerate(merged):
            stop = max(at + ring, end)
            if index + 1 < len(merged):
                stop = min(stop, merged[index + 1][0])  # that very number, so it sorts with the press
            batches.setdefault((at, True), []).append(key)
            batches.setdefault((stop, False), []).append(key)
    events = _events(batches)
    presses = [event.at for event in events if event.down]
    return Timeline(events, max(length, presses[-1]) if presses else length)
