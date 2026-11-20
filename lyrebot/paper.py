"""The page the main window is written on: paper lit by a desk lamp at its head, going over to the pink
and mauve of an evening sky at its foot, with a fine grain, a little clouding and a few fibres.

Tk's widgets cannot be see-through, so every part of the window that shows the page is a canvas laying
its own slice of one picture of the paper under what it draws (``OnPage``).  The picture is made of:

- the wash: the colours, and the clouding in them.  They change slowly across the page, faster down
  it, so the wash is drawn at an eighth of the window's width and a quarter of its height (some 10 ms
  in pure Python) and enlarged by Tk.  It runs on a little past the window's right and foot, so a
  window being dragged larger keeps paper under it.  The colours are laid out by the window's height,
  so while it is dragged taller or shorter the page is washed anew as it goes, the colours stretching
  with it; dragged only wider, it is washed when it outgrows the margin, and once more when it settles;
- the grain: a fine grain and a few fibres, darkening the wash by a level or two.  It does not depend
  on the window's size, so it is one square tile, drawn once and kept on disk beside the icons, which
  Tk lays over the wash, tile by tile from the window's top left corner, as the picture is made.

The picture is opaque, so the canvases only copy it to the screen when they repaint: a picture with
see-through pixels would be blended afresh on every repaint, which made dragging the window slow.  The
clouding and the grain belong to the window's coordinates, not its size, so they keep still while it is
resized.  The head of the page, under the title bar, is one even lamplight.

Two kinds of picture go with the paper.  Marks (``Page.press``: the gilt frame, the lyre's watermark) are
pressed into the picture itself, so they lie under everything written on the page and show through its
tints; when the window changes size they are taken out again, by putting back the paper kept from under
them, and pressed in where they now go.  What hangs over the page (``Page.hang``: the bookmark's ribbon)
is laid by each canvas it crosses over all that canvas draws.
"""

from __future__ import annotations

import base64
import math
import random
import struct
import time
import tkinter as tk
from typing import Callable, Optional
import zlib

from . import theme

# Where a picture goes on the page, for a window of this width and height: its top left corner, or a box
# (left, top, width, height) for it to be laid over again and again until it is filled; None while there is
# nowhere for it yet.
Place = Callable[[int, int], Optional[tuple[int, ...]]]

HEAD = (250, 234, 200)  # lamplight over the head of the page
CREAM = (250, 245, 236)  # the middle of the page, the paper itself
ROSE = (244, 219, 208)  # the evening at its foot
MAUVE = (234, 219, 230)  # the sky going violet, to the right of the foot
CLOUDING = 2.5  # how far the paper's clouding strays from the even colour, in levels
STEP = 8  # the wash is drawn at 1/STEP of the window's width (the colours change slowly across)
STEP_DOWN = 4  # and 1/STEP_DOWN of its height: down the page they change fast, and a coarser step would show
MARGIN = 96  # how far the wash runs on past the window's right and foot
TILE = 512  # the grain's square
GRAIN = 2  # the grain darkens each pixel by 1 to 1 + GRAIN levels (never 0: see ``grain``), which the wash
LIFT = 1 + GRAIN / 2  # adds back on average
FIBRE = 4  # and a fibre by this much more
QUICK = 0.03  # seconds: while the window is being dragged taller or shorter, a new wash at most this often
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
# belong to the window's coordinates, so a resize does not stir them, and a row of them, once worked out,
# is kept for every wash after.
_CLOUDS = []
_random = random.Random(7)
for _cell, _weight in ((140, 0.7), (50, 0.3)):
    _CLOUDS.append((_cell, _weight, [[_random.uniform(-1.0, 1.0) for _ in range(32)] for _ in range(32)]))
_cloud_rows: dict[int, list[float]] = {}


