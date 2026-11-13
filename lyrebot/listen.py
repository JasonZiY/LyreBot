"""Listen to the keyboard: which of the 21 lyre keys are pressed, and when.

A low-level keyboard hook (``WH_KEYBOARD_LL``) is told of every key press on
the desktop, whichever window has the focus, so the game can keep it while
LyreBot listens.  Keys are told apart by scan code, the key's place on the
keyboard, as LyreBot plays them, so the keyboard layout does not matter.
Presses that other programs send are heard too, so another player's
performance can be written down as well.  Only the standard library is used,
and nothing here touches Windows until ``listen`` is called.

The hook needs a message loop on the thread that installed it: ``listen``
runs one, woken every ``TICK`` seconds by a timer to see whether to stop.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
import threading
import time
from typing import Callable

from .keyboard import SCAN_CODES, STOP_KEY

WAIT = 300.0  # seconds to wait for the first press before giving up
QUIET = 30.0  # seconds without a lyre key that end the listening
LONGEST = 1800.0  # seconds
TICK = 0.1  # seconds between looks at whether to stop

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_QUIT, WM_TIMER = 0x0012, 0x0113
LLKHF_EXTENDED = 0x01
MODIFIERS = {0x11, 0x12, 0x5B, 0x5C, 0xA2, 0xA3, 0xA4, 0xA5}  # Ctrl, Alt and Windows, either side
KEY_AT = {code: key for key, code in SCAN_CODES.items()}

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class ListenError(RuntimeError):
    pass


class Ear:
    """What the hook has heard, kept apart from Windows so it can be tested.

    ``press(scan, virtual, extended, now)`` and ``release(...)`` are called
    for every key event.  A key held down repeats its press until released;
    only the first counts.  Keys pressed with Ctrl, Alt or Windows held
    (switching windows, say) do not count, and neither do keys pressed while
    ``ignore()`` says so (LyreBot's own window has the focus).
    """

    swallow = False  # for tests: keep the keys heard from reaching any window

    def __init__(self, ignore: Callable[[], bool] = lambda: False):
        self.ignore = ignore
        self.presses: list[tuple[float, str]] = []
        self.held: set[int] = set()
        self.modifiers: set[int] = set()
        self.stop = False  # F8 was pressed
        self.lock = threading.Lock()

    def press(self, scan: int, virtual: int, extended: bool, now: float) -> None:
        if virtual in MODIFIERS:
            self.modifiers.add(virtual)
            return
        if virtual == STOP_KEY:
            self.stop = True
            return
        key = None if extended else KEY_AT.get(scan)
        if key is None or scan in self.held:
            return
        self.held.add(scan)
        if not self.modifiers and not self.ignore():
            with self.lock:
                self.presses.append((now, key))

    def release(self, scan: int, virtual: int) -> None:
        self.modifiers.discard(virtual)
        self.held.discard(scan)

    def heard(self) -> tuple[int, float | None]:
        """(presses so far, time of the latest)."""
        with self.lock:
            return len(self.presses), self.presses[-1][0] if self.presses else None


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    return user32, kernel32


def listen(cancel: threading.Event, report: Callable[[str, float, int], None] | None = None,
           wait: float = WAIT, quiet: float = QUIET, longest: float = LONGEST,
           ear: Ear | None = None) -> list[tuple[float, str]]:
    """The lyre keys pressed, as (seconds from the first press, key), until
    ``cancel`` is set, F8 is pressed, ``quiet`` seconds pass without a press
    or ``longest`` seconds pass from the first.  Nothing is heard while
    LyreBot's own window has the focus.

    ``report(state, seconds, presses)`` is called a few times a second with
    state "waiting" (seconds waited) or "listening" (seconds since the first press).
    """
    if sys.platform != "win32":
        raise ListenError("键盘记录只支持 Windows")
    user32, kernel32 = _user32()
    own = os.getpid()

    def ours() -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        return pid.value == own

    ear = ear or Ear(ours)

    @HOOKPROC
    def hook(code: int, message: int, data: int) -> int:
        if code >= 0:
            event = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if message in (WM_KEYDOWN, WM_SYSKEYDOWN):
                ear.press(event.scanCode, event.vkCode, bool(event.flags & LLKHF_EXTENDED), time.perf_counter())
            elif message in (WM_KEYUP, WM_SYSKEYUP):
                ear.release(event.scanCode, event.vkCode)
            if ear.swallow:
                return 1
        return user32.CallNextHookEx(None, code, message, data)

    handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook, kernel32.GetModuleHandleW(None), 0)
    if not handle:
        raise ListenError(f"Windows 不允许监听键盘（错误 {ctypes.get_last_error()}）")
    timer = user32.SetTimer(None, 0, round(TICK * 1000), None)
    began = time.perf_counter()
    try:
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message != WM_TIMER:
                continue
            now = time.perf_counter()
            count, latest = ear.heard()
            if cancel.is_set() or ear.stop:
                break
            if latest is None:
                if now - began > wait:
                    break
                if report:
                    report("waiting", now - began, 0)
                continue
            first = ear.presses[0][0]
            if now - latest > quiet or now - first > longest:
                break
            if report:
                report("listening", now - first, count)
    finally:
        user32.KillTimer(None, timer)
        user32.UnhookWindowsHookEx(handle)
    with ear.lock:
        presses = list(ear.presses)
    if not presses:
        return []
    first = presses[0][0]
    return [(at - first, key) for at, key in presses]
