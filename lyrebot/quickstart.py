"""快速开始: how LyreBot is used, in six steps on a card opened from the title bar's open book.  Each step is a
gilt number, a few plain words and a small picture made of the window's own pieces (the ▶ button, the
ribbons, keys to press, a little 简谱), so that what is read is what is then looked for in the window."""

from __future__ import annotations

import tkinter as tk

from . import icons
from .theme import GOLD, GOLD_SOFT, HAIR, INK, MUTED, ORANGE, PAPER, Theme

TITLE = "快速开始"
STEPS = (
    ("选一首曲子", "在列表里点一下曲名。"),
    ("开始弹", "进游戏、打开琴，按 F7 就开始弹。"),
    ("停下", "随时按 F8。"),
    ("先听一遍", "点右上角的橙色书签，翻到简谱，再点 ▶ 或任意一个音。"),
    ("改曲谱", "在简谱页点绿色书签，直接改原文。"),
    ("加新曲子", "MIDI、录音、你在游戏里弹的，都能变成曲谱。"),
)
TIPS = ("游戏里没反应：以管理员身份运行 LyreBot。\n"
        "不会写曲谱：原文页右下角的 ? 里有写法。右键曲子可以移动、导出简谱、删除。")
CHOSEN = "#F6E3D8"  # the chosen song's breath of orange, on the card's plain paper


def fill(frame: tk.Frame, theme: Theme, pictures: dict[str, tk.PhotoImage]) -> None:
    """Write the steps into ``frame``.  ``pictures`` are the window's own: "play" (▶), "ribbon" and "green"
    (the two bookmarks), "record" (●) and "keys" (the keyboard button)."""
    px, fonts = theme.px, theme.fonts
    kept: list[tk.PhotoImage] = []  # the pictures drawn here, kept while the card lives

    def words(canvas: tk.Canvas, x: float, y: float, text: str, colour: str = MUTED, font=None) -> float:
        item = canvas.create_text(x, y, text=text, font=font or fonts.small, fill=colour, anchor="w")
        return canvas.bbox(item)[2]

    def keycap(canvas: tk.Canvas, x: float, y: float, label: str) -> float:
        font = (fonts.figures[0], 10, "bold")
        width, height = max(px(30), theme.measure(font, label) + px(14)), px(24)
        face = theme.image(f"keycap:{width}x{height}", width, height,
                           lambda: [(HAIR, icons.rect(0, 0, width, height, px(5))),
                                    ("#FFFFFF", icons.rect(1, 1, width - 1, height - 2, px(4)))])
        kept.append(face)
        canvas.create_image(x, y, image=face, anchor="w")
        canvas.create_text(x + width / 2, y, text=label, font=font, fill=INK)
        return x + width

    def picture(step: int, canvas: tk.Canvas) -> None:
        y, x = int(canvas.cget("height")) / 2, px(4)
        if step == 0:  # the list, one song chosen
            for row, (name, chosen) in enumerate((("晴天-演奏", False), ("春日影-演奏", True), ("空之箱", False))):
                top = px(2) + row * px(16)
                if chosen:
                    canvas.create_rectangle(x, top, x + px(150), top + px(15), fill=CHOSEN, outline="")
                    canvas.create_rectangle(x, top, x + px(2), top + px(15), fill=ORANGE, outline="")
                canvas.create_text(x + px(8), top + px(8), text=name, font=fonts.small, fill=INK if chosen else MUTED,
                                   anchor="w")
        elif step == 1:  # F7, or ▶ and two seconds to get into the game
            x = keycap(canvas, x, y, "F7") + px(10)
            x = words(canvas, x, y, "或") + px(10)
            canvas.create_image(x, y, image=pictures["play"], anchor="w")
            words(canvas, x + pictures["play"].width() + px(8), y, "2 秒内切进游戏")
        elif step == 2:  # F8
            words(canvas, keycap(canvas, x, y, "F8") + px(10), y, "或切到别的窗口")
        elif step in (3, 4):  # a ribbon, and the page behind it
            ribbon = pictures["ribbon" if step == 3 else "green"]
            canvas.create_image(x, px(2), image=ribbon, anchor="nw")
            x = words(canvas, x + ribbon.width() + px(10), y, "→", GOLD, fonts.body) + px(10)
            if step == 3:  # a little 简谱, the note heard in orange
                for digit in "12356":
                    item = canvas.create_text(x, y, text=digit, font=(fonts.serif or "Cambria", 15),
                                              fill=ORANGE if digit == "3" else INK, anchor="w")
                    x = canvas.bbox(item)[2] + px(8)
            else:  # a slip with the score written on it
                canvas.create_rectangle(x, y - px(15), x + px(110), y + px(15), fill=PAPER, outline=GOLD_SOFT)
                canvas.create_text(x + px(8), y, text="speed=96\na-s-d-f-", font=(fonts.mono[0], 8), fill=INK,
                                   anchor="w")
        else:  # MIDI, a recording, the keys played
            folder = theme.icon("folder", px(22), INK, icons.folder, GOLD)
            kept.append(folder)
            for image, label in ((folder, "midi"), (pictures["record"], "录音"), (pictures["keys"], "演奏")):
                canvas.create_image(x, y, image=image, anchor="w")
                x = words(canvas, x + image.width() + px(4), y, label) + px(14)

    ring = theme.image("step-ring", px(26), px(26),
                       lambda: [(GOLD, icons.ring(px(26) / 2, px(26) / 2, px(26) / 2 - 1.5, max(1, round(theme.scale))))])
    kept.append(ring)
    for number, (title, text) in enumerate(STEPS):
        row = tk.Frame(frame, bg=PAPER)
        row.pack(fill="x", pady=(0 if number == 0 else px(10), 0))
        badge = tk.Canvas(row, width=px(26), height=px(26), bg=PAPER, highlightthickness=0)
        badge.pack(side="left", anchor="n")
        badge.create_image(px(26) / 2, px(26) / 2, image=ring)
        badge.create_text(px(26) / 2, px(26) / 2, text=str(number + 1), font=(fonts.figures[0], 11), fill=GOLD)
        said = tk.Frame(row, bg=PAPER)
        said.pack(side="left", anchor="n", padx=(px(10), px(14)))
        tk.Label(said, text=title, font=fonts.shelf, fg=INK, bg=PAPER, anchor="w").pack(fill="x")
        tk.Label(said, text=text, font=fonts.body, fg=MUTED, bg=PAPER, anchor="w", justify="left",
                 wraplength=px(220)).pack(fill="x")
        canvas = tk.Canvas(row, width=px(230), height=px(50), bg=PAPER, highlightthickness=0)
        canvas.pack(side="right", anchor="n")
        picture(number, canvas)
    tk.Frame(frame, bg=GOLD_SOFT, height=max(1, px(1))).pack(fill="x", pady=(px(14), px(8)))
    tk.Label(frame, text=TIPS, font=fonts.small, fg=MUTED, bg=PAPER, anchor="w", justify="left",
             wraplength=px(480)).pack(fill="x")
    frame.kept = kept  # type: ignore[attr-defined]
