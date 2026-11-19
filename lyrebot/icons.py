"""Small anti-aliased images for buttons, menus and the song list, drawn in pure Python at any scale.

Tk's canvas does not smooth edges on Windows and font glyphs like ▶ sit off
centre, so the images are rasterised here instead.  Each pixel is sampled on
a 4 × 4 grid; the samples that fall inside a shape set its colour and the
share of them its opacity.  The result is a PNG with an alpha channel, which
Tk 8.6 blends onto whatever lies beneath, so one image serves on paper, on a
highlight and on a hover circle alike.

Shapes are functions of a point.  The icons below are laid out on a square
of side ``size``; ``pill`` and ``rect`` take any width and height.
"""

from __future__ import annotations

import math
import struct
from typing import Callable, Iterable
import zlib

Shape = Callable[[float, float], bool]
Layers = list[tuple[str, Shape]]
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


def stroke(points: Iterable[tuple[float, float]], width: float) -> Shape:
    """A line through ``points`` with round ends and joins."""
    path = list(points)
    half2 = (width / 2) ** 2
    segments = list(zip(path, path[1:])) or [(path[0], path[0])]
    xs, ys = [x for x, _ in path], [y for _, y in path]
    box = (min(xs) - width, min(ys) - width, max(xs) + width, max(ys) + width)

    def inside(x: float, y: float) -> bool:
        if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
            return False
        for (ax, ay), (bx, by) in segments:
            dx, dy = bx - ax, by - ay
            length = dx * dx + dy * dy
            t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length))
            ex, ey = ax + t * dx - x, ay + t * dy - y
            if ex * ex + ey * ey <= half2:
                return True
        return False
    return inside


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

def play(size: int) -> Shape:
    """A right-pointing triangle, placed by eye rather than by its box, so it looks centred."""
    c = size / 2
    w, h = size * 0.30, size * 0.34
    left = c - w * 0.42
    return polygon((left, c - h / 2), (left + w, c), (left, c + h / 2))


def stop(size: int) -> Shape:
    c, half = size / 2, size * 0.13
    return rect(c - half, c - half, c + half, c + half, radius=size * 0.03)


def next_track(size: int) -> list[Shape]:
    c = size / 2
    w, h, bar, gap = size * 0.24, size * 0.30, size * 0.07, size * 0.03
    left = c - (w + gap + bar) / 2
    return [polygon((left, c - h / 2), (left + w, c), (left, c + h / 2)),
            rect(left + w + gap, c - h / 2, left + w + gap + bar, c + h / 2, radius=bar / 2)]


def restart(size: int) -> list[Shape]:
    """↺: a ring open near the top left, its arrowhead at about eleven o'clock pointing left, anticlockwise."""
    c = size / 2
    radius, thick = size * 0.21, size * 0.075
    gap = (math.radians(-175), math.radians(-115))  # screen angles, clockwise from three o'clock

    def band(x: float, y: float) -> bool:
        if not (radius - thick / 2) ** 2 <= (x - c) ** 2 + (y - c) ** 2 <= (radius + thick / 2) ** 2:
            return False
        return not gap[0] < math.atan2(y - c, x - c) < gap[1]

    tail = circle(c + radius * math.cos(gap[0]), c + radius * math.sin(gap[0]), thick / 2)
    at = gap[1]
    point = (c + radius * math.cos(at), c + radius * math.sin(at))
    ahead = (math.sin(at), -math.cos(at))  # the way an anticlockwise turn goes from there
    out = (math.cos(at), math.sin(at))
    half, length = thick * 1.45, size * 0.15
    head = triangle((point[0] + out[0] * half, point[1] + out[1] * half),
                    (point[0] - out[0] * half, point[1] - out[1] * half),
                    (point[0] + ahead[0] * length, point[1] + ahead[1] * length))
    return [band, tail, head]


def keyboard(size: int) -> list[Shape]:
    """A keyboard in outline: the case, a row of keys and the space bar."""
    s = size
    line = max(s * 0.055, 1.1)
    case = outline(s * 0.18, s * 0.31, s * 0.82, s * 0.69, s * 0.08, line)
    keys = [circle(s * x, s * 0.445, max(s * 0.03, 0.75)) for x in (0.33, 0.443, 0.557, 0.67)]
    space = stroke([(s * 0.39, s * 0.57), (s * 0.61, s * 0.57)], line)
    return [case, *keys, space]


def record(size: int) -> tuple[Shape, Shape]:
    """● with a fine ring round it: (ring, dot)."""
    c = size / 2
    return ring(c, c, size * 0.31, max(size * 0.045, 1.0)), circle(c, c, size * 0.16)


def chevron(size: int, down: bool = False) -> Shape:
    c, a = size / 2, size * 0.2
    weight = max(size * 0.12, 1.25)
    if down:
        return stroke([(c - a, c - a * 0.55), (c, c + a * 0.55), (c + a, c - a * 0.55)], weight)
    return stroke([(c - a * 0.55, c - a), (c + a * 0.55, c), (c - a * 0.55, c + a)], weight)


def check(size: int) -> Shape:
    s = size
    return stroke([(s * 0.24, s * 0.53), (s * 0.43, s * 0.71), (s * 0.78, s * 0.31)], max(s * 0.11, 1.3))


