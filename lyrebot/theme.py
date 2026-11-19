"""LyreBot's look: a study's paper and ink, a little old gold, and Claude's orange for what matters most.

The colours, the fonts (a Song-style serif for titles where one is installed,
Georgia for figures), the images drawn by ``icons`` (kept on disk, so a start
draws nothing it drew before) and the touches Windows 11 lends a window: a
title bar the colour of the paper, and rounded corners with a gold edge and a
soft shadow for the menus and tips drawn here.
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
HIGHLIGHT = "#F3E6DA"  # the chosen song
HOVER = "#F1ECE2"  # under the pointer
WARN = "#B5502F"
WARN_DEEP = "#963F23"
WHITE = "#FFFFFF"

SANS = "Microsoft YaHei UI"
SERIFS = ("Noto Serif SC SemiBold", "Source Han Serif SC SemiBold", "Source Han Serif CN SemiBold",
          "Noto Serif CJK SC SemiBold", "Noto Serif SC Medium", "Noto Serif SC", "Source Han Serif SC")
FIGURES = ("Georgia", "Constantia", "Palatino Linotype", "Cambria")

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
        self.shelf = heading(10)  # OST and 歌曲
        self.title = heading(12)  # the song in the player, and a dialog's question
        self.figures = (figures, 9)  # times, bars and counts
        self.figures_small = (figures, 8)


class Theme:
    """Everything one window needs to look the part, made once per window."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self.scale = root.winfo_fpixels("1i") / 96
        self.fonts = Fonts(root)
        self.images: dict[tuple, tk.PhotoImage] = {}
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

    def icon(self, name: str, size: int, color: str, draw: Callable[[int], object]) -> tk.PhotoImage:
        """A one-colour icon: ``draw(size)`` gives its shape or shapes."""
        def layers() -> icons.Layers:
            shapes = draw(size)
            shapes = shapes if isinstance(shapes, (list, tuple)) else [shapes]
            return [(color, shape) for shape in shapes]
        return self.image(f"{name}:{color}", size, size, layers)

    def pill(self, width: int, height: int, fill: str, border: str) -> tk.PhotoImage:
        line = max(1.0, self.scale)
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


# ---- Windows 11 dress ----------------------------------------------------------

DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR, DWMWA_WINDOW_CORNER_PREFERENCE = 34, 35, 36, 33
ROUND, ROUND_SMALL = 2, 3


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


def round_corners(window: tk.Misc, border: str = GOLD, small: bool = False) -> bool:
    """Rounded corners, an edge of ``border`` and a soft shadow for a borderless window, where Windows
    can draw them (Windows 11); False where the window must draw its own edge."""
    if not _windows_11():
        return False
    window.update_idletasks()
    return _set(window, DWMWA_WINDOW_CORNER_PREFERENCE, ROUND_SMALL if small else ROUND) and \
        _set(window, DWMWA_BORDER_COLOR, _colorref(border))
