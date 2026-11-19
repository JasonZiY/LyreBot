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
    that swells and thins like a pen's."""
    path = list(points)
    widths = list(width) if isinstance(width, (list, tuple)) else [width] * len(path)
    segments = list(zip(path, path[1:], widths, widths[1:])) or [(path[0], path[0], widths[0], widths[0])]
    xs, ys, most = [x for x, _ in path], [y for _, y in path], max(widths)
    box = (min(xs) - most, min(ys) - most, max(xs) + most, max(ys) + most)

    def inside(x: float, y: float) -> bool:
        if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
            return False
        for (ax, ay), (bx, by), wa, wb in segments:
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
    """RGBA pixels of ``layers`` (later ones over earlier ones) on nothing."""
    colors = [_rgb(color) for color, _ in layers]
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
            covered = n - hits.get(-1, 0)
            if not covered:
                out += b"\x00\x00\x00\x00"
                continue
            red = sum(colors[i][0] * count for i, count in hits.items() if i >= 0) / covered
            green = sum(colors[i][1] * count for i, count in hits.items() if i >= 0) / covered
            blue = sum(colors[i][2] * count for i, count in hits.items() if i >= 0) / covered
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

def line_weight(size: float, share: float = 0.075, least: float = 1.15) -> float:
    """The family's pen: a set share of the icon, never finer than a clean line of about a pixel."""
    return max(size * share, least)


def play(size: int) -> Shape:
    """A right-pointing triangle with softened corners, placed by eye rather than by its box, so it looks
    centred."""
    c = size / 2
    w, h = size * 0.32, size * 0.36
    left = c - w * 0.42
    return rounded_triangle((left, c - h / 2), (left + w, c), (left, c + h / 2), size * 0.035)


def stop(size: int) -> Shape:
    c, half = size / 2, size * 0.13
    return rect(c - half, c - half, c + half, c + half, radius=size * 0.03)


def next_track(size: int) -> list[Part]:
    """⏭ in line: a hollow triangle, and the bar it stops at in gilt."""
    c, line = size / 2, line_weight(size, 0.052, 1.2)
    w, h, gap = size * 0.20, size * 0.26, size * 0.10
    left = c - (w + gap) / 2
    return [stroke(closed([(left, c - h / 2), (left + w, c), (left, c + h / 2)]), line),
            gilt(stroke([(left + w + gap, c - h / 2), (left + w + gap, c + h / 2)], line * 1.1))]


def restart(size: int) -> list[Shape]:
    """↺ in line: a ring open near the top left, and at its end, at about eleven o'clock, an arrowhead
    pointing anticlockwise.  No gilt: an arrowhead of another colour comes loose from its ring."""
    c, line = size / 2, line_weight(size, 0.052, 1.2)
    radius = size * 0.20
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
    return [stroke(arc, line), stroke([wings[0], tip, wings[1]], line)]


def keyboard(size: int) -> list[Part]:
    """A keyboard in line: the case, a row of keys, and the space bar in gilt."""
    s, line = size, line_weight(size, 0.052, 1.2)
    case = outline(s * 0.18, s * 0.31, s * 0.82, s * 0.69, s * 0.08, line)
    keys = [circle(s * x, s * 0.445, max(s * 0.028, 0.75)) for x in (0.33, 0.443, 0.557, 0.67)]
    return [case, *keys, gilt(stroke([(s * 0.39, s * 0.575), (s * 0.61, s * 0.575)], line))]


def record(size: int) -> tuple[Shape, Shape]:
    """● with a fine ring round it: (ring, dot)."""
    c = size / 2
    return ring(c, c, size * 0.31, max(size * 0.045, 1.0)), circle(c, c, size * 0.16)


def chevron(size: int, down: bool = False) -> Shape:
    c, a = size / 2, size * 0.2
    weight = max(size * 0.095, 1.1)
    if down:
        return stroke([(c - a, c - a * 0.55), (c, c + a * 0.55), (c + a, c - a * 0.55)], weight)
    return stroke([(c - a * 0.55, c - a), (c + a * 0.55, c), (c - a * 0.55, c + a)], weight)


def check(size: int) -> list[Shape]:
    """✓ as a pen makes it: the short stroke finer, the long one thinning as it lifts."""
    s = size
    weight = max(s * 0.1, 1.25)
    return [stroke([(s * 0.24, s * 0.53), (s * 0.43, s * 0.71)], weight * 0.8),
            stroke([(s * 0.43, s * 0.71), (s * 0.78, s * 0.31)], [weight, weight * 0.7])]


def magnifier(size: int) -> list[Part]:
    """A reading glass: a fine ring, and its handle in gilt."""
    s, line = size, line_weight(size)
    return [ring(s * 0.43, s * 0.43, s * 0.25, line),
            gilt(stroke([(s * 0.62, s * 0.62), (s * 0.82, s * 0.82)], line * 1.6))]


def _folder(s: float) -> list[tuple[float, float]]:
    return closed([(s * 0.14, s * 0.78), (s * 0.14, s * 0.24), (s * 0.40, s * 0.24), (s * 0.48, s * 0.33),
                   (s * 0.86, s * 0.33), (s * 0.86, s * 0.78)])


def folder(size: int) -> list[Part]:
    """A folder in line, the edge of its front in gilt."""
    s, line = size, line_weight(size)
    return [stroke(_folder(s), line), gilt(stroke([(s * 0.14, s * 0.46), (s * 0.86, s * 0.46)], line * 0.85))]


