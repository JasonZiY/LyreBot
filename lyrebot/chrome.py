"""The main window's own frame (Windows 11): LyreBot draws the title bar, Windows still does the rest.

Tk cannot draw into a window's title bar, so the window gives its system frame up
(``overrideredirect``, which Tk then counts as having no border at all) and takes back from
Windows what that frame did for it: a taskbar button and a place in Alt+Tab
(``WS_EX_APPWINDOW``), edges that resize and snap to the side of a screen (``WS_THICKFRAME``),
minimising (``WS_MINIMIZEBOX``), the system menu, and Windows 11's rounded corners and shadow.
The non-client area is kept empty (``WM_NCCALCSIZE``), so the whole window is Tk's, just as Tk
believes.  There is no maximise: a narrow player has no use for the whole screen.

Where the pointer is decides what a press does (``WM_NCHITTEST``): near an edge it resizes, on
the title bar it moves the window, on the bar's buttons and everywhere else Tk has it.  Tk's own
windows lie over the frame and are asked first, so the ones that reach the edges or the bar are
told to stand aside (``HTTRANSPARENT``) wherever the frame should answer.

The window procedures run inside Tk's handling of a message and must not call back into Tk
(tkinter's thread bookkeeping would come undone): they read only the plain numbers in ``Frame``,
which the Tk side keeps up to date.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import tkinter as tk
from typing import Callable, Iterable

HTTRANSPARENT, HTCLIENT, HTCAPTION = -1, 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = range(10, 18)

WM_NCDESTROY, WM_NCCALCSIZE, WM_NCHITTEST, WM_SYSCOMMAND = 0x0082, 0x0083, 0x0084, 0x0112
WM_NCLBUTTONDOWN, WM_NCRBUTTONDOWN = 0x00A1, 0x00A4
SC_MINIMIZE, SC_MAXIMIZE = 0xF020, 0xF030
GWL_EXSTYLE, GWL_STYLE, GWLP_WNDPROC, GWLP_HWNDPARENT = -20, -16, -4, -8
WS_CAPTION, WS_SYSMENU, WS_THICKFRAME = 0x00C00000, 0x00080000, 0x00040000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
SW_HIDE, SW_SHOW = 0, 5

Box = tuple[int, int, int, int]  # left, top, right, bottom


class Frame:
    """The window's edges and title bar, in pixels from its top left corner."""

    def __init__(self, edge: int, caption: int):
        self.edge = edge  # how far in from each side a press resizes the window
        self.caption = caption  # the title bar's height: a press there moves the window
        self.buttons: list[Box] = []  # on the title bar, but Tk's to press
        self.hwnd = 0  # the window Windows sees (Tk's wrapper), once adopted
        self.presses = 0  # presses on the title bar and the edges, which Windows has and Tk never hears of

    def hit(self, x: int, y: int, width: int, height: int) -> int:
        """What a press at (x, y) does in a window ``width`` by ``height``: one of the HT codes."""
        if any(left <= x < right and top <= y < bottom for left, top, right, bottom in self.buttons):
            return HTCLIENT
        edge, corner = self.edge, 2 * self.edge
        west, east = x < corner, x >= width - corner
        if y < edge:
            return HTTOPLEFT if west else HTTOPRIGHT if east else HTTOP
        if y >= height - edge:
            return HTBOTTOMLEFT if west else HTBOTTOMRIGHT if east else HTBOTTOM
        north, south = y < corner, y >= height - corner
        if x < edge:
            return HTTOPLEFT if north else HTBOTTOMLEFT if south else HTLEFT
        if x >= width - edge:
            return HTTOPRIGHT if north else HTBOTTOMRIGHT if south else HTRIGHT
        return HTCAPTION if y < self.caption else HTCLIENT

    def at(self, lparam: int) -> int:
        """``hit`` for the screen point packed in a WM_NCHITTEST's lParam."""
        x, y = ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value
        box = wintypes.RECT()
        if not _user32.GetWindowRect(self.hwnd, ctypes.byref(box)):
            return HTCLIENT
        return self.hit(x - box.left, y - box.top, box.right - box.left, box.bottom - box.top)

    def minimise(self) -> None:
        """Down to the taskbar, as the system's own button would (Tk will not iconify a window without
        a frame)."""
        if self.hwnd:
            _user32.PostMessageW(self.hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)


def available() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)  # its own function objects: argtypes stay ours
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    _user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    _user32.CallWindowProcW.argtypes = (ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                        wintypes.LPARAM)
    _user32.CallWindowProcW.restype = LRESULT
    _user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    _user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.UINT)
    _user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
