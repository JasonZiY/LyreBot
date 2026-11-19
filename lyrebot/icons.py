"""Small anti-aliased images for buttons, menus and the song list, drawn in pure Python at any scale.

Tk's canvas does not smooth edges on Windows and font glyphs like ▶ sit off
centre, so the images are rasterised here instead.  Each pixel is sampled on
a 4 × 4 grid; the samples that fall inside a shape set its colour and the
share of them its opacity.  The result is a PNG with an alpha channel, which
Tk 8.6 blends onto whatever lies beneath, so one image serves on paper, on a
highlight and on a hover circle alike.

Shapes are functions of a point.  The icons below are laid out on a square
of side ``size``; ``pill`` and ``rect`` take any width and height.

The icons are one family, drawn like an engraving with a little gilding:
fine lines of one weight with round ends, hollow rather than filled, and in
most of them one small part marked ``gilt`` to be laid in old gold.  Only
play and record stay solid, in orange: the two things the window most wants
pressed.
"""

from __future__ import annotations

import math
import struct
from typing import Callable, Iterable, Union
import zlib

Shape = Callable[[float, float], bool]
Layers = list[tuple[str, Shape]]
Part = Union[Shape, tuple[str, Shape]]  # a line of the icon, or ("gilt", shape): its touch of gold
SAMPLES = 4  # per axis, per pixel
_OFFSETS = [(i + 0.5) / SAMPLES for i in range(SAMPLES)]
_CORNERS = [(0, 0), (SAMPLES - 1, 0), (0, SAMPLES - 1), (SAMPLES - 1, SAMPLES - 1)]


# ---- shapes -----------------------------------------------------------------

def circle(cx: float, cy: float, r: float) -> Shape:
    return lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def ring(cx: float, cy: float, r: float, width: float) -> Shape:
    """An annulus whose middle runs at radius ``r``."""
    inner, outer = (r - width / 2) ** 2, (r + width / 2) ** 2
    return lambda x, y: inner <= (x - cx) ** 2 + (y - cy) ** 2 <= outer


def rect(x0: float, y0: float, x1: float, y1: float, radius: float = 0.0) -> Shape:
    def inside(x: float, y: float) -> bool:
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        dx = max(x0 + radius - x, 0.0, x - (x1 - radius))
        dy = max(y0 + radius - y, 0.0, y - (y1 - radius))
        return dx * dx + dy * dy <= radius * radius
    return inside


def outline(x0: float, y0: float, x1: float, y1: float, radius: float, width: float) -> Shape:
    """The edge of a rounded rectangle, ``width`` thick, drawn inside it."""
    outer = rect(x0, y0, x1, y1, radius)
    inner = rect(x0 + width, y0 + width, x1 - width, y1 - width, max(radius - width, 0.0))
    return lambda x, y: outer(x, y) and not inner(x, y)


def polygon(*points: tuple[float, float]) -> Shape:
    """A convex polygon with its points listed clockwise (y grows downwards)."""
    edges = list(zip(points, points[1:] + points[:1]))

    def inside(x: float, y: float) -> bool:
        return all((bx - ax) * (y - ay) - (by - ay) * (x - ax) >= 0 for (ax, ay), (bx, by) in edges)
    return inside