def move_to(size: int) -> list[Part]:
    """A folder in line with a gilt arrow going into it."""
    s, line = size, line_weight(size)
    return [stroke(_folder(s), line),
            gilt(stroke([(s * 0.33, s * 0.56), (s * 0.64, s * 0.56)], line)),
            gilt(stroke([(s * 0.54, s * 0.47), (s * 0.64, s * 0.56), (s * 0.54, s * 0.65)], line))]


def quill(size: int) -> list[Part]:
    """A quill: the feather in line, its shaft in gilt running out to the nib.  Writing the score anew."""
    s, line = size, line_weight(size)
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
    vane = stroke(upper + lower[::-1], line)
    shaft = gilt(stroke([(s * 0.13, s * 0.87), (s * 0.70, s * 0.30)], line * 0.9))
    return [vane, shaft]


def score_page(size: int) -> list[Part]:
    """A page of music with a turned-down corner, its note in gilt: 简谱."""
    s, line = size, line_weight(size)
    page = stroke(closed([(s * 0.24, s * 0.10), (s * 0.60, s * 0.10), (s * 0.78, s * 0.28), (s * 0.78, s * 0.90),
                          (s * 0.24, s * 0.90)]), line)
    fold = stroke([(s * 0.60, s * 0.10), (s * 0.60, s * 0.28), (s * 0.78, s * 0.28)], line * 0.85)
    head = gilt(circle(s * 0.44, s * 0.70, max(s * 0.085, 1.1)))
    stem = gilt(stroke([(s * 0.515, s * 0.70), (s * 0.515, s * 0.40), (s * 0.62, s * 0.47)], line * 0.9))
    return [page, fold, head, stem]


def trash(size: int) -> list[Shape]:
    s = size
    weight = line_weight(size, 0.07, 1.1)
    return [stroke([(s * 0.20, s * 0.27), (s * 0.80, s * 0.27)], weight),
            stroke([(s * 0.40, s * 0.27), (s * 0.42, s * 0.17), (s * 0.58, s * 0.17), (s * 0.60, s * 0.27)], weight),
            stroke([(s * 0.27, s * 0.34), (s * 0.31, s * 0.84), (s * 0.69, s * 0.84), (s * 0.73, s * 0.34)], weight),
            stroke([(s * 0.44, s * 0.44), (s * 0.445, s * 0.73)], weight * 0.85),
            stroke([(s * 0.56, s * 0.44), (s * 0.555, s * 0.73)], weight * 0.85)]


def pen_nib(size: int) -> list[Part]:
    """A pen's nib in line, slit to its point, its breather hole in gilt: a speed of one's own."""
    s, line = size, line_weight(size)
    nib = closed([(s * 0.36, s * 0.10), (s * 0.64, s * 0.10), (s * 0.77, s * 0.45), (s * 0.50, s * 0.90),
                  (s * 0.23, s * 0.45)])
    return [stroke(nib, line), stroke([(s * 0.50, s * 0.90), (s * 0.50, s * 0.60)], line * 0.8),
            gilt(circle(s * 0.50, s * 0.48, max(s * 0.08, 0.95)))]


def fleuron(width: int, height: int) -> list[Shape]:
    """·◆·: a small lozenge between two dots, the ornament set into a rule."""
    c, m = width / 2, height / 2
    half_w, half_h = height * 0.30, height * 0.48
    dot, gap = max(height * 0.13, 0.7), height * 0.95
    return [polygon((c, m - half_h), (c + half_w, m), (c, m + half_h), (c - half_w, m)),
            circle(c - gap, m, dot), circle(c + gap, m, dot)]


def fleuron_width(height: int) -> int:
    """How wide ``fleuron`` needs its picture to be at this height."""
    return int(math.ceil(height * 2.3)) | 1


def warning(size: int) -> list[Shape]:
    """⚠ in line: a triangle with round corners, and the ! inside it."""
    s, line = size, line_weight(size, 0.085, 1.1)
    corners = [(s * 0.5, s * 0.10), (s * 0.94, s * 0.86), (s * 0.06, s * 0.86)]
    return [stroke(closed(corners), line), stroke([(s * 0.5, s * 0.40), (s * 0.5, s * 0.59)], line * 1.05),
            circle(s * 0.5, s * 0.715, line * 0.62)]


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


def lyre(size: int) -> list[Shape]:
    """The lyre of LyreBot's crest in line: two arms that thin as they rise and curl at their horns, the
    yoke, the strings and the sound box."""
    k = size / 1024
    points, widths = lyre_arm()
    widths = [max(w * k, 1.0) for w in widths]
    (yoke_a, yoke_b, yoke_w), (box_a, box_b, box_w) = LYRE_YOKE, LYRE_BOX
    shapes = [stroke([(x * k, y * k) for x, y in points], widths),
              stroke([((1024 - x) * k, y * k) for x, y in points], widths),
              stroke([(yoke_a[0] * k, yoke_a[1] * k), (yoke_b[0] * k, yoke_b[1] * k)], max(yoke_w * k, 0.9)),
              stroke([(box_a[0] * k, box_a[1] * k), (box_b[0] * k, box_b[1] * k)], max(box_w * k, 1.1))]
    across, top, bottom, thread = LYRE_STRINGS
    shapes += [stroke([(x * k, top * k), (x * k, bottom * k)], max(thread * k, 0.55)) for x in across]
    return shapes


def pill(width: int, height: int, fill: str, border: str, line: float = 1.0) -> Layers:
    """A rounded button face: its border, then its fill inside."""
    radius = height / 2 - 0.5
    face = [(border, rect(0.5, 0.5, width - 0.5, height - 0.5, radius))]
    if fill != border:
        face.append((fill, rect(0.5 + line, 0.5 + line, width - 0.5 - line, height - 0.5 - line, radius - line)))
    return face