def magnifier(size: int) -> list[Shape]:
    s = size
    weight = max(s * 0.09, 1.2)
    return [ring(s * 0.44, s * 0.44, s * 0.24, weight), stroke([(s * 0.62, s * 0.62), (s * 0.80, s * 0.80)], weight * 1.15)]


def folder(size: int) -> Shape:
    s = size
    return stroke([(s * 0.16, s * 0.74), (s * 0.16, s * 0.28), (s * 0.40, s * 0.28), (s * 0.47, s * 0.37),
                   (s * 0.84, s * 0.37), (s * 0.84, s * 0.74), (s * 0.16, s * 0.74)], max(s * 0.08, 1.2))


def move_to(size: int) -> list[Shape]:
    """A folder with an arrow going into it."""
    s = size
    weight = max(s * 0.08, 1.2)
    return [folder(size), stroke([(s * 0.36, s * 0.56), (s * 0.62, s * 0.56)], weight),
            stroke([(s * 0.53, s * 0.48), (s * 0.62, s * 0.56), (s * 0.53, s * 0.64)], weight)]


def sparkle(size: int) -> list[Shape]:
    """A four-pointed star and a small one: making something anew."""
    s = size

    def star(cx: float, cy: float, r: float, w: float) -> Shape:
        return union(polygon((cx, cy - r), (cx + w, cy), (cx, cy + r), (cx - w, cy)),
                     polygon((cx - r, cy), (cx, cy - w), (cx + r, cy), (cx, cy + w)))
    return [star(s * 0.44, s * 0.56, s * 0.34, s * 0.09), star(s * 0.78, s * 0.22, s * 0.14, s * 0.045)]


def page_note(size: int) -> list[Shape]:
    """A page with a note on it: 简谱."""
    s = size
    weight = max(s * 0.07, 1.1)
    return [outline(s * 0.22, s * 0.12, s * 0.78, s * 0.88, s * 0.07, weight),
            circle(s * 0.45, s * 0.66, s * 0.085),
            stroke([(s * 0.525, s * 0.66), (s * 0.525, s * 0.34), (s * 0.63, s * 0.40)], weight * 0.95)]


def trash(size: int) -> list[Shape]:
    s = size
    weight = max(s * 0.075, 1.15)
    return [stroke([(s * 0.20, s * 0.27), (s * 0.80, s * 0.27)], weight),
            stroke([(s * 0.40, s * 0.27), (s * 0.42, s * 0.17), (s * 0.58, s * 0.17), (s * 0.60, s * 0.27)], weight),
            stroke([(s * 0.27, s * 0.34), (s * 0.31, s * 0.84), (s * 0.69, s * 0.84), (s * 0.73, s * 0.34)], weight),
            stroke([(s * 0.44, s * 0.44), (s * 0.445, s * 0.73)], weight * 0.85),
            stroke([(s * 0.56, s * 0.44), (s * 0.555, s * 0.73)], weight * 0.85)]


def pencil(size: int) -> list[Shape]:
    s = size
    return [stroke([(s * 0.33, s * 0.67), (s * 0.72, s * 0.28)], max(s * 0.14, 1.6)),
            triangle((s * 0.20, s * 0.80), (s * 0.25, s * 0.62), (s * 0.38, s * 0.75))]


def diamond(size: int) -> Shape:
    c = size / 2
    r = size / 2 - 0.3
    return polygon((c, c - r), (c + r, c), (c, c + r), (c - r, c))


def warning(size: int) -> tuple[Shape, list[Shape]]:
    """A warning triangle and the ! to paint on it: (triangle, marks)."""
    s = size
    body = polygon((s * 0.5, s * 0.08), (s * 0.96, s * 0.88), (s * 0.04, s * 0.88))
    weight = max(s * 0.12, 1.2)
    return body, [stroke([(s * 0.5, s * 0.38), (s * 0.5, s * 0.60)], weight), circle(s * 0.5, s * 0.74, weight * 0.6)]


def lyre(size: int) -> list[Shape]:
    """The lyre of LyreBot's crest in line: two curving arms, the crossbar, the strings and the base."""
    k = size / 1024
    left = bezier([(420, 800), (200, 760), (170, 450), (330, 330), (400, 280), (340, 170), (250, 200)], 40)
    arm = max(56 * k, 1.1)
    shapes = [stroke([(x * k, y * k) for x, y in left], arm),
              stroke([((1024 - x) * k, y * k) for x, y in left], arm),
              circle(250 * k, 200 * k, max(40 * k, 0.9)), circle(774 * k, 200 * k, max(40 * k, 0.9)),
              stroke([(300 * k, 322 * k), (724 * k, 322 * k)], max(44 * k, 1.0)),
              stroke([(368 * k, 804 * k), (656 * k, 804 * k)], max(58 * k, 1.1))]
    thread = max(18 * k, 0.55)
    shapes += [stroke([(x * k, 340 * k), (x * k, 780 * k)], thread) for x in (434, 486, 538, 590)]
    return shapes


def pill(width: int, height: int, fill: str, border: str, line: float = 1.0) -> Layers:
    """A rounded button face: its border, then its fill inside."""
    radius = height / 2 - 0.5
    face = [(border, rect(0.5, 0.5, width - 0.5, height - 0.5, radius))]
    if fill != border:
        face.append((fill, rect(0.5 + line, 0.5 + line, width - 0.5 - line, height - 0.5 - line, radius - line)))
    return face
