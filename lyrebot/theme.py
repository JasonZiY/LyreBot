"""LyreBot's look: a study's paper and ink, a little old gold, and Claude's orange for what matters most.

The colours, the fonts (a Song-style serif for titles where one is installed,
Georgia for figures), the images drawn by ``icons`` (kept on disk, so a start
draws nothing it drew before) and the touches Windows 11 lends a window: a
title bar the colour of the paper for the dialogs, and rounded corners with a
gold edge and a soft shadow for the menus and tips drawn here.  The main
window's title bar is drawn by LyreBot itself (``chrome``), in leather and gilt.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

from . import icons

PAPER = "#FAF7F0"  # the page
INK = "#2B2722"  # what is written on it
MUTED = "#8A8174"  # what is said in passing
FAINT = "#C9BFAE"  # what cannot be used now
HAIR = "#E3D6BC"  # fine rules
GOLD = "#B08D57"  # old gold: chevrons, ornaments, the edge of a menu
GOLD_SOFT = "#D2BC93"  # a gold rule that has to stay quiet
ORANGE = "#D97757"  # play, and the song that plays
ORANGE_DEEP = "#C15F3C"
PEACH = "#F0D3C4"
PEACH_DEEP = "#E6B49C"  # the text's selection while a line is marked WRONG, to stand apart from it
HIGHLIGHT = "#F3E6DA"  # the chosen song
HOVER = "#F1ECE2"  # under the pointer
WARN = "#B5502F"
WARN_DEEP = "#963F23"
WHITE = "#FFFFFF"

# The title bar is the book's cover, the dark leather of the program icon, stamped in gold.
LEATHER_TOP, LEATHER_FOOT = "#4A3B31", "#3A2E27"  # the leather lit from above
LEATHER_EDGE = "#2E241E"  # where the cover meets the page
LEATHER_HOVER = "#5E4C40"  # a button's leather under the pointer
GILT = "#D9BC82"  # gold stamped into leather
GILT_BRIGHT = "#E8D2A0"  # the title, where the gold catches the light
GILT_DEEP = "#B58F50"  # the fillet along the cover's foot
EMBER = "#E8A386"  # a warning in the gold's company

# On the main window's page, which changes colour from head to foot (``paper``), grounds are tints that
# let the paper show through rather than colours of their own.
VEIL = "#FFFFFF66"  # a button's face: a slip of lighter paper
VEIL_HOVER = "#FFFFFFB3"
VEIL_FAINT = "#FFFFFF33"
CHOSEN_TINT = "#D9775729"  # the chosen song: a breath of orange
HOVER_TINT = "#8A81741A"  # under the pointer: a shade

# What the page itself carries (``App.dress_page``): a double rule of gold framing it, the lyre pressed so
# faintly that it is more felt than seen, and the program icon's orange ribbon lying over it.
FRAME_OUTER, FRAME_INNER = "#B08D57D7", "#D2BC93E6"
WATERMARK = "#B08D5712"
RIBBON, RIBBON_FOLD, RIBBON_TUCK = ORANGE, "#C7684A", "#96513A"
RIBBON_SHADE, RIBBON_SHADE_FAR = "#5A322038", "#5A32201A"
# The second ribbon, on the 简谱 and text pages, in a deep green that keeps company with the old gold.
GREEN, GREEN_FOLD, GREEN_TUCK = "#5E8573", "#4C6E60", "#3A5548"
WRONG = "#F4DCD3"  # under a line of the score that cannot be read

SANS = "Microsoft YaHei UI"
SERIFS = ("Noto Serif SC SemiBold", "Source Han Serif SC SemiBold", "Source Han Serif CN SemiBold",
          "Noto Serif CJK SC SemiBold", "Noto Serif SC Medium", "Noto Serif SC", "Source Han Serif SC")
FIGURES = ("Georgia", "Constantia", "Palatino Linotype", "Cambria")
MONOS = ("Cascadia Mono", "Consolas", "Courier New")  # for writing a score: its keys and marks line up

# Drawn images are kept on disk under the hash of the code that draws them: changed code, a fresh folder.
_DRAWING = hashlib.sha1(b"".join(path.read_bytes() for path in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()[:12]
CACHE_ROOT = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "LyreBot" / "images"
CACHE = CACHE_ROOT / _DRAWING


class Fonts:
    """The fonts LyreBot writes in, picked from those installed."""

    def __init__(self, root: tk.Misc):
        families = set(tkfont.families(root))
        serif = next((name for name in SERIFS if name in families), None)
        figures = next((name for name in FIGURES if name in families), SANS)
        self.serif = serif

        def heading(size: int) -> tuple:
            return (serif, size) if serif else (SANS, size, "bold")
        self.body = (SANS, 9)
        self.small = (SANS, 8)
        self.shelf = heading(10)  # OST and 乐曲
        self.title = heading(12)  # the song in the player, and a dialog's question
        self.figures = (figures, 9)  # times, bars and counts
        self.figures_small = (figures, 8)
        self.mono = (next((name for name in MONOS if name in families), "Courier New"), 10)


class Theme:
    """Everything one window needs to look the part, made once per window."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self.scale = root.winfo_fpixels("1i") / 96
        self.fonts = Fonts(root)
        self.images: dict[tuple, tk.PhotoImage] = {}
        self.page = None  # the window's paper, where it has one (``paper.Page``)
        root.lyrebot_theme = self  # type: ignore[attr-defined]

    @staticmethod
    def of(widget: tk.Misc) -> "Theme":
        top = widget.winfo_toplevel()
        for holder in (widget, top, getattr(top, "master", None)):
            theme = getattr(holder, "lyrebot_theme", None)
            if theme is not None:
                return theme
        return Theme(top)

    def px(self, n: float) -> int:
        return max(1, round(n * self.scale))

    def measure(self, font: tuple, text: str) -> int:
        return int(self.root.tk.call("font", "measure", font, text))

    def image(self, key: str, width: int, height: int, layers: Callable[[], icons.Layers]) -> tk.PhotoImage:
        """The picture ``layers`` draw at this size, from memory, from disk, or drawn now."""
        memo = (key, width, height)
        if memo not in self.images:
            name = hashlib.sha1(f"{_DRAWING}|{key}|{width}x{height}".encode()).hexdigest()[:20] + ".png"
            data = None
            try:
                data = (CACHE / name).read_bytes()
            except OSError:
                pass
            if not data:
                data = icons.png(width, height, icons.rgba(width, height, layers()))
                try:
                    if not CACHE.is_dir():
                        _forget_old_drawings()
                    CACHE.mkdir(parents=True, exist_ok=True)
                    (CACHE / name).write_bytes(data)
                except OSError:
                    pass
            self.images[memo] = tk.PhotoImage(master=self.root, data=base64.b64encode(data).decode("ascii"))
        return self.images[memo]

    def icon(self, name: str, size: int, color: str, draw: Callable[[int], object],
             gold: str | None = None) -> tk.PhotoImage:
        """An icon: ``draw(size)`` gives its parts, its lines painted ``color`` and its gilt ``gold`` (or
        ``color`` too)."""
        return self.image(f"{name}:{color}:{gold or color}", size, size,
                          lambda: icons.paint(draw(size), color, gold))  # type: ignore[arg-type]

    def fleuron(self, height: int, color: str) -> tk.PhotoImage:
        """·◆· in ``color``, ``height`` tall: the ornament set into a rule."""
        width = icons.fleuron_width(height)
        return self.image(f"fleuron:{color}", width, height, lambda: icons.paint(icons.fleuron(width, height), color))

    def frame(self) -> tuple[int, int, dict[str, tk.PhotoImage]]:
        """The double rule framing the page, in pieces: each corner ("nw", "ne", "sw", "se") in a square of
        its own, and a short length of each edge ("n", "s", "w", "e") to be laid along it, over and over;
        with the side of a corner's square and how deep the rules lie from the edge."""
        layers, side, depth = icons.frame(self.scale, FRAME_OUTER, FRAME_INNER)
        run = 16  # the length of edge in each piece
        views = {"nw": (side, side, {}), "ne": (side, side, {"width": side}), "sw": (side, side, {"height": side}),
                 "se": (side, side, {"width": side, "height": side}), "n": (run, depth, {"dx": side}),
                 "s": (run, depth, {"dx": side, "height": depth}), "w": (depth, run, {"dy": side}),
                 "e": (depth, run, {"dy": side, "width": depth})}
        pieces = {name: self.image(f"frame:{name}:{FRAME_OUTER}:{FRAME_INNER}:{self.scale:.4f}", width, height,
                                   lambda view=view: icons.placed(layers, **view))  # type: ignore[arg-type]
                  for name, (width, height, view) in views.items()}
        return side, depth, pieces

    def watermark(self, size: int) -> tk.PhotoImage:
        """The lyre, pressed into the paper."""
        return self.image(f"watermark:{WATERMARK}", size, size, lambda: icons.paint(icons.lyre(size), WATERMARK))

    def ribbon(self, extra: int = 0, green: bool = False) -> tk.PhotoImage:
        """A bookmark's ribbon, with its shadow, orange or ``green``; ``extra`` pixels longer (or shorter) while
        it is tugged."""
        px = self.px
        band, length, notch, drop = px(10.5), px(37) + extra, px(5), px(1.5)
        body, fold, tuck = (GREEN, GREEN_FOLD, GREEN_TUCK) if green else (RIBBON, RIBBON_FOLD, RIBBON_TUCK)
        key = ("green-ribbon" if green else "ribbon") + (f":{extra}" if extra else "")
        return self.image(key, band + drop + 1, length + drop + 1,
                          lambda: icons.ribbon(band, length, notch, drop, body, fold, tuck, RIBBON_SHADE,
                                               RIBBON_SHADE_FAR))

    def pill(self, width: int, height: int, fill: str, border: str) -> tk.PhotoImage:
        line = max(1, round(self.scale))  # whole pixels: an edge of 1.25 smears over two rows
        return self.image(f"pill:{fill}:{border}:{line}", width, height, lambda: icons.pill(width, height, fill, border, line))


