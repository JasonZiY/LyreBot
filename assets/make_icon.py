# Draws assets/lyrebot.png and lyrebot.ico: LyreBot as a book. A cover of dark leather, its spine on the
# left with a gilt line beside it, a double gilt frame with a lozenge in each corner, the lyre of the
# window's crest stamped in gold, and an orange ribbon hanging from the top. Open it and the window is
# the book's paper.
#
# Every size is drawn for its own pixels rather than shrunk from a big picture: straight edges and lines
# fall on whole pixels, so they stay sharp; only the curves are smoothed. Small sizes get plainer covers
# (one fillet up to 64 px, none below 30 px, a bolder lyre with fewer strings) so that nothing turns to
# mush. Needs Pillow; run from the project folder.
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyrebot import icons  # noqa: E402  the lyre's lines, shared with the crest in the window

SS = 4  # each size is drawn at four times its pixels, then each block of 4 x 4 averaged into one
LEATHER = ((74, 59, 49), (46, 36, 30))  # top, bottom
SPINE = ((52, 41, 34), (34, 26, 22))
RIM = (112, 90, 73)  # the cover's worn edge, lighter: the book's outline on a dark taskbar
GOLD = ((236, 211, 152), (181, 143, 80))
SHADOW = (24, 18, 14)  # where the gold is pressed into the leather
ORANGE, ORANGE_FOLD = (217, 119, 87), (176, 86, 58)
COVER = (102, 62, 922, 962)  # on the design's square of 1024
LOOKS = {  # how much each cover carries
    "large": dict(spine=236, rim=9, lyre=((596, 520), 0.60, 1.0), ribbon=(746, 812, 282, 34)),
    "medium": dict(spine=228, rim=20, lyre=((597, 524), 0.62, 1.35), ribbon=(740, 818, 278, 40)),
    "small": dict(spine=214, rim=36, lyre=((562, 560), 0.76, 1.6), ribbon=(744, 840, 252, 46)),
}


class Sheet:
    """One size of the icon: the design's square of 1024 laid over ``n`` pixels, drawn ``SS`` times finer."""

    def __init__(self, n: int):
        self.n, self.size, self.unit = n, n * SS, n / 1024

    def at(self, v: float) -> float:
        """Where a design point falls, as it is: for curves."""
        return v * self.unit * SS

    def edge(self, v: float) -> int:
        """Where a design edge falls, moved onto the nearest whole pixel."""
        return round(v * self.unit) * SS

    def thick(self, w: float) -> int:
        """A design line's width in whole pixels, at least one."""
        return max(1, round(w * self.unit)) * SS

    def band(self, middle: float, w: float) -> tuple[int, int]:
        """The whole pixels a line ``w`` wide covers, running through ``middle``."""
        width = max(1, round(w * self.unit))
        start = round(middle * self.unit - width / 2)
        return start * SS, (start + width) * SS

    def mask(self) -> Image.Image:
        return Image.new("L", (self.size, self.size), 0)

    def falling(self, top, bottom) -> Image.Image:
        """A picture the size of the sheet, ``top`` at the top shading to ``bottom``."""
        strip = Image.new("RGB", (1, 256))
        for y in range(256):
            strip.putpixel((0, y), tuple(round(a + (b - a) * y / 255) for a, b in zip(top, bottom)))
        return strip.resize((self.size, self.size), Image.BILINEAR)


def box(draw, x0, y0, x1, y1, **options):
    """A rectangle covering [x0, x1) x [y0, y1): Pillow's corners are inclusive."""
    if "radius" in options:
        draw.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), **options)
    else:
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), **options)


def strings_for(sheet: Sheet, scale: float) -> list[float]:
    """Where the lyre's strings go at this size: as many (4, 3 or 2) as stay two pixels apart."""
    across = icons.LYRE_STRINGS[0]
    first, last = across[0], across[-1]
    for count in (4, 3, 2):
        if (last - first) / (count - 1) * scale * sheet.unit >= 2:
            return [first + (last - first) * i / (count - 1) for i in range(count)]
    return [(first + last) / 2]


def stamp_lyre(draw, sheet: Sheet, centre, scale, bold):
    """The crest's lyre into ``draw``, its middle at ``centre`` on the design's square."""
    cx, cy = centre

    def at(x, y):
        return cx + (x - 512) * scale, cy + (y - 500) * scale

    def dot(x, y, width):
        r = width * scale / 2
        draw.ellipse((sheet.at(x - r), sheet.at(y - r), sheet.at(x + r), sheet.at(y + r)), fill=255)

    points, widths = icons.lyre_arm(steps=360)
    for (x, y), width in zip(points, widths):
        dot(*at(x, y), width * bold)
        dot(*at(1024 - x, y), width * bold)
    for a, b, thickness in (icons.LYRE_YOKE, icons.LYRE_BOX):  # level bars, on whole pixels
        top, bottom = sheet.band(at(*a)[1], thickness * scale * bold)
        box(draw, sheet.edge(at(*a)[0]), top, sheet.edge(at(*b)[0]), bottom, radius=(bottom - top) / 2, fill=255)
    _, high, low, thread = icons.LYRE_STRINGS
    for x in strings_for(sheet, scale):  # upright, on whole pixels
        left, right = sheet.band(at(x, 0)[0], thread * scale * bold)
        box(draw, left, sheet.edge(at(0, high)[1]), right, sheet.edge(at(0, low)[1]), fill=255)


