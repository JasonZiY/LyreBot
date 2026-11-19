"""The page the main window is written on: paper lit by a desk lamp at its head, going over to the pink
and mauve of an evening sky at its foot, with a fine grain, a little clouding and a few fibres.

Tk's widgets cannot be see-through, so every part of the window that shows the page is a canvas laying
its own slice of one picture of the paper under what it draws (``OnPage``).  The picture is made of:

- the wash: the colours, and the clouding in them.  They change slowly, so the wash is drawn at an
  eighth of the window's size (some 10 ms in pure Python) and enlarged by Tk.  It runs on a little
  past the window's right and foot, so a window being dragged larger keeps paper under it; it is
  washed anew when the drag outgrows that margin, and once more for the size the window settles at;
- the grain: a fine grain and a few fibres, darkening the wash by a level or two.  It does not depend
  on the window's size, so it is one square tile, drawn once and kept on disk beside the icons, which
  Tk lays over the wash, tile by tile from the window's top left corner, as the picture is made.

The picture is opaque, so the canvases only copy it to the screen when they repaint: a picture with
see-through pixels would be blended afresh on every repaint, which made dragging the window slow.  The
clouding and the grain belong to the window's coordinates, not its size, so they keep still while it is
resized.  The search field cannot lie on the page (a real Entry is solid), so the head of the
page, where it sits, is one even colour, which the field takes (``Page.head``).
"""

from __future__ import annotations

import base64
import math
import random
import struct
import time
import tkinter as tk
import zlib

from . import theme

HEAD = (250, 234, 200)  # lamplight over the head of the page
CREAM = (250, 245, 236)  # the middle of the page, the paper itself
ROSE = (244, 219, 208)  # the evening at its foot
MAUVE = (234, 219, 230)  # the sky going violet, to the right of the foot
CLOUDING = 2.5  # how far the paper's clouding strays from the even colour, in levels
STEP = 8  # the wash is drawn at 1/STEP of the window's size
MARGIN = 96  # how far the wash runs on past the window's right and foot
TILE = 512  # the grain's square
GRAIN = 2  # the grain darkens each pixel by 1 to 1 + GRAIN levels (never 0: see ``grain``), which the wash
LIFT = 1 + GRAIN / 2  # adds back on average
FIBRE = 4  # and a fibre by this much more
QUICK = 0.05  # seconds: while the window is being dragged larger than its wash, a new wash at most this often
SETTLE = 120  # milliseconds after the last change of size: the wash for the size it has come to