def _forget_old_drawings() -> None:
    """Remove images kept for code that has changed since."""
    if not CACHE_ROOT.is_dir():
        return
    for folder in CACHE_ROOT.iterdir():
        if folder.is_dir() and folder.name != _DRAWING:
            for file in folder.glob("*.png"):
                try:
                    file.unlink()
                except OSError:
                    pass
            try:
                folder.rmdir()
            except OSError:
                pass


# ---- screens -------------------------------------------------------------------

class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _Rect), ("rcWork", _Rect), ("dwFlags", ctypes.c_ulong)]


MONITOR_DEFAULTTONEAREST = 2

Area = tuple[int, int, int, int]  # left, top, right, bottom, in the screen's pixels


def screen_area(widget: tk.Misc, x: float, y: float) -> Area:
    """The part of the screen at (x, y), or the nearest one, that windows may use: less the taskbar.

    A second screen to the left of or above the main one lies at negative
    coordinates, and may be a different size, so a popup is placed by the
    screen it opens on rather than by the main one."""
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = [_Point, ctypes.c_ulong]
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MonitorInfo)]
            monitor = user32.MonitorFromPoint(_Point(round(x), round(y)), MONITOR_DEFAULTTONEAREST)
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                work = info.rcWork
                return work.left, work.top, work.right, work.bottom
        except (AttributeError, OSError):
            pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