def _cloud_row(row: int, across: int) -> list[float]:
    """The clouds at the wash's samples along its row ``row``, at least ``across`` of them."""
    found = _cloud_rows.get(row)
    if found is not None and len(found) >= across:
        return found
    across = -(-across // 64) * 64  # a little to spare, so a wider window does not mean working it out again
    y = (row + 0.5) * STEP_DOWN
    total = [0.0] * across
    for cell, weight, grid in _CLOUDS:
        fy = y / cell
        v = _ease(fy - int(fy))
        above, below = grid[int(fy) % 32], grid[(int(fy) + 1) % 32]
        line = [(a + (b - a) * v) * weight for a, b in zip(above, below)]
        for i in range(across):
            fx = (i + 0.5) * STEP / cell
            k = int(fx)
            total[i] += line[k % 32] + (line[(k + 1) % 32] - line[k % 32]) * _ease(fx - k)
    _cloud_rows[row] = total
    return total


QUARTERS = round(4 * CLOUDING)  # the clouds, at full strength, in quarter levels either side of nothing
_cloud_steps: dict[int, bytes] = {}


def _cloud_index(row: int, across: int) -> bytes:
    """The clouds along row ``row`` at full strength, as a byte per sample: quarter levels, 0 the darkest."""
    found = _cloud_steps.get(row)
    if found is None or len(found) < across:
        found = bytes(QUARTERS + round(4 * CLOUDING * c) for c in _cloud_row(row, across))
        _cloud_steps[row] = found
    return found


def _png(width: int, height: int, raw: bytes, kind: int) -> bytes:
    """A PNG of ``raw`` rows (each led by its filter byte); ``kind`` 2 is RGB, 6 is RGBA."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, kind, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 1))
            + chunk(b"IEND", b""))


def wash(width: int, height: int, flat: int) -> bytes:
    """The colours of the page for a window ``width`` by ``height``, and a margin past it, at 1/STEP of
    the width and 1/STEP_DOWN of the height, as an RGB PNG.  Its rows above ``flat`` are one colour.
    What depends on the row alone or the column alone is worked out once for it, and the clouds are
    kept from wash to wash, which keeps a wash to a few milliseconds."""
    across, down = -(-(width + MARGIN) // STEP), -(-(height + MARGIN) // STEP_DOWN)
    toward_right = [_smooth(0.2, 1.0, (i + 0.5) * STEP / max(1, width - 1)) for i in range(across)]
    raw = bytearray()
    for j in range(down):
        y = (j + 0.5) * STEP_DOWN
        red, green, blue = (value + LIFT + 0.5 for value in _row(y, height, flat))  # LIFT: what the grain takes
        low = _smooth(0.6, 1.0, y / max(1, height - 1)) * 0.8
        cloudy = CLOUDING * _smooth(flat, flat + 0.15 * height, y)
        row = bytearray(3 * across)  # (no colour comes past 255: 250 at most, a lift of 2 and clouds of 2.5)
        if low:  # the evening's mauve, which follows the window's width
            clouds = [c * cloudy for c in _cloud_row(j, across)[:across]] if cloudy else [0.0] * across
            dr, dg, db = (MAUVE[0] - red) * low, (MAUVE[1] - green) * low, (MAUVE[2] - blue) * low
            row[0::3] = bytes(int(red + dr * k + c) for k, c in zip(toward_right, clouds))
            row[1::3] = bytes(int(green + dg * k + c) for k, c in zip(toward_right, clouds))
            row[2::3] = bytes(int(blue + db * k + c) for k, c in zip(toward_right, clouds))
        elif cloudy == CLOUDING:  # most of the page: a colour and the clouds, put together by bytes.translate
            steps = _cloud_index(j, across)[:across]
            for start, value in enumerate((red, green, blue)):
                table = bytes(int(value + (k - QUARTERS) / 4) for k in range(2 * QUARTERS + 1))
                row[start::3] = steps.translate(table.ljust(256, bytes(1)))
        else:  # the lamplight, and the clouds coming into it
            clouds = [c * cloudy for c in _cloud_row(j, across)[:across]] if cloudy else [0.0] * across
            row[0::3] = bytes(int(red + c) for c in clouds)
            row[1::3] = bytes(int(green + c) for c in clouds)
            row[2::3] = bytes(int(blue + c) for c in clouds)
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


def _pixels(png: bytes) -> tuple[int, int, int, bytes]:
    """Width, height, bytes per pixel and the pixels, row after row, of an 8-bit RGB or RGBA PNG."""
    width, height = struct.unpack(">II", png[16:24])
    depth = {2: 3, 6: 4}[png[25]]
    data, at = b"", 8
    while at < len(png):
        length, kind = struct.unpack(">I4s", png[at:at + 8])
        if kind == b"IDAT":
            data += png[at + 8:at + 8 + length]
        at += 12 + length
    raw, stride = zlib.decompress(data), width * depth
    out = bytearray()
    above = bytearray(stride)
    for row in range(height):
        kind, line = raw[row * (stride + 1)], bytearray(raw[row * (stride + 1) + 1:(row + 1) * (stride + 1)])
        for i in range(stride if kind else 0):  # undo the row's filter (Tk writes none, but a PNG may have any)
            left = line[i - depth] if i >= depth else 0
            up, corner = above[i], above[i - depth] if i >= depth else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif kind == 4:
                guess = left + up - corner
                near = min((abs(guess - left), 0, left), (abs(guess - up), 1, up), (abs(guess - corner), 2, corner))
                line[i] = (line[i] + near[2]) & 0xFF
        out += line
        above = line
    return width, height, depth, bytes(out)


def fade(master: tk.Misc, image: tk.PhotoImage, left: int, top: int, width: int, height: int,
         solid_at_top: bool) -> tk.PhotoImage | None:
    """A strip of the page ``image`` from (left, top), ``width`` by ``height``: the paper itself along one
    edge (the top, or the foot), clearing to nothing along the other.  Laid over a list that runs on past
    that edge, its last lines sink into the paper rather than being cut off.  None where the strip is not
    on the page."""
    try:
        png = image.tk.call(image, "data", "-format", "png", "-from", left, top, left + width, top + height)
        across, down, depth, rgb = _pixels(png if isinstance(png, bytes) else bytes(png, "latin-1"))
    except (tk.TclError, KeyError, ValueError, struct.error, zlib.error):
        return None
    raw = bytearray()
    for row in range(down):
        t = (row + 0.5) / down
        clear = round(255 * _smooth(0.0, 1.0, 1 - t if solid_at_top else t))  # how much of the paper shows
        line = bytearray(4 * across)
        for channel in range(3):
            line[channel::4] = rgb[row * across * depth + channel:(row + 1) * across * depth:depth]
        line[3::4] = bytes((max(clear, 1),)) * across  # never wholly clear (see ``grain``)
        raw += b"\x00" + line
    return _photo(master, _png(across, down, bytes(raw), 6))


class Page:
    """The paper under one window, and the canvases that lay it."""

    def __init__(self, root: tk.Misc, flat: int):
        self.root, self.flat = root, flat
        self.surfaces: list[OnPage] = []
        self.image: tk.PhotoImage | None = None  # the paper, as the surfaces lay it
        self.grain: tk.PhotoImage | None = None
        self.size = (0, 0)
        self.job: str | None = None
        self.soon: str | None = None  # a wash for a window grown past this one's margin
        self.later: str | None = None  # the wash for the size the window settles at
        self.wanted = (0, 0)  # the window's size, as it last said
        self.washed_at = 0.0
        self.marks: list[tuple[tk.PhotoImage, Place]] = []  # pressed into the paper (``press``)
        self.pressed: list | None = None  # where they are pressed in now
        self.patches: list[tuple[tk.PhotoImage, int, int]] = []  # the paper from under them, and where it goes
        self.over: list[tuple[tk.PhotoImage, Place]] = []  # lying over the page (``hang``)
        self.edition = 0  # changes whenever the picture does, for what is cut from it (``fade``)
        root.bind("<Configure>", self.moved, add="+")

    def lay(self, width: int, height: int) -> None:
        """Make the paper for a window of this size and lay it under every surface, now."""
        if self.grain is None:
            self.grain = _photo(self.root, grain_tile())
        image = _photo(self.root, wash(width, height, self.flat)).zoom(STEP, STEP_DOWN)
        image.tk.call(image, "copy", self.grain, "-to", 0, 0, image.width(), image.height(),
                      "-compositingrule", "overlay")  # the grain, tiled over the wash
        old, self.image = self.image, image
        self.size, self.washed_at = (width, height), time.monotonic()
        self.pressed, self.patches = None, []  # a fresh picture, with no marks in it yet
        self.edition += 1
        self.refresh()
        del old  # only now, with nothing showing it any more

    def press(self, image: tk.PhotoImage, place: Place) -> None:
        """Press ``image`` into the paper at ``place``: an ornament, a watermark, or a length of a rule that
        ``place`` lays along a box, under everything written on the page and showing through its tints."""
        self.marks.append((image, place))
        self.pressed = None
        self.schedule()

    def hang(self, image: tk.PhotoImage, place: Place) -> None:
        """Hang ``image`` over the page at ``place``, as a ribbon lies over a book's page: each canvas it
        crosses lays it over all it draws.  The song list lays it again after drawing; a piece that draws
        anew without laying its paper again would draw over it, so it belongs where only the backdrop and
        the list lie."""
        self.over.append((image, place))
        self.schedule()

    def swap(self, old: tk.PhotoImage, new: tk.PhotoImage | None) -> None:
        """Hang ``new`` where ``old`` hangs (the ribbon, a frame of its tug): the canvases it crosses lay it again
        now, and nothing else is touched."""
        over = []
        for image, place in self.over:
            if image is not old:
                over.append((image, place))
            elif new is not None:  # (None takes it down)
                over.append((new, place))
        self.over = over
        for surface in self.surfaces:
            try:
                if surface.away or not surface.winfo_exists():
                    continue
                canvas: tk.Canvas = surface  # type: ignore[assignment]
                x, y = self.offset(surface)
                surface.lay_over(x, y, canvas.canvasx(0) - x, canvas.canvasy(0) - y)
            except tk.TclError:
                pass

    def window(self) -> tuple[int, int]:
        """The window's size, as it last said, or else the size the paper was made for."""
        width, height = self.wanted
        return (width, height) if width > 1 and height > 1 else self.size

    def stamp(self) -> None:
        """Press the marks in where they go for the window as it is now, first taking them out of where
        they were by putting back the paper kept from under them.  Left alone while they are where they go."""
        image = self.image
        if image is None or not self.marks:
            return
        places = [place(*self.window()) for _, place in self.marks]
        if places == self.pressed:
            return
        for patch, x, y in reversed(self.patches):
            image.tk.call(image, "copy", patch, "-to", x, y, "-compositingrule", "set")
        self.patches = []
        right, foot = image.width(), image.height()
        for (mark, _), at in zip(self.marks, places):
            if at is None:
                continue
            x, y = at[0], at[1]
            width, height = (at[2], at[3]) if len(at) == 4 else (mark.width(), mark.height())
            left, top = max(x, 0), max(y, 0)
            far, low = min(x + width, right), min(y + height, foot)
            if far <= left or low <= top:
                continue
            patch = tk.PhotoImage(master=self.root, width=far - left, height=low - top)
            patch.tk.call(patch, "copy", image, "-from", left, top, far, low)
            if len(at) == 4:  # laid over and over (Tk tiles a picture over a bigger box)
                image.tk.call(image, "copy", mark, "-to", left, top, far, low, "-compositingrule", "overlay")
            else:
                image.tk.call(image, "copy", mark, "-from", left - x, top - y, far - x, low - y, "-to", left, top,
                              "-compositingrule", "overlay")
            self.patches.append((patch, left, top))
        self.pressed = places
        self.edition += 1

    def add(self, surface: "OnPage") -> None:
        self.surfaces.append(surface)
        surface.bind("<Configure>", lambda _: self.schedule(), add="+")
        self.schedule()

    def tone(self, x: float, y: float) -> str:
        width, height = self.size
        return colour(x, y, max(width, 1), max(height, 1), self.flat)

    def moved(self, event: tk.Event) -> None:
        """The window (or a part of it: a toplevel's bindings see its children's events too) changed.

        Dragged taller or shorter, it is washed anew as it goes (at most every QUICK seconds), so the
        colours, laid out by its height, stretch with it.  Dragged only wider, the wash it has stays, its
        evening's mauve a little off but the paper covering it, until the window outgrows the margin.
        Either way, once it holds still it is washed for the size it came to."""
        if event.widget is not self.root:
            return
        self.wanted = event.width, event.height
        if event.width > 1 and event.height > 1 and self.wanted != self.size:
            if self.later is not None:
                self.root.after_cancel(self.later)
            self.later = self.root.after(SETTLE, self.settled)
            stretched = event.height != self.size[1]  # the colours follow the height: wash as it goes
            outgrown = event.width > self.size[0] + MARGIN or event.height > self.size[1] + MARGIN
            if (stretched or outgrown) and self.soon is None:
                wait = max(QUICK - (time.monotonic() - self.washed_at), 0.0)
                self.soon = self.root.after(max(1, round(wait * 1000)), self.grown)
        self.schedule()

    def grown(self) -> None:  # the quick wash while a drag goes on
        self.soon = None
        self.catch_up()

    def settled(self) -> None:
        self.later = None
        self.catch_up()
        self.pressed = None  # the marks go where the pieces have come to rest
        self.schedule()  # whatever stepped aside while the window was being resized comes back

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
        self.stamp()
        alive = []
        for surface in self.surfaces:
            try:
                if not surface.winfo_exists():
                    continue
                if surface.away:  # on a page not shown: only the picture, so none it shows is freed
                    surface.keep_paper(self.image)
                else:
                    x, y = self.offset(surface)
                    surface.lay_paper(self.image, x, y)
            except tk.TclError:
                pass  # (laid again next time)
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
    """For a canvas that shows its slice of the window's page under what it draws, and what hangs over the
    page over it.  Its drawings clear with ``delete("!paper")``, which leaves both where they lie."""

    page: Page | None = None
    away = False  # on a page that is not shown (the window shows one of two): ``Page.refresh`` passes it by
    paper: tuple[tk.PhotoImage, int, int] | None = None
    laid: tuple[str, float, float] | None = None  # the image and place the paper item has
    hung: list[tuple[str, float, float]] = []  # the images and places of what hangs over it

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
        found = canvas.find_withtag("paper&&!over")
        if not found or self.laid != (str(image), left, top):
            if found:
                canvas.coords(found[0], left, top)
                canvas.itemconfigure(found[0], image=image)
            else:
                canvas.tag_lower(canvas.create_image(left, top, anchor="nw", image=image, tags="paper"))
            self.laid = (str(image), left, top)
        self.lay_over(x, y, left, top)

    def keep_paper(self, image: tk.PhotoImage) -> None:
        """Away, show the paper's new picture where the old one was, so that the old one may be freed; where
        it goes is worked out once the canvas is shown again."""
        canvas: tk.Canvas = self  # type: ignore[assignment]
        found = canvas.find_withtag("paper&&!over")
        if found and (self.laid is None or self.laid[0] != str(image)):
            canvas.itemconfigure(found[0], image=image)
        self.laid = None

    def lay_over(self, x: int, y: int, left: float, top: float) -> None:
        """Lay what hangs over the page where it crosses this canvas, which stands at (x, y) in the window,
        over everything it has drawn.  (Its items are tagged ``paper`` too, so drawings clearing with
        ``delete("!paper")`` leave them.)"""
        canvas: tk.Canvas = self  # type: ignore[assignment]
        page, hung = self.page, []
        if page is not None and page.over:
            width, height = canvas.winfo_width(), canvas.winfo_height()
            for image, place in page.over:
                at = place(*page.window())
                if at is not None and at[0] < x + width and x < at[0] + image.width() \
                        and at[1] < y + height and y < at[1] + image.height():
                    hung.append((str(image), left + at[0], top + at[1]))
        if hung != self.hung:
            canvas.delete("over")
            for image_name, ix, iy in hung:
                canvas.create_image(ix, iy, anchor="nw", image=image_name, tags=("paper", "over"))
            self.hung = hung
        elif hung:
            canvas.tag_raise("over")  # over whatever was drawn since

    def follow_page(self) -> None:
        """Keep the paper still under a canvas that has scrolled."""
        if self.paper is not None:
            self.lay_paper(*self.paper)


class ScrollingPage(OnPage):
    """For a canvas that scrolls over the page (the song list, the 简谱 page's score): the paper stays still
    under it, and at an edge with more beyond, its last lines sink into the paper instead of being cut
    off.  It sets ``extent``, how tall all of it is, and calls ``make_fades`` once it is a canvas."""

    extent = 0
    theme: "theme.Theme"

    def make_fades(self) -> None:
        canvas: tk.Canvas = self  # type: ignore[assignment]
        self.fade_top = canvas.create_image(0, 0, anchor="nw", state="hidden", tags="fade")
        self.fade_bottom = canvas.create_image(0, 0, anchor="nw", state="hidden", tags="fade")
        self.fades: tuple[tk.PhotoImage | None, tk.PhotoImage | None] = (None, None)
        self.faded_for: tuple | None = None  # the page and place the fades were made for

    def lay_paper(self, image: tk.PhotoImage, x: int, y: int) -> None:
        super().lay_paper(image, x, y)
        self.fade_edges(image, x, y)

    def fade_edges(self, image: tk.PhotoImage, x: int, y: int) -> None:
        """At the top, once it has scrolled, and at the foot, while there is more below, a strip of the page
        clearing away from the edge lies over what is written (``fade``), so that the lines there sink into
        the paper.  The strips are made again only when the page or the canvas's place changes."""
        canvas: tk.Canvas = self  # type: ignore[assignment]
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        if self.page is not None and self.page.later is not None:  # being resized: the fades step aside,
            self.faded_for = None  # to be made once it holds still and the page is washed for its size
            canvas.itemconfigure("fade", state="hidden")
            return
        tall = min(self.theme.px(18), height // 4)
        edition = self.page.edition if self.page is not None else 0  # (marks pressed in anew change the paper)
        if self.faded_for != (str(image), edition, x, y, width, height):
            self.faded_for = (str(image), edition, x, y, width, height)
            for item in (self.fade_top, self.fade_bottom):  # let go of the old strips before they are freed
                canvas.itemconfigure(item, image="")
            self.fades = (fade(canvas, image, x, y, width, tall, solid_at_top=True),
                          fade(canvas, image, x, y + height - tall, width, tall, solid_at_top=False))
        top = canvas.canvasy(0)
        above, below = self.edges(top, height)
        for item, strip, at, show in ((self.fade_top, self.fades[0], top, above),
                                      (self.fade_bottom, self.fades[1], top + height - tall, below)):
            if strip is None:
                canvas.itemconfigure(item, image="", state="hidden")
                continue
            canvas.coords(item, canvas.canvasx(0), at)
            canvas.itemconfigure(item, image=strip, state="normal" if show else "hidden")
        canvas.tag_raise("fade")
        canvas.tag_raise("over")  # the ribbon lies over the lines sinking into the paper too

    def edges(self, top: float, height: int) -> tuple[bool, bool]:
        """Whether there is more above the view (it has scrolled) and below it."""
        return top > 0, top + height < self.extent

    def yview(self, *args):
        result = super().yview(*args)  # type: ignore[misc]
        if args:
            self.follow_page()
        return result

    def yview_moveto(self, fraction: float) -> None:
        super().yview_moveto(fraction)  # type: ignore[misc]
        self.follow_page()

    def yview_scroll(self, number: int, what: str) -> None:
        super().yview_scroll(number, what)  # type: ignore[misc]
        self.follow_page()
