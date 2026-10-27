"""Small anti-aliased button icons, drawn in pure Python for any screen scale.

Tk's canvas does not smooth edges on Windows and font glyphs like ▶ sit off
centre, so the icons are rasterised here instead: every pixel is sampled on
a grid and blended with the background colour.
"""

from __future__ import annotations

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
