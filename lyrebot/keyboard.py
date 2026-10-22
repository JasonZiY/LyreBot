"""Windows keyboard output through SendInput, using physical scan codes.

Scan codes make the 21 keys work whatever keyboard layout is active.  Nothing
here touches Windows until ``Keyboard()`` is created.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

SCAN_CODES = {
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15, "u": 0x16,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23, "j": 0x24,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31, "m": 0x32,
}
STOP_KEY = 0x77  # F8
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # only here so INPUT has its full size
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _UNION)]


class KeyboardError(RuntimeError):
    pass


class _User32:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise KeyboardError("键盘演奏只支持 Windows")
        self.dll = ctypes.WinDLL("user32", use_last_error=True)
        self.dll.SendInput.argtypes = [ctypes.c_uint32, ctypes.POINTER(INPUT), ctypes.c_int32]
        self.dll.SendInput.restype = ctypes.c_uint32
        self.dll.GetForegroundWindow.restype = ctypes.c_void_p
        self.dll.GetAsyncKeyState.argtypes = [ctypes.c_int32]
        self.dll.GetAsyncKeyState.restype = ctypes.c_int16

    def send_input(self, inputs: list[INPUT]) -> int:
        array = (INPUT * len(inputs))(*inputs)
        return int(self.dll.SendInput(len(inputs), array, ctypes.sizeof(INPUT)))

    def foreground(self) -> int:
        return int(self.dll.GetForegroundWindow() or 0)

    def key_held(self, virtual_key: int) -> bool:
        return bool(self.dll.GetAsyncKeyState(virtual_key) & 0x8000)


class Keyboard:
    """Presses keys and remembers which ones it still holds, to release them."""

    def __init__(self, _api: Any = None) -> None:
        self._api = _api or _User32()
        self.held: set[str] = set()

    def foreground(self) -> int:
        return self._api.foreground()

    def stop_pressed(self) -> bool:
        return self._api.key_held(STOP_KEY)

    def send(self, keys: tuple[str, ...], down: bool) -> None:
        if not down:
            keys = tuple(key for key in keys if key in self.held)
        if not keys:
            return
        inputs = []
        for key in keys:
            item = INPUT(type=1)  # INPUT_KEYBOARD
            flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
            item.ki = KEYBDINPUT(0, SCAN_CODES[key], flags, 0, 0)
            inputs.append(item)
        if down:
            self.held.update(keys)
        sent = self._api.send_input(inputs)
        if sent != len(inputs):
            raise KeyboardError("Windows 拒绝了按键输入。请以管理员身份运行 LyreBot（游戏也以管理员身份运行时必须如此）。")
        if not down:
            self.held.difference_update(keys)

    def release_all(self) -> None:
        for key in sorted(self.held):
            try:
                self.send((key,), False)
            except KeyboardError:
                pass
        self.held.clear()


def foreground_title_and_pid(hwnd: int) -> tuple[str, int]:
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    buffer = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    pid = ctypes.c_uint32()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return buffer.value, int(pid.value)
