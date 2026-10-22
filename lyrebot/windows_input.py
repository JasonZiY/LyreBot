"""Windows keyboard output using fixed physical QWERTY scan codes.

Importing this module does not access Windows or generate input. The backend is
created only when the user starts a playback session.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import sys
from typing import Any


SCAN_CODES = {
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15, "u": 0x16,
    "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23, "j": 0x24,
    "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31, "m": 0x32,
}
KEY_ORDER = {key: index for index, key in enumerate(SCAN_CODES)}

# Explicit Windows widths also make ABI tests meaningful on non-Windows hosts.
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
LONG = ctypes.c_int32
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", DWORD), ("data", _INPUTUNION)]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process_id: int = 0


class WindowsInputError(RuntimeError):
    """Windows could not safely complete an input operation."""


class _WindowsAPI:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise WindowsInputError("键盘演奏仅支持 Windows。")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.enum_callback = ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_ssize_t)
        signatures = {
            "SendInput": ([ctypes.c_uint32, ctypes.POINTER(INPUT), ctypes.c_int32], ctypes.c_uint32),
            "GetForegroundWindow": ([], ctypes.c_void_p),
            "GetAsyncKeyState": ([ctypes.c_int32], ctypes.c_int16),
            "GetCursorPos": ([ctypes.POINTER(POINT)], ctypes.c_int32),
            "EnumWindows": ([self.enum_callback, ctypes.c_ssize_t], ctypes.c_int32),
            "IsWindowVisible": ([ctypes.c_void_p], ctypes.c_int32),
            "GetWindowTextLengthW": ([ctypes.c_void_p], ctypes.c_int32),
            "GetWindowTextW": ([ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int32], ctypes.c_int32),
            "GetWindowThreadProcessId": ([ctypes.c_void_p, ctypes.POINTER(DWORD)], DWORD),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(self.user32, name)
            function.argtypes = argtypes
            function.restype = restype

    def send_inputs(self, inputs: list[INPUT]) -> int:
        array = (INPUT * len(inputs))(*inputs)
        ctypes.set_last_error(0)
        return int(self.user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)))

    def last_error(self) -> int:
        return int(ctypes.get_last_error())

    def foreground_window(self) -> int:
        return int(self.user32.GetForegroundWindow() or 0)

    def async_key_state(self, virtual_key: int) -> int:
        return int(self.user32.GetAsyncKeyState(virtual_key))

    def cursor_position(self) -> tuple[int, int]:
        point = POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise WindowsInputError("无法读取鼠标位置，演奏已停止。")
        return int(point.x), int(point.y)

    def window_info(self, hwnd: int) -> WindowInfo:
        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        process_id = DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        return WindowInfo(hwnd, buffer.value, int(process_id.value))

    def list_windows(self) -> list[WindowInfo]:
        result: list[WindowInfo] = []

        @self.enum_callback
        def callback(hwnd: int, _parameter: int) -> int:
            if self.user32.IsWindowVisible(hwnd):
                item = self.window_info(int(hwnd))
                if item.title.strip():
                    result.append(item)
            return 1

        if not self.user32.EnumWindows(callback, 0):
            raise WindowsInputError("Windows 无法列出应用程序窗口。")
        return result


def list_windows(*, exclude_process_id: int | None = None) -> list[WindowInfo]:
    """List visible, titled windows without activating any window."""
    items = _WindowsAPI().list_windows()
    return sorted((item for item in items if item.process_id != exclude_process_id),
                  key=lambda item: (item.title.casefold(), item.hwnd))


def get_foreground_window() -> WindowInfo:
    api = _WindowsAPI()
    return api.window_info(api.foreground_window())


class WindowsInput:
    """Own and release only the keys pressed by this playback backend.

    ``_api`` is an internal seam for tests, so tests never send real input.
    A partial or failed press conservatively records every attempted key for
    cleanup, because Windows does not guarantee which keys reached a target.
    """

    def __init__(self, *, emergency_corner: bool = True, _api: Any = None) -> None:
        self._api = _api if _api is not None else _WindowsAPI()
        self.emergency_corner = emergency_corner
        self._owned_keys: set[str] = set()

    @property
    def owned_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._owned_keys, key=KEY_ORDER.__getitem__))

    def foreground_window(self) -> int:
        return self._api.foreground_window()

    def held_modifiers(self) -> tuple[str, ...]:
        modifiers = ((0x11, "Ctrl"), (0x10, "Shift"), (0x12, "Alt"),
                     (0x5B, "Windows"), (0x5C, "Windows"))
        return tuple(dict.fromkeys(name for vk, name in modifiers
                                   if self._api.async_key_state(vk) & 0x8000))

    def stop_requested(self) -> bool:
        # Only the high bit means currently held; the low transition bit is
        # explicitly unreliable according to GetAsyncKeyState documentation.
        if self._api.async_key_state(0x77) & 0x8000:  # F8
            return True
        if self.emergency_corner:
            x, y = self._api.cursor_position()
            return 0 <= x <= 1 and 0 <= y <= 1
        return False

    def send_keys(self, keys: tuple[str, ...], down: bool) -> None:
        if any(key not in SCAN_CODES for key in keys):
            raise WindowsInputError("曲谱中包含不支持的按键。")
        unique = tuple(dict.fromkeys(keys))
        if not down:
            unique = tuple(key for key in unique if key in self._owned_keys)
        if not unique:
            return
        if down:
            self._owned_keys.update(unique)
        inputs: list[INPUT] = []
        for key in unique:
            item = INPUT()
            item.type = 1  # INPUT_KEYBOARD
            item.ki = KEYBDINPUT(0, SCAN_CODES[key], 0x0008 | (0 if down else 0x0002), 0, 0)
            inputs.append(item)
        try:
            sent = self._api.send_inputs(inputs)
        except Exception as error:
            raise WindowsInputError(f"Windows 键盘输出失败：{error}") from error
        if sent != len(inputs):
            code = self._api.last_error()
            raise WindowsInputError(
                f"Windows 只接受了 {sent}/{len(inputs)} 个按键事件（错误码 {code}），演奏已停止。"
                "请确认 LyreBot 与目标程序的权限级别相同（例如都以管理员身份运行）。"
            )
        if not down:
            self._owned_keys.difference_update(unique)

    def release_all(self) -> None:
        # Individual attempts ensure one failed release does not prevent the
        # remaining keys from being released.
        failures: list[str] = []
        for key in self.owned_keys:
            try:
                self.send_keys((key,), False)
            except Exception:
                failures.append(key.upper())
        if failures:
            raise WindowsInputError("Windows 无法松开以下按键：" + "、".join(failures) + "。")
