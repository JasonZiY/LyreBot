"""Play a timeline against a clock, stopping on F8 or a window switch."""

from __future__ import annotations

import threading
import time
from typing import Callable, Protocol

from .timeline import Timeline

LATE_LIMIT = 0.1  # seconds; beyond this the clock is shifted instead of rushing notes


class Backend(Protocol):
    def foreground(self) -> int: ...
    def stop_pressed(self) -> bool: ...
    def send(self, keys: tuple[str, ...], down: bool) -> None: ...
    def release_all(self) -> None: ...


def play(
    timeline: Timeline,
    backend: Backend,
    target: int,
    cancel: threading.Event,
    on_progress: Callable[[float], None] | None = None,
    *,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Return "done", "stopped" (F8 or cancel) or "focus" (window switched).

    If the computer stalls, the rest of the song is delayed by the stall rather
    than sending the missed notes in a burst.  Keys are always released.
    """
    origin = clock()
    next_report = 0.0

    def interrupted() -> str | None:
        if cancel.is_set() or backend.stop_pressed():
            return "stopped"
        if backend.foreground() != target:
            return "focus"
        return None

    def wait(at: float) -> str | None:
        nonlocal next_report
        while True:
            reason = interrupted()
            if reason:
                return reason
            now = clock()
            if on_progress and now - origin >= next_report:
                on_progress(min(now - origin, timeline.duration))
                next_report = now - origin + 0.1
            remaining = origin + at - now
            if remaining <= 0:
                return None
            # Sleep coarsely, then spin the last couple of milliseconds.
            sleep(min(remaining - 0.0015, 0.005) if remaining > 0.002 else 0)

    try:
        for event in timeline.events:
            reason = wait(event.at)
            if reason:
                return reason
            late = clock() - (origin + event.at)
            if late > LATE_LIMIT:
                origin += late
            backend.send(event.keys, event.down)
        reason = wait(timeline.duration)
        if on_progress:
            on_progress(timeline.duration)
        return reason or "done"
    finally:
        backend.release_all()
