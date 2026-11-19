"""Small anti-aliased button icons, drawn in pure Python for any screen scale.

Tk's canvas does not smooth edges on Windows and font glyphs like ▶ sit off
centre, so the icons are rasterised here instead: every pixel is sampled on
a grid and blended with the background colour.
"""

from __future__ import annotations

import math
from typing import Callable

Shape = Callable[[float, float], bool]
SAMPLES = 4  # per axis, per pixel


def circle(cx: float, cy: float, r: float) -> Shape:
    return lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def rect(x0: float, y0: float, x1: float, y1: float, radius: float = 0.0) -> Shape:
    def inside(x: float, y: float) -> bool:
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        dx = max(x0 + radius - x, 0.0, x - (x1 - radius))
        dy = max(y0 + radius - y, 0.0, y - (y1 - radius))
        return dx * dx + dy * dy <= radius * radius
    return inside


def polygon(*points: tuple[float, float]) -> Shape:
    """A convex polygon with its points listed clockwise (y grows downwards)."""
    edges = list(zip(points, points[1:] + points[:1]))

    def inside(x: float, y: float) -> bool:
        return all((bx - ax) * (y - ay) - (by - ay) * (x - ax) >= 0 for (ax, ay), (bx, by) in edges)
    return inside


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def render(size: int, background: str, layers: list[tuple[str, Shape]]) -> list[str]:
    """Return Tk ``PhotoImage.put`` rows; later layers paint over earlier ones."""
    base = _rgb(background)
    colors = [(_rgb(color), shape) for color, shape in layers]
    step = 1.0 / SAMPLES
    offsets = [(i + 0.5) * step for i in range(SAMPLES)]
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            total = [0, 0, 0]
            for oy in offsets:
                for ox in offsets:
                    x, y = px + ox, py + oy
                    pick = base
                    for color, shape in colors:
                        if shape(x, y):
                            pick = color
                    total[0] += pick[0]
                    total[1] += pick[1]
                    total[2] += pick[2]
            n = SAMPLES * SAMPLES
            row.append("#%02x%02x%02x" % (round(total[0] / n), round(total[1] / n), round(total[2] / n)))
        rows.append("{" + " ".join(row) + "}")
    return rows


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
    w, h, bar, gap = size * 0.26, size * 0.32, size * 0.07, size * 0.02
    left = c - (w + gap + bar) / 2
    return [polygon((left, c - h / 2), (left + w, c), (left, c + h / 2)),
            rect(left + w + gap, c - h / 2, left + w + gap + bar, c + h / 2, radius=bar * 0.3)]


def triangle(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> Shape:
    """A triangle with its corners in any order."""
    turn = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return polygon(a, b, c) if turn >= 0 else polygon(a, c, b)


def restart(size: int) -> list[Shape]:
    """↺: a ring open near the top right, its arrowhead at about two o'clock pointing up, anticlockwise."""
    c = size / 2
    radius, thick = size * 0.21, size * 0.075
    gap = (math.radians(-85), math.radians(-25))  # screen angles, clockwise from three o'clock

    def ring(x: float, y: float) -> bool:
        if not (radius - thick / 2) ** 2 <= (x - c) ** 2 + (y - c) ** 2 <= (radius + thick / 2) ** 2:
            return False
        return not gap[0] < math.atan2(y - c, x - c) < gap[1]

    at = gap[1]
    point = (c + radius * math.cos(at), c + radius * math.sin(at))
    ahead = (math.sin(at), -math.cos(at))  # the way an anticlockwise turn goes from there
    out = (math.cos(at), math.sin(at))
    half, length = thick * 1.45, size * 0.15
    head = triangle((point[0] + out[0] * half, point[1] + out[1] * half),
                    (point[0] - out[0] * half, point[1] - out[1] * half),
                    (point[0] + ahead[0] * length, point[1] + ahead[1] * length))
    return [ring, head]


def keyboard(size: int) -> list[Shape]:
    """A small keyboard: the body first, then the keys, to be painted in the background colour."""
    c = size / 2
    w, h = size * 0.50, size * 0.32
    x0, y0 = c - w / 2, c - h / 2
    body = rect(x0, y0, x0 + w, y0 + h, radius=size * 0.04)
    key, gap = size * 0.065, size * 0.035
    columns = 4
    left = c - (columns * key + (columns - 1) * gap) / 2
    keys = [rect(left + column * (key + gap), y0 + gap * 1.6 + row * (key + gap),
                 left + column * (key + gap) + key, y0 + gap * 1.6 + row * (key + gap) + key, radius=key * 0.2)
            for row in range(2) for column in range(columns)]
    bar = rect(c - size * 0.12, y0 + h - gap * 1.6 - key * 0.8, c + size * 0.12, y0 + h - gap * 1.6, radius=key * 0.2)
    return [body, *keys, bar]
