# Draws the small pictures README.md shows in its text, into assets/readme: LyreBot's buttons as the window
# draws them, by the window's own code (App.build_images, the title bar's faces, the list's and search
# field's marks, the ribbons), each laid on a tile of the page's paper (the title bar's book on its leather)
# so that it shows on a dark page as well as a light one. Each tile is 44 pixels square and is shown 22
# high, two pixels to one, so that it stays sharp on fine screens. Run it from the project folder whenever
# a button's face changes: python assets/make_readme_icons.py
import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lyrebot import chrome, icons  # noqa: E402
from lyrebot.app import App  # noqa: E402
from lyrebot.theme import (GOLD, GREEN, GREEN_FOLD, GREEN_TUCK, HAIR, LEATHER_EDGE, LEATHER_TOP, MUTED,  # noqa: E402
                           PAPER, RIBBON, RIBBON_FOLD, RIBBON_SHADE, RIBBON_SHADE_FAR, RIBBON_TUCK, WARN, Theme)
from lyrebot.widgets import TitleBar  # noqa: E402

OUT = ROOT / "assets" / "readme"
TILE = 44
# The buttons are drawn as the window draws them at some scale: the round ones at 118 %, so that ▶ (34
# pixels at 100 %) fills the tile; each drawn in fine lines at whatever scale makes what is drawn LINED
# pixels at its longest, so that they all look the same size and read when the tile is shown 22 high.
ROUND, LINED = 1.18, 27
FACES = {  # the picture's name here: what the window calls it
    "play": "play-normal", "stop": "play-stop", "halt": "halt-normal", "record": "record-normal",
    "recording": "recording-in", "restart": "restart-normal", "next": "next-normal", "keys": "keys-normal",
    "grow": "grow-normal", "shrink": "shrink-normal", "undo": "undo-normal", "save": "save-normal",
    "guide": "guide-normal", "warning": f"warning:{WARN}:{WARN}", "search": f"magnifier:{MUTED}:{GOLD}",
    "book": "title-start",
}
ROUNDS = {"play", "stop", "halt"}  # as they are; the rest made so many pixels long:
FIT = {"record": 33, "recording": 33}  # the small round ones a little fuller than the lines


def drawings(root: tk.Tk, scale: float) -> dict[str, tuple[int, int, object]]:
    """Everything the window draws for its buttons, as it would at ``scale``: (width, height, layers) by name."""
    root.tk.call("tk", "scaling", scale * 96 / 72)  # what Theme reads as the screen's scale
    theme = Theme(root)
    drawn: dict[str, tuple[int, int, object]] = {}

    def keep(key: str, width: int, height: int, layers) -> tk.PhotoImage:
        drawn[key] = (width, height, layers)
        return tk.PhotoImage(master=root, width=width, height=height)
    theme.image = keep  # type: ignore[method-assign]  # what the window would draw, kept here instead
    App.build_images(SimpleNamespace(theme=theme, scale=theme.scale))  # type: ignore[arg-type]
    TitleBar(root, theme, chrome.Frame(edge=0, caption=0), "LyreBot", "", lambda: None, lambda: None,
             start=lambda: None).destroy()
    theme.icon("warning", theme.px(22), WARN, icons.warning)  # the list's mark on a score that cannot be read
    theme.icon("magnifier", theme.px(20), MUTED, icons.magnifier, GOLD)  # the search field's
    return drawn


def tile(ground: str, edge: str) -> icons.Layers:
    radius = TILE * 0.22
    return [(edge, icons.rect(0, 0, TILE, TILE, radius)), (ground, icons.rect(2, 2, TILE - 2, TILE - 2, radius - 2))]


def on_paper(layers: icons.Layers) -> icons.Layers:
    """Tints (#RRGGBBAA) as they look in the window, over the page: mixed into its paper here, where no page
    lies under the picture (icons.rgba leaves a tint see-through, for the page to show)."""
    def solid(color: str) -> str:
        if len(color) != 9:
            return color
        alpha = int(color[7:9], 16) / 255
        mixed = (round(int(color[i:i + 2], 16) * alpha + int(PAPER[i:i + 2], 16) * (1 - alpha)) for i in (1, 3, 5))
        return "#" + "".join(f"{value:02X}" for value in mixed)
    return [(solid(color), shape) for color, shape in layers]


def extent(width: int, height: int, layers: icons.Layers) -> int:
    """How many pixels what ``layers`` draw spans, across or down, whichever is more."""
    pixels = icons.rgba(width, height, layers)
    inked = [i for i in range(width * height) if pixels[4 * i + 3]]
    across, down = [i % width for i in inked], [i // width for i in inked]
    return max(max(across) - min(across), max(down) - min(down)) + 1


def centred(layers: icons.Layers, width: float, height: float) -> icons.Layers:
    return on_paper(icons.placed(layers, dx=-(TILE - width) / 2, dy=-(TILE - height) / 2))


def write(name: str, layers: icons.Layers) -> None:
    (OUT / f"{name}.png").write_bytes(icons.png(TILE, TILE, icons.rgba(TILE, TILE, layers)))


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    round_ones, at_two = drawings(root, ROUND), drawings(root, 2.0)
    chosen = {}
    for name, key in FACES.items():
        if name in ROUNDS:
            chosen[name] = round_ones[key]
        else:  # measured at 200 %, then drawn again at the scale that makes it as long as it should be
            width, height, layers = at_two[key]
            length = FIT.get(name, LINED)
            chosen[name] = drawings(root, 2.0 * length / extent(width, height, layers()))[key]  # type: ignore
    root.destroy()

    OUT.mkdir(parents=True, exist_ok=True)
    for name in FACES:
        width, height, layers = chosen[name]
        ground = tile(LEATHER_TOP, LEATHER_EDGE) if name == "book" else tile(PAPER, HAIR)  # the book: title bar
        write(name, ground + centred(layers(), width, height))  # type: ignore[operator]
    band, length, notch, drop = 14, 31, 7, 2  # a bookmark hanging from the top of the tile, its end in view
    for name, (body, fold, tuck) in (("ribbon", (RIBBON, RIBBON_FOLD, RIBBON_TUCK)),
                                     ("green", (GREEN, GREEN_FOLD, GREEN_TUCK))):
        hanging = icons.ribbon(band, length, notch, drop, body, fold, tuck, RIBBON_SHADE, RIBBON_SHADE_FAR)
        write(name, tile(PAPER, HAIR) + on_paper(icons.placed(hanging, dx=-(TILE - band - drop) / 2)))
    print(f"{len(FACES) + 2} pictures in {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
