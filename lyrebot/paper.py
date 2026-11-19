"""The page the main window is written on: paper lit by a desk lamp at its head, going over to the pink
and mauve of an evening sky at its foot, with a fine grain, a little mottling and a few fibres.

Tk's widgets cannot be see-through, so the paper is drawn once as a picture the size of the window, and
every part of the window that shows the page is a canvas laying its own slice of that one picture under
what it draws (``OnPage``).  The search field cannot (a real Entry is solid), so the head of the page,
where it sits, is one even colour, which the field takes (``Page.head``).

The picture is drawn in pure Python, about half a second for a window, so each size is kept on disk
beside the icons; after a resize the new size is drawn on a thread while the old picture stays up.
"""

from __future__ import annotations

import base64
import math
import random
import struct
import threading
import tkinter as tk
import zlib

from . import theme

HEAD = (250, 234, 200)  # lamplight over the head of the page
CREAM = (250, 245, 236)  # the middle of the page, the paper itself
ROSE = (244, 219, 208)  # the evening at its foot
MAUVE = (234, 219, 230)  # the sky going violet, to the right of the foot
MOTTLE = 2.5  # how far the paper's clouding strays from the even colour, in levels
FIBRE = 5  # how much darker a fibre is
KEEP = 8  # sizes of page kept on disk


