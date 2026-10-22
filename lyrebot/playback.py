"""Convert musical beats to short key taps and play fixed-clock deadlines."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
import math
import threading
import time
from typing import Callable, Protocol, Any

from .windows_input import KEY_ORDER


@dataclass(frozen=True)
class InputEvent:
    at: float
    keys: tuple[str, ...]
    down: bool


@dataclass(frozen=True)
class Timeline:
    events: tuple[InputEvent, ...]
    duration_seconds: float


@dataclass(frozen=True)
class PlaybackResult:
    reason: str
    elapsed_seconds: float


class PlaybackError(RuntimeError):
    """Playback stopped because its timing or target is no longer safe."""


class Backend(Protocol):
    def foreground_window(self) -> int: ...
    def stop_requested(self) -> bool: ...
    def held_modifiers(self) -> tuple[str, ...]: ...
    def send_keys(self, keys: tuple[str, ...], down: bool) -> None: ...
    def release_all(self) -> None: ...


def compile_timeline(score: Any, bpm: float | None = None, tap_ms: float = 35.0) -> Timeline:
    """Use note lengths for rhythm and bounded short taps for keyboard output.

    ``bpm`` overrides the starting tempo; later tempo changes in the score are
    scaled by the same ratio. Simultaneous appearances of one key are one
    attack. Every pulse ends before the next attack of that key, even for
    extremely fast repeated notes.
    """
    try:
        tempo = float(score.bpm if bpm is None else bpm)
        tap_seconds = float(tap_ms) / 1000.0
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("速度和按键时长必须是有限数字。") from error
    if not math.isfinite(tempo) or not 1 <= tempo <= 1000:
        raise ValueError("速度必须在 1 到 1000 BPM 之间。")
    if not math.isfinite(tap_seconds) or tap_seconds <= 0:
        raise ValueError("按键时长必须是正数。")
    ratio = tempo / float(score.bpm) if bpm is not None else 1.0
    # Tempo segments as (start beat, start second, seconds per beat).
    segments: list[tuple[Fraction, float, float]] = [(Fraction(0), 0.0, 60.0 / tempo)]
    for change_beat, change_bpm in getattr(score, "tempo_changes", ()):
        change_tempo = float(change_bpm) * ratio
        if not math.isfinite(change_tempo) or change_tempo <= 0:
            raise ValueError("曲中变速的速度无效。")
        begin, begin_seconds, per_beat = segments[-1]
        try:
            change_seconds = begin_seconds + float(Fraction(change_beat) - begin) * per_beat
        except (OverflowError, ValueError) as error:
            raise ValueError("曲谱包含无法表示的演奏时间。") from error
        segments.append((Fraction(change_beat), change_seconds, 60.0 / change_tempo))
    segment_starts = [segment[0] for segment in segments]

    def to_seconds(beats: Fraction) -> float:
        begin, begin_seconds, per_beat = segments[bisect_right(segment_starts, beats) - 1 if beats >= 0 else 0]
        try:
            seconds = begin_seconds + float(beats - begin) * per_beat
        except (OverflowError, ValueError) as error:
            raise ValueError("曲谱包含无法表示的演奏时间。") from error
        if not math.isfinite(seconds) or not 0 <= seconds <= 86400:
            raise ValueError("演奏时长不能超过 24 小时。")
        if beats > 0 and seconds <= 0:
            raise ValueError("曲谱中的时值过小，无法表示。")
        return seconds

    duration_seconds = to_seconds(Fraction(score.total_beats))
    by_key: dict[str, dict[Fraction, Fraction]] = {}
    onset_times: dict[float, Fraction] = {}
    for note in score.events:
        start, duration = Fraction(note.start), Fraction(note.duration)
        if start < 0 or duration <= 0:
            raise ValueError("音符必须从 0 拍或之后开始，且时值为正。")
        at = to_seconds(start)
        if at in onset_times and onset_times[at] != start:
            raise ValueError("曲谱中有起音过于接近、无法区分的音符。")
        onset_times[at] = start
        for key in note.keys:
            if key not in KEY_ORDER:
                raise ValueError(f"不支持的按键：{key!r}。")
            attacks = by_key.setdefault(key, {})
            attacks[start] = min(attacks.get(start, duration), duration)
    batches: dict[tuple[float, bool], set[str]] = {}
    for key, attacks in by_key.items():
        starts = sorted(attacks)
        for index, start in enumerate(starts):
            at = to_seconds(start)
            length = min(tap_seconds, to_seconds(start + attacks[start]) - at)
            if index + 1 < len(starts):
                gap = to_seconds(starts[index + 1]) - at
                length = min(length, gap / 2.0)
            if length <= 0 or at + length <= at:
                raise ValueError("曲谱中的按键时值过小，无法表示。")
            batches.setdefault((at, True), set()).add(key)
            batches.setdefault((at + length, False), set()).add(key)
    events = tuple(InputEvent(at, tuple(sorted(keys, key=KEY_ORDER.__getitem__)), down)
                   for (at, down), keys in sorted(batches.items()))
    if events:
        duration_seconds = max(duration_seconds, events[-1].at)
    if duration_seconds > 86400:
        raise ValueError("演奏时长不能超过 24 小时。")
    return Timeline(events, duration_seconds)


class _StopPlayback(Exception):
    pass


def play_timeline(
    timeline: Timeline,
    backend: Backend,
    target_hwnd: int,
    stop_event: threading.Event,
    *,
    on_progress: Callable[[float], None] | None = None,
    _clock: Callable[[], float] = time.monotonic,
) -> PlaybackResult:
    """Play against one monotonic start, stopping instead of catching up late.

    ``on_progress`` receives elapsed seconds on the playback worker thread.
    There is no window activation and no pause/resume. Every exit path attempts
    to release all keys owned by the backend, including a failed key-down.
    """
    started = _clock()
    next_progress = 0.0
    reason = "completed"
    failure: BaseException | None = None
    attack_times = sorted({event.at for event in timeline.events if event.down})

    def safety_check() -> None:
        if stop_event.is_set() or backend.stop_requested():
            raise _StopPlayback()
        if not target_hwnd or backend.foreground_window() != target_hwnd:
            raise PlaybackError("目标窗口失去焦点，演奏已停止。")
        modifiers = backend.held_modifiers()
        if modifiers:
            raise PlaybackError("检测到按住了 " + "、".join(modifiers) + "，为避免触发快捷键，演奏已停止。")

    def wait_until(deadline: float) -> None:
        nonlocal next_progress
        while True:
            safety_check()
            now = _clock()
            elapsed = max(0.0, now - started)
            if on_progress is not None and elapsed >= next_progress:
                on_progress(min(elapsed, timeline.duration_seconds))
                next_progress = elapsed + 0.05
            remaining = deadline - now
            if remaining <= 0:
                return
            stop_event.wait(min(0.01, remaining))

    try:
        for event in timeline.events:
            deadline = started + event.at
            wait_until(deadline)
            # Recheck immediately before the send, including after callbacks.
            safety_check()
            now = _clock()
            if now - deadline > 0.150:
                raise PlaybackError("演奏落后超过 150 ms，已停止。请尝试关闭占用资源的程序。")
            next_attack = bisect_right(attack_times, event.at)
            if next_attack < len(attack_times) and now - started > attack_times[next_attack]:
                raise PlaybackError("演奏错过了下一个音符的时间点，为避免积压的音符一齐发出，已停止。")
            backend.send_keys(event.keys, event.down)
        wait_until(started + timeline.duration_seconds)
    except _StopPlayback:
        reason = "stopped"
    except BaseException as error:
        failure = error
    finally:
        try:
            backend.release_all()
        except Exception as error:
            previous = f"{failure} " if failure is not None else ""
            raise PlaybackError(previous + f"松开按键失败：{error}") from error
    if failure is not None:
        if isinstance(failure, (KeyboardInterrupt, SystemExit, PlaybackError)):
            raise failure
        raise PlaybackError(str(failure)) from failure
    elapsed = max(0.0, _clock() - started)
    if on_progress is not None:
        on_progress(min(elapsed, timeline.duration_seconds))
    return PlaybackResult(reason, elapsed)