def lozenge(draw, sheet: Sheet, x, y, r):
    draw.polygon([sheet.at(v) for v in (x, y - r, x + r * 0.75, y, x, y + r, x - r * 0.75, y)], fill=255)


def cover(detail: str, n: int) -> Image.Image:
    """The icon ``n`` pixels square, with as much on it as ``detail`` ("large", "medium" or "small") allows."""
    look, sheet = LOOKS[detail], Sheet(n)
    left, top, right, bottom = (sheet.edge(v) for v in COVER)
    out = Image.new("RGBA", (sheet.size, sheet.size), (0, 0, 0, 0))
    body = sheet.mask()
    box(ImageDraw.Draw(body), left, top, right, bottom, radius=sheet.at(96), fill=255)
    out.paste(RIM, (0, 0), body)
    rim = sheet.thick(look["rim"])  # about a pixel wide at the sizes the cover is used for
    inside = sheet.mask()
    box(ImageDraw.Draw(inside), left + rim, top + rim, right - rim, bottom - rim,
        radius=max(sheet.at(96) - rim, 0), fill=255)
    out.paste(sheet.falling(*LEATHER), (0, 0), inside)
    spine = sheet.mask()
    box(ImageDraw.Draw(spine), 0, 0, sheet.edge(look["spine"]), sheet.size, fill=255)
    spine = ImageChops.multiply(spine, inside)
    out.paste(sheet.falling(*SPINE), (0, 0), spine)

    gold = sheet.mask()
    g = ImageDraw.Draw(gold)
    hinge = look["spine"] + 22
    if detail == "large":
        x0, x1 = sheet.band(hinge, 10)
        box(g, x0, sheet.edge(92), x1, sheet.edge(932), fill=255)
        box(g, sheet.edge(312), sheet.edge(130), sheet.edge(880), sheet.edge(894), radius=sheet.at(24), outline=255,
            width=sheet.thick(14))
        box(g, sheet.edge(340), sheet.edge(158), sheet.edge(852), sheet.edge(866), radius=sheet.at(12), outline=255,
            width=sheet.thick(6))
        for x, y in ((340, 158), (852, 158), (340, 866), (852, 866)):
            lozenge(g, sheet, x, y, 26)
    elif detail == "medium":
        x0, x1 = sheet.band(hinge + 4, 20)
        box(g, x0, sheet.edge(96), x1, sheet.edge(928), fill=255)
        box(g, sheet.edge(318), sheet.edge(136), sheet.edge(876), sheet.edge(888), radius=sheet.at(28), outline=255,
            width=sheet.thick(26))
    centre, scale, bold = look["lyre"]
    stamp_lyre(g, sheet, centre, scale, bold)
    if detail == "large":  # the stamp's shadow, a little below it: gold pressed into the leather
        mask = sheet.mask()
        mask.paste(gold, (0, sheet.thick(4)))
        mask = ImageChops.multiply(Image.eval(mask, lambda v: v * 150 // 255), inside)
        out.paste(Image.new("RGB", out.size, SHADOW), (0, 0), mask)
    out.paste(sheet.falling(*GOLD), (0, 0), gold)

    ribbon_left, ribbon_right, low, notch = look["ribbon"]
    x0, x1, y0, y1 = sheet.edge(ribbon_left), sheet.edge(ribbon_right), sheet.edge(40), sheet.edge(low)
    ribbon = sheet.mask()
    ImageDraw.Draw(ribbon).polygon([(x0, y0), (x1, y0), (x1, y1), ((x0 + x1) / 2, y1 - sheet.at(notch)), (x0, y1)],
                                   fill=255)
    out.paste(Image.new("RGB", out.size, ORANGE), (0, 0), ribbon)
    fold = sheet.mask()  # where it comes out from under the cover
    box(ImageDraw.Draw(fold), x0, y0, x1, max(top + sheet.thick(6), y0 + SS), fill=255)
    out.paste(Image.new("RGB", out.size, ORANGE_FOLD), (0, 0), ImageChops.multiply(fold, ribbon))
    return out.resize((n, n), Image.BOX)


def detail_for(n: int) -> str:
    return "small" if n < 30 else "medium" if n <= 64 else "large"


if __name__ == "__main__":
    cover("large", 1024).save("assets/lyrebot.png")
    # One frame for each size Windows asks for at 100%-200% scaling, each drawn for its own pixels, so
    # Windows never has to stretch one (that blurs it).
    SIZES = [16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 128, 256]
    frames = [cover(detail_for(n), n) for n in SIZES]
    frames[-1].save("assets/lyrebot.ico", sizes=[(n, n) for n in SIZES], append_images=frames[:-1])