def _smooth(low: float, high: float, x: float) -> float:
    t = min(max((x - low) / (high - low), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(round(v) for v in rgb)


def _row(y: int, height: int, flat: int) -> tuple[float, float, float]:
    """The page's colour across row ``y``, before the evening's mauve, the clouding and the grain."""
    lit = _smooth(flat, flat + 0.38 * height, y)  # out of the lamplight into the paper's own colour
    evening = _smooth(0.45, 1.0, y / max(1, height - 1))
    out = []
    for head, cream, rose in zip(HEAD, CREAM, ROSE):
        day = head + (cream - head) * lit
        out.append(day + (rose - day) * evening)
    return out[0], out[1], out[2]


def colour(x: float, y: float, width: int, height: int, flat: int) -> str:
    """The page's even colour at (x, y): what a solid widget standing there should be."""
    base = _row(int(y), height, flat)
    weight = _smooth(0.6, 1.0, y / max(1, height - 1)) * 0.8 * _smooth(0.2, 1.0, x / max(1, width - 1))
    return _hex(tuple(b + (m - b) * weight for b, m in zip(base, MAUVE)))  # type: ignore[arg-type]


def draw(width: int, height: int, flat: int, seed: int = 7) -> bytes:
    """The page for a window ``width`` by ``height`` as an RGB PNG; its top ``flat`` rows are one colour
    and a grain."""
    rng = random.Random(seed)
    grids = []
    for step, weight in ((140, 0.7), (50, 0.3)):  # clouding: two sizes of soft blotch
        values = [[rng.uniform(-1.0, 1.0) for _ in range(width // step + 3)] for _ in range(height // step + 3)]
        across = []
        for x in range(width):
            spot = x / step
            i = int(spot)
            u = spot - i
            across.append((i, u * u * (3 - 2 * u)))
        grids.append((step, weight, values, across))
    violet = [_smooth(0.2, 1.0, x / max(1, width - 1)) for x in range(width)]
    grain = rng.randbytes(width * height)
    fibres = bytearray(width * height)
    calm = flat + int(0.1 * height)  # no fibres in the lamplight
    for _ in range(width * height // 1400):
        x, y = rng.uniform(0, width), rng.uniform(calm, height)
        heading = rng.uniform(0, math.pi)
        for _ in range(rng.randint(6, 26)):
            heading += rng.gauss(0, 0.18)
            x += math.cos(heading)
            y += math.sin(heading)
            if 0 <= x < width and calm <= y < height:
                fibres[int(y) * width + int(x)] = FIBRE
    raw = bytearray()
    for y in range(height):
        red, green, blue = _row(y, height, flat)
        mauve = _smooth(0.6, 1.0, y / max(1, height - 1)) * 0.8
        dr, dg, db = (MAUVE[0] - red) * mauve, (MAUVE[1] - green) * mauve, (MAUVE[2] - blue) * mauve
        fade = MOTTLE * _smooth(flat, flat + 0.15 * height, y)
        clouds = [0.0] * width
        if fade:
            for step, weight, values, across in grids:
                spot = y / step
                j = int(spot)
                v = spot - j
                v = v * v * (3 - 2 * v)
                line = [(p + (q - p) * v) * weight * fade for p, q in zip(values[j], values[j + 1])]
                clouds = [c + line[i] + (line[i + 1] - line[i]) * u for c, (i, u) in zip(clouds, across)]
        start = y * width
        row = bytearray(3 * width)
        red, green, blue = red + 0.5, green + 0.5, blue + 0.5
        for x in range(width):
            k, n = violet[x], clouds[x] + grain[start + x] % 3 - 1 - fibres[start + x]
            row[3 * x] = int(red + dr * k + n)
            row[3 * x + 1] = int(green + dg * k + n)
            row[3 * x + 2] = int(blue + db * k + n)
        raw += b"\x00" + row

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def picture(width: int, height: int, flat: int) -> bytes:
    """The page for this size, from disk, or drawn now and kept (safe to call from a thread)."""
    path = theme.CACHE / f"page-{width}x{height}-{flat}.png"
    try:
        return path.read_bytes()
    except OSError:
        pass
    data = draw(width, height, flat)
    try:
        theme.CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        pages = sorted(theme.CACHE.glob("page-*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in pages[KEEP:]:
            old.unlink()
    except OSError:
        pass
    return data


class Page:
    """The paper under one window, and the canvases that lay it."""

    SETTLE = 150  # milliseconds after the last change of size before the page is drawn anew

    def __init__(self, root: tk.Misc, flat: int):
        self.root, self.flat = root, flat
        self.head = _hex(HEAD)  # the even colour of the top ``flat`` rows
        self.surfaces: list[OnPage] = []
        self.image: tk.PhotoImage | None = None
        self.size = (0, 0)
        self.job: str | None = None
        self.resize_job: str | None = None
        self.wanted: tuple[int, int] | None = None
        root.bind("<Configure>", self.moved, add="+")

    def lay(self, width: int, height: int) -> None:
        """Lay the page for a window of this size, now."""
        self.show(width, height, picture(width, height, self.flat))

    def show(self, width: int, height: int, data: bytes) -> None:
        self.image = tk.PhotoImage(master=self.root, data=base64.b64encode(data).decode("ascii"))
        self.size = (width, height)
        self.refresh()  # at once: the old picture goes when nothing shows it any more

    def add(self, surface: "OnPage") -> None:
        self.surfaces.append(surface)
        surface.bind("<Configure>", lambda _: self.schedule(), add="+")
        self.schedule()

    def tone(self, x: float, y: float) -> str:
        width, height = self.size
        return colour(x, y, max(width, 1), max(height, 1), self.flat)

    def moved(self, event: tk.Event) -> None:
        """The window (or a part of it: a toplevel's bindings see its children's events) changed."""
        if event.widget is not self.root:
            return
        self.schedule()
        if event.width > 1 and event.height > 1 and (event.width, event.height) != self.size:
            if self.resize_job:
                self.root.after_cancel(self.resize_job)
            self.resize_job = self.root.after(self.SETTLE, self.resized)

    def resized(self) -> None:
        self.resize_job = None
        size = self.root.winfo_width(), self.root.winfo_height()
        if size == self.size or size == self.wanted:
            return
        self.wanted = size
        found: dict[str, bytes] = {}
        worker = threading.Thread(target=lambda: found.setdefault("data", picture(*size, self.flat)), daemon=True)
        worker.start()
        self.collect(worker, size, found)

    def collect(self, worker: threading.Thread, size: tuple[int, int], found: dict[str, bytes]) -> None:
        if worker.is_alive():
            self.root.after(40, lambda: self.collect(worker, size, found))
            return
        if self.wanted == size:
            self.wanted = None
            if "data" in found:
                self.show(*size, found["data"])

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

    def take_page(self, owner: "theme.Theme") -> None:
        """Lie on the page of the window this canvas is in, if that window has one."""
        page = getattr(owner, "page", None)
        if page is not None and page.root is self.winfo_toplevel():  # type: ignore[attr-defined]
            self.page = page
            page.add(self)

    def lay_paper(self, image: tk.PhotoImage, x: int, y: int) -> None:
        self.paper = (image, x, y)
        left, top = self.canvasx(0) - x, self.canvasy(0) - y  # type: ignore[attr-defined]
        found = self.find_withtag("paper")  # type: ignore[attr-defined]
        if found:
            self.coords(found[0], left, top)  # type: ignore[attr-defined]
            self.itemconfigure(found[0], image=image)  # type: ignore[attr-defined]
        else:
            self.create_image(left, top, anchor="nw", image=image, tags="paper")  # type: ignore[attr-defined]
        self.tag_lower("paper")  # type: ignore[attr-defined]

    def follow_page(self) -> None:
        """Keep the paper still under a canvas that has scrolled."""
        if self.paper is not None:
            self.lay_paper(*self.paper)