MONITOR_DEFAULTTONULL = 0


def screen_holding(left: int, top: int, right: int, bottom: int) -> Area | None:
    """The work area of the screen holding most of a box, or None when no screen holds any of it (the screen
    it was on has been taken away)."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromRect.argtypes = [ctypes.POINTER(_Rect), ctypes.c_ulong]
        user32.MonitorFromRect.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MonitorInfo)]
        monitor = user32.MonitorFromRect(ctypes.byref(_Rect(left, top, right, bottom)), MONITOR_DEFAULTTONULL)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
    except (AttributeError, OSError):
        return None
    work = info.rcWork
    return work.left, work.top, work.right, work.bottom


def fit(width: int, height: int, x: int, y: int, area: Area, left_instead: int | None = None,
        bottom_instead: int | None = None) -> tuple[int, int]:
    """Where a popup of this size goes so that it stays whole on its screen: at (x, y) if it fits;
    else at ``left_instead`` across and with its bottom at ``bottom_instead``, if those fit; else
    moved in from the screen's edge."""
    left, top, right, bottom = area
    if x + width > right:
        x = left_instead if left_instead is not None and left_instead >= left else right - width
    if y + height > bottom:
        y = bottom_instead - height if bottom_instead is not None and bottom_instead - height >= top else bottom - height
    return max(x, left), max(y, top)


def geometry(x: int, y: int, width: int | None = None, height: int | None = None) -> str:
    """A Tk geometry string, which takes negative positions as "+-200"."""
    size = f"{width}x{height}" if width is not None and height is not None else ""
    return f"{size}+{int(x)}+{int(y)}"


# ---- Windows 11 dress ----------------------------------------------------------

DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR, DWMWA_WINDOW_CORNER_PREFERENCE = 34, 35, 36, 33
ROUND, ROUND_SMALL = 2, 3
NO_COLOR = 0xFFFFFFFE  # DWMWA_COLOR_NONE: no edge drawn at all


def _windows_11() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


def _colorref(color: str) -> int:
    red, green, blue = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return red | (green << 8) | (blue << 16)


def _set(window: tk.Misc, attribute: int, value: int) -> bool:
    try:
        hwnd = int(window.wm_frame(), 16)
        number = ctypes.c_int(value)
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(number),
                                                          ctypes.sizeof(number)) == 0
    except (AttributeError, OSError, ValueError, tk.TclError):
        return False


def dress(window: tk.Misc, caption: str = PAPER, text: str = INK, border: str = HAIR) -> bool:
    """Colour a window's title bar and edge to match the page (Windows 11; elsewhere nothing changes)."""
    if not _windows_11():
        return False
    window.update_idletasks()
    done = _set(window, DWMWA_CAPTION_COLOR, _colorref(caption))
    _set(window, DWMWA_TEXT_COLOR, _colorref(text))
    _set(window, DWMWA_BORDER_COLOR, _colorref(border))
    return done


def round_corners(window: tk.Misc, border: str | None = GOLD, small: bool = False) -> bool:
    """Rounded corners, an edge of ``border`` (None: no edge) and a soft shadow for a borderless window,
    where Windows can draw them (Windows 11); False where the window must draw its own edge."""
    if not _windows_11():
        return False
    window.update_idletasks()
    return _set(window, DWMWA_WINDOW_CORNER_PREFERENCE, ROUND_SMALL if small else ROUND) and \
        _set(window, DWMWA_BORDER_COLOR, NO_COLOR if border is None else _colorref(border))
