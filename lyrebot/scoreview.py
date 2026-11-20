"""The 简谱 page's score: a LyreBot score drawn as 简谱 on the window's page.

The layout and the drawing are ``jianpu``'s own, the ones it prints with, so the page reads as the
exported 简谱 does; only the pen is different.  ``jianpu`` draws onto anything with ``text``, ``line``, ``dot``
and ``path``, and ``Drawing`` gives it those on a Tk canvas: digits in the serif, in ink, with held notes
paler; bar lines and the brace in old gold; numbers and tempo marks in the figures' face, muted.  Tk does
not smooth what it draws itself, so level and upright lines are laid on whole pixels and the curves (ties,
slurs, rolls, triplet brackets, the brace) and the dots are drawn here, smoothed, as small pictures.

The systems are laid out for the view's width (more bars a line as it widens) and scroll over the page,
which stays still under them, the last lines at an edge sinking into the paper as the song list's do.
The view knows where every note is and when it sounds, so it can light the notes being heard, keep them in
sight, and say which note was clicked.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
import math
import re
import time
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable

from . import icons, jianpu
from .paper import ScrollingPage
from .score import Score
from .theme import CHOSEN_TINT, GOLD, HOVER_TINT, INK, MUTED, ORANGE, Theme

SCALE = 0.72  # pixels per unit of the printed page, at 100% scaling: two bars of quavers to a line
LEFT, RIGHT = 20, 10  # units kept clear left of the systems (for the brace) and right of them
NUMBER = 15  # how tall a bar's number is written, in units (12 in print, where only a line's first has one)
TOP, FOOT = 8, 24  # and above the first system, and under the last
HELD = "#B3A793"  # a note going on from the one before, not struck again: paler ink
COLOURS = {"#111": INK, jianpu.GREY: HELD, "#444": MUTED, "#777": MUTED}
FOLLOW_PAUSE = 4.0  # seconds after the reader scrolls before the view follows the song again
GLIDE = (0.25, 0.5, 0.72, 0.88, 0.96, 1.0)  # the steps of a scroll to keep the notes heard in sight
SNAP = 0.25  # seconds: a note starts with the nearest key pressed this close to where it is written
SLICE = 0.025  # seconds of drawing at a time: the score is drawn a few systems at a turn, those in view first


# ---- smoothed lines, as pictures --------------------------------------------------------------

_NUMBER = re.compile(r"[MLQC]|-?\d+(?:\.\d+)?")


def flatten(d: str, steps: int = 14) -> list[list[tuple[float, float]]]:
    """An SVG path of absolute M, L, Q and C commands (as ``jianpu`` writes them) as lines of points."""
    tokens = _NUMBER.findall(d)
    lines: list[list[tuple[float, float]]] = []
    index, command = 0, "M"

    def take(count: int) -> list[float]:
        nonlocal index
        values = [float(value) for value in tokens[index:index + count]]
        index += count
        return values
    while index < len(tokens):
        if tokens[index] in "MLQC":
            command = tokens[index]
            index += 1
            continue
        if command == "M":
            x, y = take(2)
            lines.append([(x, y)])
            command = "L"  # coordinates after a move go on as lines
        elif command == "L":
            lines[-1].append(tuple(take(2)))  # type: ignore[arg-type]
        elif command == "Q":
            (x0, y0), (cx, cy, x1, y1) = lines[-1][-1], take(4)
            for step in range(1, steps + 1):
                t = step / steps
                lines[-1].append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1,
                                  (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1))
        elif command == "C":
            (x0, y0), (ax, ay, bx, by, x1, y1) = lines[-1][-1], take(6)
            for step in range(1, steps + 1):
                t = step / steps
                u = 1 - t
                lines[-1].append((u ** 3 * x0 + 3 * u * u * t * ax + 3 * u * t * t * bx + t ** 3 * x1,
                                  u ** 3 * y0 + 3 * u * u * t * ay + 3 * u * t * t * by + t ** 3 * y1))
    return lines


def ink(lines: list[list[tuple[float, float]]], pen: float, colour: str) -> tuple[bytes, int, int, int, int]:
    """Lines of points drawn ``pen`` pixels wide, smoothed: each pixel as covered as its middle is near the
    line.  Returns a PNG and where its top left corner goes, and its size."""
    xs = [x for line in lines for x, _ in line]
    ys = [y for line in lines for _, y in line]
    reach = pen / 2 + 1
    left, top = math.floor(min(xs) - reach), math.floor(min(ys) - reach)
    width, height = math.ceil(max(xs) + reach) - left, math.ceil(max(ys) + reach) - top
    cover = [0.0] * (width * height)
    half = pen / 2
    for line in lines:
        for (ax, ay), (bx, by) in zip(line, line[1:] or line):
            ax, ay, bx, by = ax - left, ay - top, bx - left, by - top
            dx, dy = bx - ax, by - ay
            length = dx * dx + dy * dy
            for py in range(max(0, math.floor(min(ay, by) - reach)), min(height, math.ceil(max(ay, by) + reach))):
                cy = py + 0.5
                row = py * width
                for px in range(max(0, math.floor(min(ax, bx) - reach)), min(width, math.ceil(max(ax, bx) + reach))):
                    cx = px + 0.5
                    t = 0.0 if length == 0 else max(0.0, min(1.0, ((cx - ax) * dx + (cy - ay) * dy) / length))
                    distance = math.hypot(ax + t * dx - cx, ay + t * dy - cy)
                    covered = half + 0.5 - distance
                    if covered > cover[row + px]:
                        cover[row + px] = min(covered, 1.0)
    red, green, blue = int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)
    pixels = bytearray(4 * width * height)
    for index, amount in enumerate(cover):
        if amount > 0:
            pixels[4 * index:4 * index + 4] = bytes((red, green, blue, round(255 * amount)))
    return icons.png(width, height, bytes(pixels)), left, top, width, height


# ---- what is where ------------------------------------------------------------------------------

@dataclass
class Note:
    """A written note or chord on the view: where it is drawn, and when it sounds."""
    staff: int
    beat: object  # Fraction: from the start of the song, as written on the page
    length: object  # Fraction
    struck: bool
    box: list[float] = field(default_factory=lambda: [math.inf, math.inf, -math.inf, -math.inf])
    at: float = 0.0  # seconds into the song, at the speed it is heard
    until: float = 0.0
    start: float = 0.0  # where hearing it from begins: at its key's press, which may be a hair off the page


class Drawing:
    """``jianpu``'s pen on a Tk canvas (see the module's note), ``scale`` pixels to the printed page's unit.

    Without ``first`` it only measures: it notes each note and where it is drawn, and draws nothing.  With
    it, it draws, numbering the notes from ``first`` as the measuring did."""

    def __init__(self, view: "ScoreView", scale: float, first: int | None = None):
        self.view, self.scale = view, scale
        self.measuring, self.next = first is None, first or 0
        self.current: int | None = None  # the note what is drawn now belongs to

    def note(self, bar, staff: int, symbol) -> None:
        if symbol is None or not symbol.keys:
            self.current = None
        elif self.measuring:
            self.current = self.view.register(bar, staff, symbol)
        else:
            self.current, self.next = self.next, self.next + 1

    def _grow(self, left: float, top: float, right: float, bottom: float) -> None:
        if self.current is not None:
            box = self.view.notes[self.current].box
            box[0], box[1] = min(box[0], left), min(box[1], top)
            box[2], box[3] = max(box[2], right), max(box[3], bottom)

    def _tags(self, kind: str) -> tuple[str, ...]:
        return ("score", kind, f"n{self.current}") if self.current is not None else ("score", kind)

    def text(self, x: float, y: float, value: str, size: float, anchor: str = "middle", family: str = jianpu.FONT,
             weight: str = "bold", fill: str = "#111", style: str = "") -> None:
        s = self.scale
        plain = weight == "normal"  # bar numbers, tempo marks, triplets' 3: the figures' face, muted
        font = self.view.font("figures" if plain else "digits", size * s, style == "italic")
        colour = MUTED if plain and fill == "#111" else COLOURS.get(fill, INK)
        ascent, descent = self.view.metrics(font)
        px, baseline = x * s, y * s
        if self.measuring:
            if self.current is not None:
                width = self.view.width_of(font, value)
                self._grow(px - width / 2, baseline - ascent * 0.75, px + width / 2, baseline + descent)
            return
        tk_anchor = {"middle": "s", "start": "sw", "end": "se"}[anchor]
        item = self.view.create_text(px, baseline + descent, text=value, anchor=tk_anchor, font=font, fill=colour,
                                     tags=self._tags("digit"))
        if self.current is not None:
            self.view.inked[item] = colour

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 1.3, stroke: str = "#111") -> None:
        s = self.scale
        pen = max(1, round(width * s))
        colour = COLOURS.get(stroke, stroke)
        a, b, c, d = (round(v * s) for v in (x1, y1, x2, y2))  # level and upright: on whole pixels
        if self.measuring:
            if self.current is not None:
                self._grow(min(a, c) - pen, min(b, d) - pen, max(a, c) + pen, max(b, d) + pen)
            return
        item = self.view.create_line(a, b, c, d, width=pen, fill=colour, capstyle="butt", tags=self._tags("stroke"))
        if self.current is not None:
            self.view.inked[item] = colour

    def dot(self, x: float, y: float, r: float = 1.9, fill: str = "#111") -> None:
        s = self.scale
        colour = COLOURS.get(fill, INK)
        radius = max(1.0, round(r * s * 2) / 2)
        if self.measuring:
            if self.current is not None:
                self._grow(x * s - radius, y * s - radius, x * s + radius, y * s + radius)
            return
        item = self.view.create_image(x * s, y * s, image=self.view.dot(radius, colour), tags=self._tags("dot"))
        if self.current is not None:
            self.view.dotted[item] = (radius, colour)

    def bar_number(self, bar, x: float, y: float) -> None:
        """Every bar's number over its opening bar line, in the bar lines' gold (``x``, ``y``: the bar's left
        and the number's baseline, in units)."""
        if self.measuring:
            return
        s = self.scale
        font = self.view.font("figures", NUMBER * s)
        _, descent = self.view.metrics(font)
        self.view.create_text(x * s + self.view.theme.px(2), y * s + descent, text=str(bar.number), anchor="sw",
                              font=font, fill=GOLD, tags=("score", "number", f"bar{bar.number}"))

    def path(self, d: str, width: float = 1.1, stroke: str = "#111") -> None:
        if self.measuring:
            return
        s = self.scale
        lines = [[(x * s, y * s) for x, y in line] for line in flatten(d)]
        image, left, top = self.view.curve(lines, max(0.9, width * s), COLOURS.get(stroke, stroke))
        self.view.create_image(left, top, anchor="nw", image=image, tags=self._tags("curve"))


class ScoreView(ScrollingPage, tk.Canvas):
    """The score of the 简谱 page.  ``on_note(index)`` is told of a note clicked."""

    def __init__(self, parent: tk.Misc, theme: Theme, on_note: Callable[[int], None]):
        self.theme, self.on_note = theme, on_note
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, borderwidth=0, width=theme.px(300),
                         height=theme.px(300), yscrollincrement=theme.px(16), cursor="arrow")
        self.scale = SCALE * theme.scale
        self.sheet: jianpu.Sheet | None = None
        self.notes: list[Note] = []
        self.inked: dict[int, str] = {}  # what is drawn in a colour of its own, by item: that colour
        self.dotted: dict[int, tuple[float, str]] = {}  # the dots, by item: their radius and colour
        self.fonts: dict[tuple, tkfont.Font] = {}
        self.metric: dict[str, tuple[int, int]] = {}
        self.widths: dict[tuple, int] = {}
        self.dots: dict[tuple, tk.PhotoImage] = {}
        self.curves: dict[tuple, tuple[tk.PhotoImage, int, int]] = {}
        self.tints: dict[tuple, tk.PhotoImage] = {}
        self.staves: list[list[tuple[float, float, int]]] = []  # per staff: (at, until, note), in order
        self.rows: list[tuple[float, float]] = []  # each system's top and foot, in pixels
        self.pending: list[tuple] = []  # the systems measured but not drawn yet: (row, system, top, first note, end)
        self.drawing: str | None = None  # the next few systems' turn to be drawn
        self.laid_width = 0
        self.most: int | None = None  # bars a line at most: jianpu's own choice, or four in the wide window
        self.job: str | None = None
        self.lit: list[int] = []  # the notes shown as being heard
        self.hovered: int | None = None
        self.lit_items: list[int] = []
        self.hover_item = self.create_image(0, 0, anchor="nw", state="hidden")
        self.reader_scrolled = -math.inf  # when the reader last scrolled
        self.glide: str | None = None
        self.make_fades()
        self.bind("<Configure>", lambda _: self.later())
        self.bind("<Button-1>", self.click)
        self.bind("<Motion>", self.hover)
        self.bind("<Leave>", lambda _: self.hover_on(None))
        self.take_page(theme)

    # -- drawing --
    def font(self, kind: str, pixels: float, italic: bool = False) -> tkfont.Font:
        size = max(6, round(pixels))
        key = (kind, size, italic)
        if key not in self.fonts:
            fonts = self.theme.fonts
            if kind == "digits":
                family, weight = (fonts.serif, "normal") if fonts.serif else ("Cambria", "bold")
            else:
                family, weight = fonts.figures[0], "normal"
            self.fonts[key] = tkfont.Font(root=self, family=family, size=-size, weight=weight,
                                          slant="italic" if italic else "roman")
        return self.fonts[key]

    def metrics(self, font: tkfont.Font) -> tuple[int, int]:
        """The font's ascent and descent."""
        key = str(font)
        if key not in self.metric:
            self.metric[key] = (font.metrics("ascent"), font.metrics("descent"))
        return self.metric[key]

    def width_of(self, font: tkfont.Font, text: str) -> int:
        key = (str(font), text)
        if key not in self.widths:
            self.widths[key] = font.measure(text)
        return self.widths[key]

    def dot(self, radius: float, colour: str) -> tk.PhotoImage:
        key = (radius, colour)
        if key not in self.dots:
            size = math.ceil(2 * radius) + 2
            self.dots[key] = self.theme.image(f"score-dot:{colour}:{radius}", size, size,
                                              lambda: [(colour, icons.circle(size / 2, size / 2, radius))])
        return self.dots[key]

    def curve(self, lines: list[list[tuple[float, float]]], pen: float, colour: str) -> tuple[tk.PhotoImage, int, int]:
        """A smoothed curve: the same shape drawn once wherever it falls, a quarter pixel either way."""
        ox, oy = math.floor(lines[0][0][0]), math.floor(lines[0][0][1])
        key = (round(pen, 2), colour,
               tuple(tuple((round((x - ox) * 4), round((y - oy) * 4)) for x, y in line) for line in lines))
        if key not in self.curves:
            data, left, top, _, _ = ink([[(x - ox, y - oy) for x, y in line] for line in lines], pen, colour)
            self.curves[key] = (tk.PhotoImage(master=self, data=data), left, top)
        image, left, top = self.curves[key]
        return image, ox + left, oy + top

    def tint(self, colour: str, width: int, height: int) -> tk.PhotoImage:
        """A rounded tint, as the song list lays under the chosen song and the one under the pointer."""
        key = (colour, width, height)
        if key not in self.tints:
            radius = self.theme.px(4)
            data = icons.png(width, height, icons.rgba(width, height, [(colour, icons.rect(0, 0, width, height, radius))]))
            self.tints[key] = tk.PhotoImage(master=self, data=data)
        return self.tints[key]

    def register(self, bar, staff: int, symbol) -> int:
        self.notes.append(Note(staff, bar.start + symbol.start, symbol.length, symbol.struck))
        return len(self.notes) - 1

    keep_top: float | None = None  # where a score just shown is to be scrolled to, in pixels

    def show(self, sheet: jianpu.Sheet | None, top: float = 0.0) -> None:
        """Draw ``sheet`` (or nothing) for the view's width now, scrolled ``top`` pixels down: from the top,
        or, for the same song drawn again, where it was."""
        self.sheet = sheet
        self.lit, self.hovered, self.lit_at, self.timing, self.staves = [], None, None, None, []
        self.lit_bar, self.bar_starts = None, []
        self.keep_top = top
        self.lay_out()

    def width_now(self) -> int:
        width = self.winfo_width()
        return width if width > 1 else int(self.cget("width"))

    def later(self) -> None:
        """The view changed size: once it holds still (and the window too), lay the score out again for a new
        width.  Not while it is on a page not shown."""
        top = self.winfo_toplevel()
        if self.job is not None:
            top.after_cancel(self.job)
        self.job = top.after(150, self.settle)

    def resizing(self) -> bool:
        return self.page is not None and self.page.later is not None

    def settle(self) -> None:
        self.job = None
        if self.away:
            return
        if self.resizing():  # the window is still being dragged: wait for it
            self.later()
        elif self.width_now() != self.laid_width:
            self.lay_out()

    def lay_out(self) -> None:
        """Lay the score out for the view's width: every note measured now, so that where each one is and when
        it sounds are known at once; the systems in view drawn now, the rest a few at a time (``draw_some``)."""
        self.stop_drawing()
        self.delete("score")
        for item in self.lit_items:
            self.delete(item)
        self.lit_items = []
        self.itemconfigure(self.hover_item, state="hidden", image="")
        self.hovered = None
        self.notes, self.inked, self.dotted, self.rows, self.pending = [], {}, {}, [], []
        self.curves, self.tints = {}, {}  # (their pictures are freed with them: what showed them is gone)
        width = self.laid_width = self.width_now()
        sheet = self.sheet
        if sheet is None:
            self.extent = 0
            self.configure(scrollregion=(0, 0, width, 0))
            return
        s = self.scale
        systems = jianpu.layout_rows(sheet, LEFT, max(width / s - RIGHT, LEFT + 60), self.most)
        measure = Drawing(self, s)
        y = float(TOP)
        for row, system in enumerate(systems):
            top, first = y, len(self.notes)
            y = jianpu.draw_system(measure, sheet, system, row, y, {}, rule=GOLD)
            self.rows.append((top * s, (y - 14) * s))
            self.pending.append((row, system, top, first, len(self.notes)))
        self.extent = round((y - 20 + FOOT) * s)
        self.configure(scrollregion=(0, 0, width, self.extent))
        if self.keep_top is not None:  # (before any is drawn: what comes into view is drawn first)
            room = max(self.extent - max(self.winfo_height(), 1), 0)
            self.yview_moveto(min(max(self.keep_top, 0.0), room) / self.extent if self.extent else 0.0)
            self.keep_top = None
        if self.timing is not None:
            self.time(*self.timing)
        self.light(self.lit_at)
        self.draw_some()
        if self.paper is not None:
            self.lay_paper(*self.paper)

    def four_bars(self) -> tuple[int, int] | None:
        """How big the view must be for its score laid out four bars a line: as wide as the widest of those
        lines, as tall as the tallest four lines next to one another (with room for the fades over the
        lines beyond them), or as tall as all of it if it has four lines or fewer.  None with no score."""
        sheet = self.sheet
        if sheet is None or not sheet.bars:
            return None
        s = self.scale
        groups = [sheet.bars[at:at + 4] for at in range(0, len(sheet.bars), 4)]
        line = max(sum(jianpu.bar_width(bar) for bar in group) for group in groups)
        width = math.ceil((LEFT + line + RIGHT + 1) * s)
        steps = [jianpu.system_height(sheet, group) + 30 for group in groups]  # one system's top to the next's
        if len(steps) <= 4:
            return width, math.ceil((TOP + sum(steps) - 20 + FOOT) * s)
        tallest = max(sum(steps[at:at + 4]) for at in range(len(steps) - 3)) - 14
        return width, math.ceil(tallest * s) + 2 * self.theme.px(18)

    def stop_drawing(self) -> None:
        if self.drawing is not None:
            self.winfo_toplevel().after_cancel(self.drawing)
            self.drawing = None

    def draw_row(self, entry: tuple) -> None:
        row, system, top, first, end = entry
        jianpu.draw_system(Drawing(self, self.scale, first), self.sheet, system, row, top, {}, rule=GOLD)
        for index in self.lit:
            if first <= index < end:
                self.colour(index, ORANGE)
        if self.lit_bar is not None:
            self.itemconfigure(f"bar{self.lit_bar}", fill=ORANGE)

    def draw_some(self) -> None:
        """Draw systems for ``SLICE``, those in view first, from the top, then the others nearest the view;
        the rest at the next turn, so that the window never waits long.  Not while the window is being
        dragged or the view's page is not shown."""
        self.drawing = None
        if not self.pending:
            return
        top = self.winfo_toplevel()
        if self.away or self.resizing():
            self.drawing = top.after(80, self.draw_some)
            return
        began = time.perf_counter()
        view_top = self.canvasy(0)
        view_foot = view_top + max(self.winfo_height(), 1)
        while self.pending:
            shown = [entry for entry in self.pending
                     if self.rows[entry[0]][0] < view_foot and self.rows[entry[0]][1] > view_top]
            entry = shown[0] if shown else min(self.pending, key=lambda e: abs(self.rows[e[0]][0] - view_top))
            self.pending.remove(entry)
            self.draw_row(entry)
            if time.perf_counter() - began >= SLICE:  # (one system at least each turn)
                break
        self.tag_raise("fade")
        self.tag_raise("over")
        if self.pending:
            self.drawing = top.after(1, self.draw_some)

    def finish(self) -> None:
        """Draw every system not drawn yet, now."""
        self.stop_drawing()
        while self.pending:
            self.draw_row(self.pending.pop(0))
        self.tag_raise("fade")
        self.tag_raise("over")

    timing: tuple | None = None
    lit_at: float | None = None

    def time(self, score: Score, speed: float, presses: list[float]) -> None:
        """When each note sounds at ``speed``; each starts from the nearest of ``presses`` (the seconds keys
        are pressed at) within ``SNAP``: a recording's notes are moved a little on the page to read well."""
        self.timing = (score, speed, presses)
        tempo = score.tempo_map(speed)
        self.bar_starts = [tempo.seconds(bar.start) for bar in self.sheet.bars] if self.sheet else []
        self.staves = []
        for index, note in enumerate(self.notes):
            note.at = tempo.seconds(note.beat)
            note.until = tempo.seconds(note.beat + note.length)
            place = bisect_right(presses, note.at)
            near = [presses[i] for i in (place - 1, place) if 0 <= i < len(presses)]
            nearest = min(near, key=lambda at: abs(at - note.at)) if near else note.at
            note.start = nearest if abs(nearest - note.at) <= SNAP else note.at
            while len(self.staves) <= note.staff:
                self.staves.append([])
            staff = self.staves[note.staff]  # (the notes come bar by bar, so each staff's in order)
            if not note.struck and staff:  # a held note going on: lit as the note it goes on from
                at, until, first = staff[-1]
                staff[-1] = (at, max(until, note.until), first)
            else:
                staff.append((note.at, note.until, index))

    # -- what is heard --
    def heard(self, seconds: float | None, follow: bool = True) -> None:
        """Light the notes sounding ``seconds`` into the song (none for None) and keep them in sight."""
        self.lit_at = seconds
        self.light(seconds)
        if follow and self.lit and time.monotonic() - self.reader_scrolled > FOLLOW_PAUSE:
            self.keep_in_sight(self.lit)

    def sounding(self, seconds: float) -> list[int]:
        found = []
        for staff in self.staves:
            place = bisect_right(staff, (seconds + 0.02, math.inf, 0)) - 1
            if place >= 0:
                at, until, index = staff[place]
                if seconds < until + 0.05:  # (a rest after it: nothing lit there)
                    found.append(index)
        return found

    lit_bar: int | None = None  # the number of the bar being heard, lit too
    bar_starts: list[float] = []  # when each bar begins, in seconds

    def light(self, seconds: float | None) -> None:
        bar = None
        if seconds is not None and self.sheet is not None and self.bar_starts:
            at = bisect_right(self.bar_starts, seconds + 1e-6) - 1
            bar = self.sheet.bars[max(at, 0)].number
        if bar != self.lit_bar:
            if self.lit_bar is not None:
                self.itemconfigure(f"bar{self.lit_bar}", fill=GOLD)
            if bar is not None:
                self.itemconfigure(f"bar{bar}", fill=ORANGE)
            self.lit_bar = bar
        lit = self.sounding(seconds) if seconds is not None else []
        if lit == self.lit and (self.lit_items or not lit):
            return
        for index in self.lit:
            self.colour(index, None)
        for item in self.lit_items:
            self.delete(item)
        self.lit, self.lit_items = lit, []
        for index in lit:
            self.colour(index, ORANGE)
            self.lit_items.append(self.ground(index, CHOSEN_TINT))
        self.tag_raise("fade")
        self.tag_raise("over")

    def colour(self, index: int, colour: str | None) -> None:
        for item in self.find_withtag(f"n{index}"):
            if item in self.inked:
                self.itemconfigure(item, fill=colour or self.inked[item])
            elif item in self.dotted:
                radius, own = self.dotted[item]
                self.itemconfigure(item, image=self.dot(radius, colour or own))

    def ground(self, index: int, colour: str) -> int:
        """A rounded tint under note ``index``."""
        left, top, right, bottom = self.notes[index].box
        pad = self.theme.px(3)
        width, height = round(right - left) + 2 * pad, round(bottom - top) + 2 * pad
        item = self.create_image(round(left) - pad, round(top) - pad, anchor="nw",
                                 image=self.tint(colour, max(width, 2), max(height, 2)))
        self.under_score(item)
        return item

    def under_score(self, item: int) -> None:
        """Lay a tint ``item`` over the paper and under the score (some of which may not be drawn yet)."""
        if self.find_withtag("score"):
            self.tag_lower(item, "score")
        if self.find_withtag("paper&&!over"):
            self.tag_raise(item, "paper&&!over")

    def keep_in_sight(self, notes: list[int]) -> None:
        """Scroll, gliding, to bring the system of ``notes`` into view a third of the way down, if it is not
        wholly in view already."""
        if not self.rows or self.extent <= self.winfo_height():
            return
        top = min(self.notes[index].box[1] for index in notes)
        row = next((row for row in self.rows if row[0] <= top <= row[1] + 1), None)
        if row is None:
            return
        view_top, height = self.canvasy(0), self.winfo_height()
        if view_top <= row[0] and row[1] <= view_top + height:
            return
        target = max(0.0, min(row[0] - height / 3, self.extent - height))
        self.glide_to(target)

    def glide_to(self, target: float) -> None:
        if self.glide is not None:
            self.winfo_toplevel().after_cancel(self.glide)
        start = self.canvasy(0)

        def step(index: int = 0) -> None:
            self.glide = None
            self.yview_moveto((start + (target - start) * GLIDE[index]) / self.extent)
            if index + 1 < len(GLIDE):
                self.glide = self.winfo_toplevel().after(16, step, index + 1)
        step()

    # -- the reader --
    def note_at(self, x: float, y: float) -> int | None:
        """The note drawn at (x, y) in the canvas, or nearest to it within a few pixels."""
        reach = self.theme.px(4)
        best, nearest = None, math.inf
        for index, note in enumerate(self.notes):
            left, top, right, bottom = note.box
            if left - reach <= x <= right + reach and top - reach <= y <= bottom + reach:
                distance = abs((left + right) / 2 - x) + abs((top + bottom) / 2 - y) / 4
                if distance < nearest:
                    best, nearest = index, distance
        return best

    def click(self, event: tk.Event) -> None:
        index = self.note_at(self.canvasx(event.x), self.canvasy(event.y))
        if index is not None:
            self.reader_scrolled = -math.inf  # it was chosen to be heard: follow it again
            self.on_note(index)

    def hover(self, event: tk.Event) -> None:
        self.hover_on(self.note_at(self.canvasx(event.x), self.canvasy(event.y)))

    def hover_on(self, index: int | None) -> None:
        if index == self.hovered:
            return
        self.hovered = index
        if index is None:
            self.itemconfigure(self.hover_item, state="hidden")
            self.configure(cursor="arrow")
            return
        left, top, right, bottom = self.notes[index].box
        pad = self.theme.px(3)
        width, height = round(right - left) + 2 * pad, round(bottom - top) + 2 * pad
        self.coords(self.hover_item, round(left) - pad, round(top) - pad)
        self.itemconfigure(self.hover_item, image=self.tint(HOVER_TINT, max(width, 2), max(height, 2)), state="normal")
        self.under_score(self.hover_item)
        self.configure(cursor="hand2")

    def wheel(self, event: tk.Event) -> None:
        if self.extent > self.winfo_height():
            self.read_on()
            self.yview_scroll(-round(event.delta / 120) * 3, "units")

    def read_on(self) -> None:
        """The reader scrolls: leave the view where they take it for a while."""
        self.reader_scrolled = time.monotonic()
        if self.glide is not None:
            self.winfo_toplevel().after_cancel(self.glide)
            self.glide = None