else:  # pragma: no cover - Windows only
    _user32 = None

# Window procedures live as long as the process: Windows may still call one while its window is
# being torn down, and freeing it from inside its own call would pull the floor from under it.
_procedures: list = []


def _subclass(hwnd: int, handle: Callable[[int, int, int], int | None]) -> None:
    """Put ``handle(message, wparam, lparam)`` in front of the window's procedure: what it returns is
    the answer, None passes the message on as before, and ``("pass", wparam, lparam)`` passes it on
    with those instead."""
    previous = _user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)
    if not previous:
        raise OSError("no window procedure to follow")

    def procedure(window, message, wparam, lparam):
        try:
            answer = handle(message, wparam or 0, lparam or 0)
        except Exception:  # never let an error escape into Windows: carry on as before
            answer = None
        if isinstance(answer, tuple):
            _, wparam, lparam = answer
        elif answer is not None:
            return answer
        return _user32.CallWindowProcW(previous, window, message, wparam, lparam)

    callback = WNDPROC(procedure)
    _procedures.append(callback)
    _user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p).value)


def stand_aside(frame: Frame, widgets: Iterable[tk.Misc]) -> None:
    """Have ``widgets`` (pieces that reach the window's edges or its title bar) leave presses there to the
    frame.  A piece made again, when the window is drawn anew, is told again."""
    def aside(message: int, _: int, lparam: int) -> int | None:
        if message == WM_NCHITTEST and frame.at(lparam) != HTCLIENT:
            return HTTRANSPARENT
        return None

    for widget in widgets:
        _subclass(widget.winfo_id(), aside)


def adopt(window: tk.Tk | tk.Toplevel, frame: Frame, see_through: Iterable[tk.Misc] = (),
          owner: tk.Misc | None = None) -> bool:
    """Give ``window`` its own frame, laid out by ``frame``; ``see_through`` are the widgets that reach
    the window's edges or its title bar.  Call it before the window is first shown.  False, with the
    window as it was, where it cannot be done.

    With an ``owner`` it is a dialog: kept over its owner's window and out of the taskbar, and neither
    resized nor minimised.  (Tk gives a window without a frame no owner of its own, so Windows is told.)"""
    if not available():
        return False
    try:
        window.withdraw()
        window.overrideredirect(True)
        window.update_idletasks()
        hwnd = int(window.wm_frame(), 16)
        if hwnd == window.winfo_id():  # no wrapper yet: Windows would not see the change
            window.deiconify()
            window.update_idletasks()
            hwnd = int(window.wm_frame(), 16)
            _user32.ShowWindow(hwnd, SW_HIDE)
        style = _user32.GetWindowLongPtrW(hwnd, GWL_STYLE) | WS_CAPTION | WS_SYSMENU
        extended = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & ~WS_EX_TOOLWINDOW
        if owner is None:  # a window of its own: a taskbar button, edges to size it by, minimising
            style, extended = style | WS_THICKFRAME | WS_MINIMIZEBOX, extended | WS_EX_APPWINDOW
        else:
            style &= ~(WS_THICKFRAME | WS_MINIMIZEBOX)
            _user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, int(owner.wm_frame(), 16))
        style &= ~WS_MAXIMIZEBOX
        frame.hwnd = hwnd

        def own(message: int, wparam: int, lparam: int) -> int | None:
            if message == WM_NCCALCSIZE:
                return 0  # all of the window is client: no system frame anywhere
            if message == WM_NCHITTEST:
                return frame.at(lparam)
            if message in (WM_NCLBUTTONDOWN, WM_NCRBUTTONDOWN):
                frame.presses += 1  # (and Windows goes on to move, size or show the menu)
            if message == WM_SYSCOMMAND and wparam & 0xFFF0 == SC_MAXIMIZE:
                return 0  # no maximising (Win+Up, the system menu): a frameless window would spill off the screen
            return None

        _subclass(hwnd, own)
        stand_aside(frame, see_through)
        _user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)
        _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, extended)
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    except (AttributeError, OSError, ValueError, tk.TclError):
        frame.hwnd = 0
        try:
            window.overrideredirect(False)
            window.deiconify()
        except tk.TclError:
            pass
        return False
    window.deiconify()
    _user32.ShowWindow(hwnd, SW_SHOW)  # a shown window keeps its taskbar button as it was: show it anew
    return True