def _smooth(low: float, high: float, x: float) -> float:
    t = min(max((x - low) / (high - low), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(round(v) for v in rgb)


def _row(y: float, height: int, flat: int) -> tuple[float, float, float]:
    """The page's colour across row ``y``, before the evening's mauve and the clouding."""
    lit = _smooth(flat, flat + 0.38 * height, y)  # out of the lamplight into the paper's own colour
    evening = _smooth(0.45, 1.0, y / max(1, height - 1))
    out = []
    for head, cream, rose in zip(HEAD, CREAM, ROSE):
        day = head + (cream - head) * lit
        out.append(day + (rose - day) * evening)
    return out[0], out[1], out[2]


def _violet(x: float, y: float, width: int, height: int) -> float:
    return _smooth(0.6, 1.0, y / max(1, height - 1)) * 0.8 * _smooth(0.2, 1.0, x / max(1, width - 1))


def colour(x: float, y: float, width: int, height: int, flat: int) -> str:
    """The page's even colour at (x, y): what a solid widget standing there should be."""
    base, weight = _row(y, height, flat), _violet(x, y, width, height)
    return _hex(tuple(b + (m - b) * weight for b, m in zip(base, MAUVE)))  # type: ignore[arg-type]


# Two sizes of soft blotch for the clouding, as fixed grids of values that wrap around: the clouds
# belong to the window's coordinates, so a resize does not stir them.
_CLOUDS = []
_random = random.Random(7)
for _cell, _weight in ((140, 0.7), (50, 0.3)):
    _CLOUDS.append((_cell, _weight, [[_random.uniform(-1.0, 1.0) for _ in range(32)] for _ in range(32)]))


def _png(width: int, height: int, raw: bytes, kind: int) -> bytes:
    """A PNG of ``raw`` rows (each led by its filter byte); ``kind`` 2 is RGB, 6 is RGBA."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, kind, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def wash(width: int, height: int, flat: int) -> bytes:
    """The colours of the page for a window ``width`` by ``height``, and a margin past it, at 1/STEP of
    the size, as an RGB PNG.  Its rows above ``flat`` are one colour.  What depends on the row alone or
    the column alone is worked out once for it, which keeps a wash to a few milliseconds."""
    across, down = -(-(width + MARGIN) // STEP), -(-(height + MARGIN) // STEP)
    xs = [(i + 0.5) * STEP for i in range(across)]
    toward_right = [_smooth(0.2, 1.0, x / max(1, width - 1)) for x in xs]
    spots = [[(int(x / cell) % 32, (int(x / cell) + 1) % 32, _ease(x / cell - int(x / cell))) for x in xs]
             for cell, _, _ in _CLOUDS]
    lift = LIFT  # what the grain takes away on average, given back here
    raw = bytearray()
    for j in range(down):
        y = (j + 0.5) * STEP
        red, green, blue = (value + lift for value in _row(y, height, flat))
        low = _smooth(0.6, 1.0, y / max(1, height - 1)) * 0.8
        dr, dg, db = (MAUVE[0] - red) * low, (MAUVE[1] - green) * low, (MAUVE[2] - blue) * low
        cloudy = CLOUDING * _smooth(flat, flat + 0.15 * height, y)
        shade = [0.0] * across
        if cloudy:
            for (cell, weight, grid), spot in zip(_CLOUDS, spots):
                fy = y / cell
                v = _ease(fy - int(fy))
                above, below = grid[int(fy) % 32], grid[(int(fy) + 1) % 32]
                line = [(a + (b - a) * v) * weight * cloudy for a, b in zip(above, below)]
                shade = [s + line[i] + (line[k] - line[i]) * u for s, (i, k, u) in zip(shade, spot)]
        red, green, blue = red + 0.5, green + 0.5, blue + 0.5
        row = bytearray(3 * across)
        row[0::3] = bytes(min(255, int(red + dr * k + s)) for k, s in zip(toward_right, shade))
        row[1::3] = bytes(min(255, int(green + dg * k + s)) for k, s in zip(toward_right, shade))
        row[2::3] = bytes(min(255, int(blue + db * k + s)) for k, s in zip(toward_right, shade))
        raw += b"\x00" + row
    return _png(across, down, bytes(raw), 2)


def _ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def grain(size: int = TILE, seed: int = 11) -> bytes:
    """The grain and fibres as a square RGBA PNG that tiles: black, each pixel as opaque as it darkens.

    No pixel is wholly clear: Tk keeps the clear pixels of an image as a region, and a region of a third
    of the pixels scattered at random takes Tk seconds to build."""
    rng = random.Random(seed)
    alpha = bytearray(1 + value % (GRAIN + 1) for value in rng.randbytes(size * size))
    for _ in range(size * size // 1400):
        x, y = rng.uniform(0, size), rng.uniform(0, size)
        heading = rng.uniform(0, math.pi)
        for _ in range(rng.randint(6, 26)):
            heading += rng.gauss(0, 0.18)
            x, y = (x + math.cos(heading)) % size, (y + math.sin(heading)) % size
            spot = int(y) * size + int(x)
            alpha[spot] = min(alpha[spot] + FIBRE, 255)
    pixels = bytearray(4 * size * size)
    pixels[3::4] = alpha
    stride = 4 * size
    raw = b"".join(b"\x00" + pixels[row * stride:(row + 1) * stride] for row in range(size))
    return _png(size, size, raw, 6)


def grain_tile() -> bytes:
    """The grain's tile, from disk, or drawn now and kept."""
    path = theme.CACHE / f"paper-grain-{TILE}.png"
    try:
        return path.read_bytes()
    except OSError:
        pass
    data = grain()
    try:
        theme.CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        pass
    return data


def _photo(root: tk.Misc, data: bytes) -> tk.PhotoImage:
    return tk.PhotoImage(master=root, data=base64.b64encode(data).decode("ascii"))


class Page:
    """The paper under one window, and the canvases that lay it."""

    def __init__(self, root: tk.Misc, flat: int):
        self.root, self.flat = root, flat
        self.head = _hex(HEAD)  # the even colour of the top ``flat`` rows
        self.surfaces: list[OnPage] = []
        self.image: tk.PhotoImage | None = None  # the paper, as the surfaces lay it
        self.grain: tk.PhotoImage | None = None
        self.size = (0, 0)
        self.job: str | None = None
        self.soon: str | None = None  # a wash for a window grown past this one's margin
        self.later: str | None = None  # the wash for the size the window settles at
        self.wanted = (0, 0)  # the window's size, as it last said
        self.washed_at = 0.0
        root.bind("<Configure>", self.moved, add="+")

    def lay(self, width: int, height: int) -> None:
        """Make the paper for a window of this size and lay it under every surface, now."""
        if self.grain is None:
            self.grain = _photo(self.root, grain_tile())
        image = _photo(self.root, wash(width, height, self.flat)).zoom(STEP)
        image.tk.call(image, "copy", self.grain, "-to", 0, 0, image.width(), image.height(),
                      "-compositingrule", "overlay")  # the grain, tiled over the wash
        old, self.image = self.image, image
        self.size, self.washed_at = (width, height), time.monotonic()
        self.refresh()
        del old  # only now, with nothing showing it any more

    def add(self, surface: "OnPage") -> None:
        self.surfaces.append(surface)
        surface.bind("<Configure>", lambda _: self.schedule(), add="+")
        self.schedule()

    def tone(self, x: float, y: float) -> str:
        width, height = self.size
        return colour(x, y, max(width, 1), max(height, 1), self.flat)

    def moved(self, event: tk.Event) -> None:
        """The window (or a part of it: a toplevel's bindings see its children's events too) changed.

        While it is being resized the wash it has stays, a little off in its colours but covering it,
        until the window grows past the wash's margin; once it holds still, it is washed for its size."""
        if event.widget is not self.root:
            return
        self.wanted = event.width, event.height
        if event.width > 1 and event.height > 1 and self.wanted != self.size:
            if self.later is not None:
                self.root.after_cancel(self.later)
            self.later = self.root.after(SETTLE, self.settled)
            outgrown = event.width > self.size[0] + MARGIN or event.height > self.size[1] + MARGIN
            if outgrown and self.soon is None:
                wait = max(QUICK - (time.monotonic() - self.washed_at), 0.0)
                self.soon = self.root.after(max(1, round(wait * 1000)), self.grown)
        self.schedule()

    def grown(self) -> None:
        self.soon = None
        self.catch_up()

    def settled(self) -> None:
        self.later = None
        self.catch_up()

    def catch_up(self) -> None:
        width, height = self.wanted
        if (width, height) != self.size and width > 1 and height > 1:
            self.lay(width, height)

    def schedule(self) -> None:
        if self.job is None:
            self.job = self.root.after_idle(self.refresh)

    def refresh(self) -> None:
        """Line every surface's slice up with where it now stands in the window."""
        self.job = None
        if self.image is None:
            return
        alive = []
        for surface in self.surfaces:
            try:
                if not surface.winfo_exists():
                    continue
                x, y = self.offset(surface)
                surface.lay_paper(self.image, x, y)
            except tk.TclError:
                continue
            alive.append(surface)
        self.surfaces = alive

    def offset(self, widget: tk.Misc) -> tuple[int, int]:
        """Where ``widget`` stands in the window, from the positions of it and its masters."""
        x = y = 0
        while widget is not None and widget is not self.root:
            x, y = x + widget.winfo_x(), y + widget.winfo_y()
            widget = widget.master
        return x, y


class OnPage:
    """For a canvas that shows its slice of the window's page under what it draws.  Its drawings clear with
    ``delete("!paper")``, which leaves the paper where it lies."""

    page: Page | None = None
    paper: tuple[tk.PhotoImage, int, int] | None = None
    laid: tuple[str, float, float] | None = None  # the image and place the paper item has

    def take_page(self, owner: "theme.Theme") -> None:
        """Lie on the page of the window this canvas is in, if that window has one."""
        page = getattr(owner, "page", None)
        if page is not None and page.root is self.winfo_toplevel():  # type: ignore[attr-defined]
            self.page = page
            page.add(self)

    def lay_paper(self, image: tk.PhotoImage, x: int, y: int) -> None:
        """The paper, with the window's top left corner at (-x, -y) of this canvas's view.  Left alone when
        it is already so: touching an item repaints the whole canvas."""
        self.paper = (image, x, y)
        canvas: tk.Canvas = self  # type: ignore[assignment]
        left, top = canvas.canvasx(0) - x, canvas.canvasy(0) - y
        found = canvas.find_withtag("paper")
        if found and self.laid == (str(image), left, top):
            return
        if found:
            canvas.coords(found[0], left, top)
            canvas.itemconfigure(found[0], image=image)
        else:
            canvas.create_image(left, top, anchor="nw", image=image, tags="paper")
            canvas.tag_lower("paper")
        self.laid = (str(image), left, top)

    def follow_page(self) -> None:
        """Keep the paper still under a canvas that has scrolled."""
        if self.paper is not None:
            self.lay_paper(*self.paper)
