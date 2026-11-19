"""Typing Chinese into the search field: what the input method is composing shows in the field itself, as
the letters go in, rather than in a white box of Windows' own.

The words of a ``PageLine`` are drawn on a canvas, which Windows does not know as a place to type, so left
alone the input method shows what it is composing in a small white window of its own under the field.
Instead the main window tells Windows that it draws compositions itself (``ISC_SHOWUICOMPOSITIONWINDOW``
taken out of ``WM_IME_SETCONTEXT``, and the start and progress of a composition kept from Windows' default
handling), reads what is being composed, and puts the list of candidates just under the field.  What the
input method finally chooses arrives, as before, as key presses.

As in ``chrome``, the window procedure must not call back into Tk: it reads and writes only the plain
values of a ``Composition``, which the field looks at a few dozen times a second while it has the keyboard.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import tkinter as tk

from . import chrome

WM_IME_STARTCOMPOSITION, WM_IME_ENDCOMPOSITION, WM_IME_COMPOSITION = 0x010D, 0x010E, 0x010F
WM_IME_SETCONTEXT = 0x0281
ISC_SHOWUICOMPOSITIONWINDOW = 0x80000000
GCS_COMPSTR, GCS_RESULTSTR = 0x0008, 0x0800
CFS_POINT, CFS_EXCLUDE = 0x0002, 0x0080


class Composition:
    """What the input method is composing in one window, and where the field it goes into stands there."""

    def __init__(self) -> None:
        self.active = False  # a field of this window has the keyboard
        self.text = ""  # being composed
        self.version = 0  # counts the changes of ``text``, for the field to notice
        self.caret = (0, 0, 0)  # the field's cursor in the window: x, its top, its foot
        self.area = (0, 0, 0, 0)  # the field, for the candidates to keep clear of

    def show(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self.version += 1

    def handle(self, message: int, wparam: int, lparam: int, context: "Context") -> int | tuple | None:
        """What the window does with an input-method message (see ``chrome._subclass`` for the answers)."""
        if message == WM_IME_SETCONTEXT:  # compositions are drawn here, by the field
            return ("pass", wparam, lparam & ~ISC_SHOWUICOMPOSITIONWINDOW)
        if not self.active:
            return None
        if message == WM_IME_STARTCOMPOSITION:
            context.place(self.caret, self.area)
            return 0  # kept from Windows, which would open its own window
        if message == WM_IME_COMPOSITION:
            if lparam & GCS_COMPSTR:
                self.show(context.composing())
            context.place(self.caret, self.area)
            if lparam & GCS_RESULTSTR:
                return None  # Tk turns what was chosen into key presses
            return 0
        if message == WM_IME_ENDCOMPOSITION:
            self.show("")
            return 0
        return None


if sys.platform == "win32":
    _imm = ctypes.WinDLL("imm32", use_last_error=True)
    _imm.ImmGetContext.argtypes = (wintypes.HWND,)
    _imm.ImmGetContext.restype = ctypes.c_void_p
    _imm.ImmReleaseContext.argtypes = (wintypes.HWND, ctypes.c_void_p)
    _imm.ImmGetCompositionStringW.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    _imm.ImmGetCompositionStringW.restype = ctypes.c_long
    _imm.ImmSetCandidateWindow.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _imm.ImmSetCompositionWindow.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
else:  # pragma: no cover - Windows only
    _imm = None


class _CandidateForm(ctypes.Structure):
    _fields_ = [("dwIndex", wintypes.DWORD), ("dwStyle", wintypes.DWORD), ("ptCurrentPos", wintypes.POINT),
                ("rcArea", wintypes.RECT)]


class _CompositionForm(ctypes.Structure):
    _fields_ = [("dwStyle", wintypes.DWORD), ("ptCurrentPos", wintypes.POINT), ("rcArea", wintypes.RECT)]


class Context:
    """The input method's side of one window, as the window procedure may ask it."""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd

    def composing(self) -> str:
        context = _imm.ImmGetContext(self.hwnd)
        if not context:
            return ""
        try:
            size = _imm.ImmGetCompositionStringW(context, GCS_COMPSTR, None, 0)
            if size <= 0:
                return ""
            text = ctypes.create_unicode_buffer(size // 2 + 1)
            _imm.ImmGetCompositionStringW(context, GCS_COMPSTR, text, size)
            return text.value[:size // 2]
        finally:
            _imm.ImmReleaseContext(self.hwnd, context)

    def place(self, caret: tuple[int, int, int], area: tuple[int, int, int, int]) -> None:
        """Candidates under the field's cursor, clear of the field; and, for an input method that draws its
        own composition anyway, that at the cursor."""
        context = _imm.ImmGetContext(self.hwnd)
        if not context:
            return
        try:
            x, top, foot = caret
            candidates = _CandidateForm(0, CFS_EXCLUDE, wintypes.POINT(x, foot), wintypes.RECT(*area))
            _imm.ImmSetCandidateWindow(context, ctypes.byref(candidates))
            composing = _CompositionForm(CFS_POINT, wintypes.POINT(x, top), wintypes.RECT(*area))
            _imm.ImmSetCompositionWindow(context, ctypes.byref(composing))
        finally:
            _imm.ImmReleaseContext(self.hwnd, context)


def attach(window: tk.Misc) -> Composition | None:
    """The composition of ``window`` (a toplevel), watched from its window procedure, one however many
    fields it has; None off Windows."""
    found = getattr(window, "lyrebot_composition", None)
    if found is not None or _imm is None or chrome._user32 is None:
        return found
    try:
        hwnd = window.winfo_id()  # Tk keeps the keyboard on this, the toplevel's own window
        composition, context = Composition(), Context(hwnd)
        chrome._subclass(hwnd, lambda message, wparam, lparam: composition.handle(message, wparam, lparam, context))
    except (AttributeError, OSError, tk.TclError):
        return None
    window.lyrebot_composition = composition  # type: ignore[attr-defined]
    return composition
