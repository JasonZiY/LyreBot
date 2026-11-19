# Draws assets/lyrebot.png and lyrebot.ico: LyreBot as a book. A cover of dark leather, its spine on the
# left with a gilt line beside it, a double gilt frame with a lozenge in each corner, the lyre of the
# window's crest stamped in gold, and an orange ribbon hanging from the top. Open it and the window is
# the book's paper. Small sizes are drawn from plainer covers (one fillet up to 64 px, none below 30 px,
# the lyre bolder) so that nothing turns to mush. Needs Pillow; run from the project folder.
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyrebot import icons  # noqa: E402  the lyre's lines, shared with the crest in the window

SS = 4  # drawn at four times the size, then scaled down: smooth edges
S = 1024 * SS
LEATHER = ((74, 59, 49), (46, 36, 30))  # top, bottom
SPINE = ((52, 41, 34), (34, 26, 22))
RIM = (112, 90, 73)  # the cover's worn edge, lighter: the book's outline on a dark taskbar
GOLD = ((236, 211, 152), (181, 143, 80))
SHADOW = (24, 18, 14)  # where the gold is pressed into the leather
ORANGE, ORANGE_FOLD = (217, 119, 87), (176, 86, 58)
COVER = (102, 62, 922, 962)


def k(*values):
    return [round(v * SS) for v in values]


def falling(top, bottom):
    """A picture the size of the canvas, ``top`` at the top shading to ``bottom``."""
    strip = Image.new("RGB", (1, 256))
    for y in range(256):
        strip.putpixel((0, y), tuple(round(a + (b - a) * y / 255) for a, b in zip(top, bottom)))
    return strip.resize((S, S), Image.BILINEAR)


def stamp_lyre(draw, centre, scale, bold=1.0, strings=4):
    """The crest's lyre into ``draw``, its middle at ``centre``."""
    cx, cy = centre

    def at(x, y):
        return cx + (x - 512) * scale, cy + (y - 500) * scale

    def dot(x, y, width):
        r = width * scale / 2
        draw.ellipse(k(x - r, y - r, x + r, y + r), fill=255)

    def line(a, b, width):
        steps = max(2, int(abs(a[0] - b[0]) + abs(a[1] - b[1])) // 2)
        for i in range(steps + 1):
            dot(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps, width)

    points, widths = icons.lyre_arm(steps=360)
    for (x, y), width in zip(points, widths):
        dot(*at(x, y), width * bold)
        dot(*at(1024 - x, y), width * bold)
    (yoke_a, yoke_b, yoke_w), (box_a, box_b, box_w) = icons.LYRE_YOKE, icons.LYRE_BOX
    line(at(*yoke_a), at(*yoke_b), yoke_w * bold)
    line(at(*box_a), at(*box_b), box_w * bold)
    across, top, bottom, thread = icons.LYRE_STRINGS
    chosen = across if strings == 4 else (across[1], across[2]) if strings == 2 else ()
    for x in chosen:
        line(at(x, top), at(x, bottom), thread * bold)


def lozenge(draw, x, y, r):
    draw.polygon(k(x, y - r, x + r * 0.75, y, x, y + r, x - r * 0.75, y), fill=255)


def cover(detail: str) -> Image.Image:
    """The icon at 1024 px, with as much on it as ``detail`` ("large", "medium" or "small") allows."""
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    body = Image.new("L", (S, S), 0)
    ImageDraw.Draw(body).rounded_rectangle(k(*COVER), radius=96 * SS, fill=255)
    out.paste(RIM, (0, 0), body)
    rim = {"large": 9, "medium": 20, "small": 36}[detail]  # about a pixel wide at the sizes it is used for
    inside = Image.new("L", (S, S), 0)
    ImageDraw.Draw(inside).rounded_rectangle(k(COVER[0] + rim, COVER[1] + rim, COVER[2] - rim, COVER[3] - rim),
                                             radius=(96 - rim) * SS, fill=255)
    out.paste(falling(*LEATHER), (0, 0), inside)
    spine_right = {"large": 236, "medium": 228, "small": 214}[detail]
    spine = Image.new("L", (S, S), 0)
    ImageDraw.Draw(spine).rectangle(k(0, 0, spine_right, 1024), fill=255)
    spine.paste(0, (0, 0), Image.eval(inside, lambda v: 255 - v))
    out.paste(falling(*SPINE), (0, 0), spine)

    gold = Image.new("L", (S, S), 0)
    g = ImageDraw.Draw(gold)
    if detail == "large":
        g.line(k(spine_right + 22, 92, spine_right + 22, 932), fill=255, width=10 * SS)
        g.rounded_rectangle(k(312, 130, 880, 894), radius=24 * SS, outline=255, width=14 * SS)
        g.rounded_rectangle(k(340, 158, 852, 866), radius=12 * SS, outline=255, width=6 * SS)
        for x, y in ((340, 158), (852, 158), (340, 866), (852, 866)):
            lozenge(g, x, y, 26)
        stamp_lyre(g, (596, 520), 0.60)
    elif detail == "medium":
        g.line(k(spine_right + 26, 96, spine_right + 26, 928), fill=255, width=20 * SS)
        g.rounded_rectangle(k(318, 136, 876, 888), radius=28 * SS, outline=255, width=26 * SS)
        stamp_lyre(g, (597, 524), 0.62, bold=1.35, strings=2)
    else:
        stamp_lyre(g, (562, 560), 0.76, bold=1.6, strings=2)
    if detail == "large":  # the stamp's shadow, a little below it: gold pressed into the leather
        mask = Image.new("L", (S, S), 0)
        mask.paste(gold, (0, 4 * SS))
        mask = ImageChops.multiply(Image.eval(mask, lambda v: v * 150 // 255), inside)
        out.paste(Image.new("RGB", (S, S), SHADOW), (0, 0), mask)
    out.paste(falling(*GOLD), (0, 0), gold)

    ribbon = Image.new("L", (S, S), 0)
    left, right, low, notch = {"large": (746, 812, 282, 34), "medium": (740, 818, 278, 40),
                               "small": (744, 840, 252, 46)}[detail]
    ImageDraw.Draw(ribbon).polygon(k(left, 40, right, 40, right, low, (left + right) / 2, low - notch, left, low),
                                   fill=255)
    out.paste(Image.new("RGB", (S, S), ORANGE), (0, 0), ribbon)
    fold = Image.new("L", (S, S), 0)  # where it comes out from under the cover
    ImageDraw.Draw(fold).rectangle(k(left, 40, right, COVER[1] + 6), fill=255)
    fold.paste(0, (0, 0), Image.eval(ribbon, lambda v: 255 - v))
    out.paste(Image.new("RGB", (S, S), ORANGE_FOLD), (0, 0), fold)
    return out.resize((1024, 1024), Image.LANCZOS)


if __name__ == "__main__":
    covers = {detail: cover(detail) for detail in ("large", "medium", "small")}
    covers["large"].save("assets/lyrebot.png")
    # One frame per size Windows asks for at 100%-200% scaling, each scaled straight from the cover
    # drawn for its size, so Windows never has to stretch a frame (that blurs it).
    SIZES = [16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 128, 256]
    frames = [covers["small" if n < 30 else "medium" if n <= 64 else "large"].resize((n, n), Image.LANCZOS)
              for n in SIZES]
    frames[-1].save("assets/lyrebot.ico", sizes=[(n, n) for n in SIZES], append_images=frames[:-1])