def triangle(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> Shape:
    """A triangle with its corners in any order."""
    turn = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return polygon(a, b, c) if turn >= 0 else polygon(a, c, b)


def stroke(points: Iterable[tuple[float, float]], width: float | list[float]) -> Shape:
    """A line through ``points`` with round ends and joins; ``width`` may be one per point, for a line
    that swells and thins like a pen's.

    A long curve is many short pieces, so they are sorted into square cells first: a point need only be
    tried against the few pieces that pass through its cell, not all of them."""
    path = list(points)
    widths = list(width) if isinstance(width, (list, tuple)) else [width] * len(path)
    segments = list(zip(path, path[1:], widths, widths[1:])) or [(path[0], path[0], widths[0], widths[0])]
    cell = max(4.0, max(widths))
    cells: dict[tuple[int, int], list] = {}
    for segment in segments:
        (ax, ay), (bx, by), wa, wb = segment
        half = max(wa, wb) / 2
        for gx in range(int((min(ax, bx) - half) // cell), int((max(ax, bx) + half) // cell) + 1):
            for gy in range(int((min(ay, by) - half) // cell), int((max(ay, by) + half) // cell) + 1):
                cells.setdefault((gx, gy), []).append(segment)

    def inside(x: float, y: float) -> bool:
        near = cells.get((int(x // cell), int(y // cell)))
        if not near:
            return False
        for (ax, ay), (bx, by), wa, wb in near:
            dx, dy = bx - ax, by - ay
            length = dx * dx + dy * dy
            t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length))
            ex, ey = ax + t * dx - x, ay + t * dy - y
            half = (wa + (wb - wa) * t) / 2
            if ex * ex + ey * ey <= half * half:
                return True
        return False
    return inside


def closed(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The points of an outline, back to where it began."""
    return points + points[:1]


def rounded_triangle(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float],
                     radius: float) -> Shape:
    """A triangle with its corners rounded: a smaller one, its sides moved in by ``radius``, grown back
    out by a pen of that radius."""
    sides = math.dist(b, c), math.dist(a, c), math.dist(a, b)
    around = sum(sides)
    centre = (sum(side * p[0] for side, p in zip(sides, (a, b, c))) / around,
              sum(side * p[1] for side, p in zip(sides, (a, b, c))) / around)
    area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
    inner = 2 * area / around
    k = max(inner - radius, 0.0) / inner
    points = [(centre[0] + (p[0] - centre[0]) * k, centre[1] + (p[1] - centre[1]) * k) for p in (a, b, c)]
    return union(triangle(*points), stroke(closed(points), 2 * radius))


def bezier(points: list[tuple[float, float]], steps: int = 24) -> list[tuple[float, float]]:
    out = []
    for step in range(steps + 1):
        t, pts = step / steps, points
        while len(pts) > 1:
            pts = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) for a, b in zip(pts, pts[1:])]
        out.append(pts[0])
    return out


def union(*shapes: Shape) -> Shape:
    return lambda x, y: any(shape(x, y) for shape in shapes)


def gilt(shape: Shape) -> tuple[str, Shape]:
    """Mark ``shape`` as the part of an icon laid in gold."""
    return ("gilt", shape)


def paint(parts: Part | Iterable[Part], color: str, gold: str | None = None) -> Layers:
    """Layers for an icon's parts: its lines in ``color``, its gilt in ``gold`` (or ``color`` too)."""
    if callable(parts) or (isinstance(parts, tuple) and parts and parts[0] == "gilt"):
        parts = [parts]
    return [(gold or color, part[1]) if isinstance(part, tuple) else (color, part) for part in parts]


# ---- rendering ----------------------------------------------------------------

def rgba(width: int, height: int, layers: Layers) -> bytes:
    """RGBA pixels of ``layers`` (later ones over earlier ones) on nothing.  A layer's colour may carry its
    own opacity ("#RRGGBBAA"), for a tint that lets what lies under the image show through."""
    colors = [_rgb(color) for color, _ in layers]
    opacity = [int(color[7:9], 16) / 255 if len(color) == 9 else 1.0 for color, _ in layers]
    shapes = [shape for _, shape in layers]
    top = len(shapes) - 1

    def pick(x: float, y: float) -> int:
        for index in range(top, -1, -1):
            if shapes[index](x, y):
                return index
        return -1

    out = bytearray()
    n = SAMPLES * SAMPLES
    for py in range(height):
        for px in range(width):
            corners = [pick(px + _OFFSETS[i], py + _OFFSETS[j]) for i, j in _CORNERS]
            if corners.count(corners[0]) == 4:  # the same all over: no edge runs through it
                hits = {corners[0]: n}
            else:
                hits = {}
                for oy in _OFFSETS:
                    for ox in _OFFSETS:
                        index = pick(px + ox, py + oy)
                        hits[index] = hits.get(index, 0) + 1
            covered = sum(opacity[i] * count for i, count in hits.items() if i >= 0)
            if not covered:
                out += b"\x00\x00\x00\x00"
                continue
            red = sum(colors[i][0] * opacity[i] * count for i, count in hits.items() if i >= 0) / covered
            green = sum(colors[i][1] * opacity[i] * count for i, count in hits.items() if i >= 0) / covered
            blue = sum(colors[i][2] * opacity[i] * count for i, count in hits.items() if i >= 0) / covered
            out += bytes((round(red), round(green), round(blue), round(255 * covered / n)))
    return bytes(out)


def png(width: int, height: int, pixels: bytes) -> bytes:
    """``pixels`` (RGBA, row by row) as a PNG file."""
    stride = width * 4
    raw = b"".join(b"\x00" + pixels[row * stride:(row + 1) * stride] for row in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def render(size: int, background: str, layers: Layers) -> list[str]:
    """Opaque Tk ``PhotoImage.put`` rows of ``layers`` on ``background``."""
    base = _rgb(background)
    pixels = rgba(size, size, layers)
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            r, g, b, a = pixels[(py * size + px) * 4:(py * size + px) * 4 + 4]
            mix = [round(base[k] + (c - base[k]) * a / 255) for k, c in enumerate((r, g, b))]
            row.append("#%02x%02x%02x" % tuple(mix))
        rows.append("{" + " ".join(row) + "}")
    return rows


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


# ---- icons ----------------------------------------------------------------------
#
# Drawn for the pixel grid.  Lines are a whole number of pixels wide, and a level or upright line runs
# through the middle of a row or column of pixels (or along the edge between two, when it is an even number
# wide), so that it fills them solidly instead of smearing over two in half tones.  Only slanting lines
# and curves are left to the smoothing, and their ends sit in the middle of a pixel.

def pen_width(size: float, share: float = 0.075) -> int:
    """The family's pen in whole pixels: a set share of the icon, at least one pixel."""
    return max(1, round(size * share))


def snap(v: float, pen: int) -> float:
    """``v`` moved onto the pixel grid: to the middle of a pixel for a line an odd number of pixels wide,
    to the edge between two for an even one, so that the line covers whole pixels."""
    return math.floor(v) + 0.5 if pen % 2 else float(round(v))


def crisp(points: list[tuple[float, float]], pen: int) -> Shape:
    """A line through ``points`` laid on the pixel grid: its level and upright runs as solid bars meeting
    in square corners, any slanting run as an ordinary stroke."""
    path = [(snap(x, pen), snap(y, pen)) for x, y in points]
    half = pen / 2
    runs = []
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        if ax == bx or ay == by:
            runs.append(rect(min(ax, bx) - half, min(ay, by) - half, max(ax, bx) + half, max(ay, by) + half))
        else:
            runs.append(stroke([(ax, ay), (bx, by)], pen))
    return union(*runs)


def block(x0: float, y0: float, x1: float, y1: float, radius: float = 0.0) -> Shape:
    """A rectangle with its edges on whole pixels."""
    return rect(round(x0), round(y0), round(x1), round(y1), radius)


def play(size: int) -> Shape:
    """A right-pointing triangle with softened corners, placed by eye rather than by its box, so it looks
    centred."""
    c = size / 2
    w, h = size * 0.32, size * 0.36
    left = c - w * 0.42
    return rounded_triangle((left, c - h / 2), (left + w, c), (left, c + h / 2), size * 0.035)


def stop(size: int) -> Shape:
    half = round(size * 0.13)
    left = round(size / 2 - half)
    return rect(left, left, left + 2 * half, left + 2 * half, radius=size * 0.03)


def next_track(size: int) -> list[Part]:
    """⏭ in line: a hollow triangle, and the bar it stops at in gilt."""
    pen = pen_width(size, 0.052)
    mid = snap(size / 2, pen)
    half = round(size * 0.13)
    left = snap(size / 2 - size * 0.15, pen)
    tip, bar = left + round(size * 0.20), left + round(size * 0.20) + round(size * 0.10)
    return [crisp(closed([(left, mid - half), (tip, mid), (left, mid + half)]), pen),
            gilt(rect(bar - pen / 2, mid - half - pen / 2, bar + pen / 2, mid + half + pen / 2))]


def restart(size: int) -> list[Shape]:
    """↺ in line: a ring open near the top left, and at its end, at about eleven o'clock, an arrowhead
    pointing anticlockwise.  No gilt: an arrowhead of another colour comes loose from its ring."""
    pen = pen_width(size, 0.052)
    c, radius = snap(size / 2, pen), round(size * 0.20)
    head, tail = math.radians(-115), math.radians(185)  # screen angles, clockwise from three o'clock
    steps = 40
    arc = [(c + radius * math.cos(head + (tail - head) * i / steps), c + radius * math.sin(head + (tail - head) * i / steps))
           for i in range(steps + 1)]
    end = arc[0]
    ahead = (math.sin(head), -math.cos(head))  # the way an anticlockwise turn goes from there
    out = (math.cos(head), math.sin(head))
    reach = size * 0.11
    tip = (end[0] + ahead[0] * reach * 0.45, end[1] + ahead[1] * reach * 0.45)
    wings = [(tip[0] + (-ahead[0] * 0.8 + out[0] * side) * reach, tip[1] + (-ahead[1] * 0.8 + out[1] * side) * reach)
             for side in (0.75, -0.75)]
    return [stroke(arc, pen), stroke([wings[0], tip, wings[1]], pen)]


def keyboard(size: int) -> list[Part]:
    """A keyboard in line: the case, a row of four keys, and the space bar in gilt, all on whole pixels and
    evenly spaced."""
    pen = pen_width(size, 0.052)
    key = max(1, round(size * 0.06))  # a key's side; the gaps between and around them are as wide
    width, height = 9 * key + 2 * pen, 4 * key + pen + 2 * pen
    x0, y0 = round((size - width) / 2), round((size - height) / 2)
    case = outline(x0, y0, x0 + width, y0 + height, size * 0.08, pen)
    inner_x, row = x0 + pen + key, y0 + pen + key
    keys = [block(inner_x + i * 2 * key, row, inner_x + i * 2 * key + key, row + key) for i in range(4)]
    space = gilt(block(inner_x + 2 * key, row + 2 * key, inner_x + 5 * key, row + 2 * key + pen))
    return [case, *keys, space]


def record(size: int) -> tuple[Shape, Shape]:
    """● with a fine ring round it: (ring, dot)."""
    c = size / 2
    return ring(c, c, size * 0.31, max(1, round(size * 0.045))), circle(c, c, size * 0.16)


def chevron(size: int, down: bool = False) -> Shape:
    """› or ⌄: two strokes at right angles, each end in the middle of a pixel, so the slopes step evenly."""
    reach = max(2, round(size * 0.2))
    c = math.floor(size / 2) + 0.5
    point = math.floor(c + reach / 2) + 0.5
    weight = max(size * 0.095, 1.35)
    if down:
        return stroke([(c - reach, point - reach), (c, point), (c + reach, point - reach)], weight)
    return stroke([(point - reach, c - reach), (point, c), (point - reach, c + reach)], weight)


def _centre(v: float) -> float:
    return math.floor(v) + 0.5


def check(size: int) -> list[Shape]:
    """✓ as a pen makes it: the short stroke finer, the long one thinning as it lifts."""
    s = size
    weight = max(s * 0.1, 1.25)
    start, low = (_centre(s * 0.24), _centre(s * 0.53)), (_centre(s * 0.43), _centre(s * 0.71))
    end = (low[0] + round(s * 0.35), low[1] - round(s * 0.35))  # the long stroke at 45°
    return [stroke([start, low], weight * 0.8), stroke([low, end], [weight, weight * 0.7])]


def magnifier(size: int) -> list[Part]:
    """A reading glass: a fine ring, and its handle in gilt."""
    s, pen = size, pen_width(size)
    c, radius = _centre(s * 0.43), round(s * 0.25)
    grip = _centre(c + radius * 0.75)
    return [ring(c, c, radius, pen + 0.25),
            gilt(stroke([(grip, grip), (grip + round(s * 0.2), grip + round(s * 0.2))], pen * 1.6))]


def _folder(s: float) -> list[tuple[float, float]]:
    return closed([(s * 0.14, s * 0.78), (s * 0.14, s * 0.24), (s * 0.40, s * 0.24), (s * 0.48, s * 0.33),
                   (s * 0.86, s * 0.33), (s * 0.86, s * 0.78)])


def folder(size: int) -> list[Part]:
    """A folder in line, the edge of its front in gilt."""
    s, pen = size, pen_width(size)
    front, left, right = snap(s * 0.46, pen), snap(s * 0.14, pen), snap(s * 0.86, pen)
    return [crisp(_folder(s), pen), gilt(rect(left + pen / 2, front - pen / 2, right - pen / 2, front + pen / 2))]


def move_to(size: int) -> list[Part]:
    """A folder in line with a gilt arrow going into it."""
    s, pen = size, pen_width(size)
    y, tip, tail = snap(s * 0.565, pen), snap(s * 0.64, pen), snap(s * 0.33, pen)
    head = max(2, round(s * 0.11))
    return [crisp(_folder(s), pen),
            gilt(rect(tail - pen / 2, y - pen / 2, tip + pen / 2, y + pen / 2)),
            gilt(stroke([(tip - head, y - head), (tip, y), (tip - head, y + head)], pen))]


def quill(size: int) -> list[Part]:
    """A quill: the feather in line, its shaft in gilt running out to the nib.  Writing the score anew."""
    s, pen = size, pen_width(size)
    start, end = (s * 0.33, s * 0.67), (s * 0.87, s * 0.13)
    along = (end[0] - start[0], end[1] - start[1])
    span = math.hypot(*along)
    across = (along[1] / span, -along[0] / span)  # towards the top left
    steps = 18
    upper, lower = [], []
    for i in range(steps + 1):
        t = i / steps
        swell = math.sin(math.pi * t ** 0.8) ** 0.9
        x, y = start[0] + along[0] * t, start[1] + along[1] * t
        upper.append((x + across[0] * s * 0.17 * swell, y + across[1] * s * 0.17 * swell))
        lower.append((x - across[0] * s * 0.09 * swell, y - across[1] * s * 0.09 * swell))
    vane = stroke(upper + lower[::-1], pen + 0.25)
    nib, top = _centre(s * 0.13), _centre(s * 0.30)
    shaft = gilt(stroke([(nib, _centre(s * 0.87)), (nib + (_centre(s * 0.87) - top), top)], pen))
    return [vane, shaft]


def score_page(size: int) -> list[Part]:
    """A page of music with a turned-down corner, its note in gilt: 简谱."""
    s, pen = size, pen_width(size)
    page = crisp(closed([(s * 0.24, s * 0.10), (s * 0.60, s * 0.10), (s * 0.78, s * 0.28), (s * 0.78, s * 0.90),
                         (s * 0.24, s * 0.90)]), pen)
    corner, fold = snap(s * 0.60, pen), snap(s * 0.10, pen)
    side = snap(s * 0.78, pen)
    turned = crisp([(corner, fold), (corner, fold + side - corner), (side, fold + side - corner)], pen)
    stem, low, high = snap(s * 0.52, pen), snap(s * 0.70, pen), snap(s * 0.40, pen)
    head = max(1.1, s * 0.08)
    return [page, turned,
            gilt(circle(stem - head + pen / 2, low, head)),
            gilt(rect(stem - pen / 2, high - pen / 2, stem + pen / 2, low)),
            gilt(stroke([(stem, high), (stem + round(s * 0.11), high + round(s * 0.11))], pen))]


def minimise(size: int) -> Shape:
    """— for the title bar: one level line on whole pixels."""
    pen = pen_width(size, 0.052)
    reach, middle = round(size * 0.15), size / 2
    return crisp([(middle - reach, middle), (middle + reach, middle)], pen)


def cross(size: int) -> list[Shape]:
    """× for the title bar: two strokes at 45° crossing in the middle, their ends in the middle of pixels."""
    pen = pen_width(size, 0.052)
    c = _centre(size / 2) if pen % 2 else float(round(size / 2))
    reach = round(size * 0.13)
    return [stroke([(c - reach, c - reach), (c + reach, c + reach)], pen),
            stroke([(c - reach, c + reach), (c + reach, c - reach)], pen)]


def trash(size: int) -> list[Shape]:
    """A bin in line: the lid with its handle, the body, two grooves."""
    s, pen = size, pen_width(size, 0.07)
    return [crisp([(s * 0.20, s * 0.27), (s * 0.80, s * 0.27)], pen),
            crisp([(s * 0.40, s * 0.27), (s * 0.40, s * 0.15), (s * 0.60, s * 0.15), (s * 0.60, s * 0.27)], pen),
            crisp([(s * 0.27, s * 0.36), (s * 0.27, s * 0.86), (s * 0.73, s * 0.86), (s * 0.73, s * 0.36)], pen),
            crisp([(s * 0.43, s * 0.46), (s * 0.43, s * 0.74)], pen),
            crisp([(s * 0.57, s * 0.46), (s * 0.57, s * 0.74)], pen)]


def pen_nib(size: int) -> list[Part]:
    """A pen's nib in line, slit to its point, its breather hole in gilt: a speed of one's own."""
    s, pen = size, pen_width(size)
    c = snap(s / 2, pen)
    top, shoulder, point = snap(s * 0.10, pen), snap(s * 0.45, pen), snap(s * 0.90, pen)
    narrow, wide = round(s * 0.14), round(s * 0.27)
    nib = crisp(closed([(c - narrow, top), (c + narrow, top), (c + wide, shoulder), (c, point), (c - wide, shoulder)]),
                pen)
    hole = snap(s * 0.50, pen)
    return [nib, rect(c - pen / 2, hole + round(s * 0.12), c + pen / 2, point),
            gilt(circle(c, hole, max(s * 0.08, 1.0)))]


def fleuron(width: int, height: int) -> list[Shape]:
    """·◆·: a small lozenge between two dots, the ornament set into a rule."""
    c, m = math.floor(width / 2) + 0.5, height / 2
    half_w, half_h = height * 0.30, height * 0.48
    dot, gap = max(height * 0.13, 0.7), round(height * 0.95)
    return [polygon((c, m - half_h), (c + half_w, m), (c, m + half_h), (c - half_w, m)),
            circle(c - gap, m, dot), circle(c + gap, m, dot)]


def fleuron_width(height: int) -> int:
    """How wide ``fleuron`` needs its picture to be at this height: an odd number, so it has a middle."""
    return int(math.ceil(height * 2.3)) | 1


def warning(size: int) -> list[Shape]:
    """⚠ in line: a triangle with round corners, and the ! inside it."""
    s, pen = size, pen_width(size, 0.085)
    c = snap(s / 2, pen)
    top, bottom, half = snap(s * 0.10, pen), snap(s * 0.86, pen), round(s * 0.44)
    upper, lower, dot = snap(s * 0.40, pen), snap(s * 0.60, pen), snap(s * 0.72, pen)
    return [crisp(closed([(c, top), (c + half, bottom), (c - half, bottom)]), pen),
            rect(c - pen / 2, upper - pen / 2, c + pen / 2, lower + pen / 2),
            rect(c - pen / 2, dot - pen / 2, c + pen / 2, dot + pen / 2)]


# The lyre of LyreBot's crest, on a square of 1024: the left arm from the sound box up to its horn, which
# ends in a small curl (the right arm mirrors it), the yoke, the sound box and the strings.  The program's
# icon (assets/make_icon.py) draws the same lyre.
LYRE_ARM = [(420, 800), (200, 760), (170, 450), (330, 330), (400, 280), (340, 170), (250, 200)]
LYRE_CURL = [(250, 200), (196, 218), (178, 272), (222, 292), (252, 262)]  # carries on the way the arm goes
LYRE_ARM_WIDTH = (70, 40, 26)  # by the sound box, at the horn, at the curl's end: it thins like a pen's stroke
LYRE_YOKE = ((300, 322), (724, 322), 40)
LYRE_BOX = ((368, 804), (656, 804), 58)
LYRE_STRINGS = ((434, 486, 538, 590), 342, 782, 16)  # across, from, to, thickness


def lyre_arm(steps: int = 40) -> tuple[list[tuple[float, float]], list[float]]:
    """The left arm, its horn's curl included, as points and the width at each, on the square of 1024."""
    thick, thin, finest = LYRE_ARM_WIDTH
    arm, curl = bezier(LYRE_ARM, steps), bezier(LYRE_CURL, steps // 3)[1:]
    widths = [thin + (thick - thin) * (1 - i / steps) ** 0.8 for i in range(steps + 1)]
    widths += [thin + (finest - thin) * (i + 1) / len(curl) for i in range(len(curl))]
    return arm + curl, widths


def lyre_strings(size: float) -> list[float]:
    """Where the strings run across a lyre ``size`` wide: four, or three over the same span when four
    would run together."""
    across = [x * size / 1024 for x in LYRE_STRINGS[0]]
    thread = max(1, round(LYRE_STRINGS[3] * size / 1024))
    if across[1] - across[0] < 2 * thread + 0.5:
        across = [across[0], (across[0] + across[-1]) / 2, across[-1]]
    return [snap(x, thread) for x in across]


def lyre(size: int) -> list[Shape]:
    """The lyre of LyreBot's crest: two arms that thin as they rise and curl at their horns, and the yoke,
    the sound box and the strings on whole pixels."""
    k = size / 1024
    points, widths = lyre_arm()
    widths = [max(w * k, 1.0) for w in widths]
    shapes = [stroke([(x * k, y * k) for x, y in points], widths),
              stroke([((1024 - x) * k, y * k) for x, y in points], widths)]
    for (a, b, thickness) in (LYRE_YOKE, LYRE_BOX):
        bar = max(1, round(thickness * k))
        middle = snap(a[1] * k, bar)
        shapes.append(rect(round(a[0] * k), middle - bar / 2, round(b[0] * k), middle + bar / 2, radius=bar / 2))
    _, top, bottom, thread = LYRE_STRINGS
    line = max(1, round(thread * k))
    shapes += [rect(x - line / 2, round(top * k), x + line / 2, round(bottom * k)) for x in lyre_strings(size)]
    return shapes


# ---- the page's ornaments ----------------------------------------------------------

def frame(unit: float, outer: str, inner: str) -> tuple[Layers, int, int]:
    """The double rule framing the page, as on an old book's page (文武边栏): a heavier line outside, ``outer``,
    and a fine one inside, ``inner``, rounding each corner on arcs of one centre.  Laid out from the page's
    top left corner, running on past any picture.  Also gives the side of the square holding a corner's
    curves, past which the rules run straight, and how deep they lie from the edge.  The lines are whole
    pixels wide, on whole rows and columns of pixels, as in the icons."""
    pen, fine = max(1, round(1.6 * unit)), max(1, round(0.8 * unit))
    edge, gap = max(1, round(2.4 * unit)), max(2, round(2.4 * unit))
    radius = max(pen + gap + fine + 1, round(7 * unit))
    inside, far = edge + pen + gap, 1 << 16
    return ([(outer, outline(edge, edge, far, far, radius, pen)),
             (inner, outline(inside, inside, far, far, radius - (inside - edge), fine))],
            edge + radius + 1, inside + fine)


def placed(layers: Layers, dx: float = 0, dy: float = 0, width: float | None = None,
           height: float | None = None) -> Layers:
    """``layers`` as a picture shows them: mirrored left for right across a picture ``width`` wide, or head
    for foot across one ``height`` tall, where those are given, and moved on by (dx, dy)."""
    def view(shape: Shape) -> Shape:
        return lambda x, y: shape((x if width is None else width - x) + dx, (y if height is None else height - y) + dy)
    return [(color, view(shape)) for color, shape in layers]


def ribbon(band: int, length: int, notch: int, drop: int, body: str, fold: str, tuck: str,
           shade: str, shade_far: str) -> Layers:
    """A bookmark's ribbon, ``band`` wide, hanging ``length`` down from the top of its picture: its end cut
    in a swallowtail ``notch`` deep, its right half in the shade of a fold, darker where it comes out from
    under the cover, and casting a soft shadow ``drop`` down and to the right.  The picture is ``band +
    drop + 1`` wide and ``length + drop + 1`` tall."""
    def halves(dx: float, dy: float) -> tuple[Shape, Shape]:  # the swallowtail is not convex: two halves are
        middle = band / 2
        return (polygon((dx, dy), (middle + dx, dy), (middle + dx, length - notch + dy), (dx, length + dy)),
                polygon((middle + dx, dy), (band + dx, dy), (band + dx, length + dy), (middle + dx, length - notch + dy)))
    near, far = halves(drop, drop), halves(drop + 0.8, drop + 0.8)
    left, right = halves(0, 0)
    tucked = max(1, round(drop))
    return [(shade_far, union(*far)), (shade, union(*near)), (body, left), (fold, right),
            (tuck, rect(0, 0, band, tucked))]


def pill(width: int, height: int, fill: str, border: str, line: int = 1) -> Layers:
    """A rounded button face: its border, then its fill inside, the straight edges on whole pixels."""
    radius = height / 2
    face = [(border, rect(0, 0, width, height, radius))]
    if fill != border:
        face.append((fill, rect(line, line, width - line, height - line, radius - line)))
    return face
