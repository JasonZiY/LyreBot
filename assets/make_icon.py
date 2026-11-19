# Draws assets/lyrebot.png and lyrebot.ico: an ivory lyre on Claude's orange, inside a fine gold
# edge, a small gold diamond above it. Needs Pillow; run from the project folder.
from PIL import Image, ImageDraw

SS = 4  # drawn at four times the size, then scaled down: smooth edges
S = 1024 * SS
ORANGE = (217, 119, 87, 255)
GOLD = (241, 214, 160, 255)
IVORY = (255, 247, 236, 255)
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
k = lambda *v: [x * SS for x in v]  # noqa: E731

d.rounded_rectangle(k(40, 40, 984, 984), radius=230 * SS, fill=ORANGE)
d.rounded_rectangle(k(96, 96, 928, 928), radius=178 * SS, outline=GOLD, width=12 * SS)


def bez(p, n=240):
    out = []
    for i in range(n + 1):
        t, pts = i / n, p
        while len(pts) > 1:
            pts = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) for a, b in zip(pts, pts[1:])]
        out.append(pts[0])
    return out


def stroke(pts, w, fill=IVORY):
    r = w / 2
    for x, y in pts:
        d.ellipse(k(x - r, y - r, x + r, y + r), fill=fill)


def line(a, b, w, fill=IVORY):
    stroke([(a[0] + (b[0] - a[0]) * i / 200, a[1] + (b[1] - a[1]) * i / 200) for i in range(201)], w, fill)


left = bez([(420, 790), (205, 752), (180, 455), (330, 345), (400, 295), (342, 192), (258, 222)])
stroke(left, 58)
stroke([(1024 - x, y) for x, y in left], 58)
for x in (258, 766):  # horn knobs
    d.ellipse(k(x - 40, 222 - 40, x + 40, 222 + 40), fill=IVORY)
line((304, 338), (720, 338), 44)  # crossbar
line((368, 798), (656, 798), 60)  # base
for x in (436, 488, 536, 588):  # strings
    line((x, 352), (x, 780), 16)
c, r = 512, 34  # the small gold diamond above
d.polygon(k(c, 160 - r, c + r * 0.75, 160, c, 160 + r, c - r * 0.75, 160), fill=GOLD)

im = im.resize((1024, 1024), Image.LANCZOS)
im.save("assets/lyrebot.png")
# One frame per size Windows asks for at 100%-200% scaling, each scaled straight
# from the big image, so Windows never has to stretch a frame (that blurs it).
SIZES = [16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 128, 256]
frames = [im.resize((n, n), Image.LANCZOS) for n in SIZES]
frames[-1].save("assets/lyrebot.ico", sizes=[(n, n) for n in SIZES], append_images=frames[:-1])
