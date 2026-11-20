"""曲谱格式速查: the essentials of docs/曲谱格式.md on a card, opened from the text page while a score is
written.  Two columns of sections, each way of writing as it is typed (in the writing font) beside what it
means; the 21 keys as a small table under the digits they stand for."""

from __future__ import annotations

import tkinter as tk

from .theme import GOLD, INK, MUTED, PAPER, Theme

TITLE = "曲谱格式速查"
KEYS = (("高音", "qwertyu"), ("中音", "asdfghj"), ("低音", "zxcvbnm"))
LEFT = (
    ("小节与声部", (("a-s-|z-", "一行一个小节，| 分开同时弹的声部"), ("", "各声部一行里的拍数要相等"),
                ("# 注释", "# 之后到行尾都是注释"))),
    ("时值（写在音后，连写相加）", (("-", "1 拍"), (".", "半拍"), ("~", "1/4 拍"), ("*", "1/8 拍"),
                          ("{1/3}", "任意拍数"), ("a-.", "附点四分：1 拍加半拍"))),
)
RIGHT = (
    ("和弦、休止与延音", (("adg-", "和弦：几个键连写，同时按"), ("0-", "休止"),
                    ("--", "写在行首：延长上一个音，可跨小节"), ("+-", "写在行中：延长前一个音"))),
    ("连音组", (("(a.s.d.)-", "括号后写整组几拍：八分三连音"), ("(a-s.)-", "组内按各自时值的比例分"))),
    ("琶音与倚音", (("^adg-", "琶音：从左往右刷"), ("s'a-", "倚音：先弹 s，再弹 a"))),
    ("速度", (("speed=96", "每分钟 96 拍，第一个小节前必须有"), ("speed=>72", "从这里渐变到 72"),
            ("roll=50", "琶音间隔（毫秒）；grace= 是倚音的"))),
)
SECTIONS = LEFT + RIGHT
FOOT = "完整的写法见 docs 文件夹里的「曲谱格式」"


def fill(frame: tk.Frame, theme: Theme) -> None:
    """Write the card into ``frame``: the keys and the sections in two columns, and where to read more."""
    px, fonts = theme.px, theme.fonts
    columns = tk.Frame(frame, bg=PAPER)
    columns.pack(fill="x")
    left, right = tk.Frame(columns, bg=PAPER), tk.Frame(columns, bg=PAPER)
    left.pack(side="left", anchor="n")
    right.pack(side="left", anchor="n", padx=(px(26), 0))
    widest = max(theme.measure(fonts.mono, code) for _, rows in SECTIONS for code, _ in rows)

    def heading(column: tk.Frame, text: str, first: bool = False) -> None:
        tk.Label(column, text=text, font=fonts.shelf, fg=GOLD, bg=PAPER, anchor="w").pack(
            fill="x", pady=(0 if first else px(8), px(2)))

    heading(left, "键位", first=True)
    keys = tk.Frame(left, bg=PAPER)
    keys.pack(fill="x")
    for column, digit in enumerate("1234567", start=1):
        tk.Label(keys, text=digit, font=fonts.mono, fg=GOLD, bg=PAPER, width=2).grid(row=0, column=column)
    for row, (name, letters) in enumerate(KEYS, start=1):
        tk.Label(keys, text=name, font=fonts.body, fg=MUTED, bg=PAPER, anchor="w").grid(row=row, column=0, sticky="w",
                                                                                      padx=(0, px(10)))
        for column, key in enumerate(letters, start=1):
            tk.Label(keys, text=key, font=fonts.mono, fg=INK, bg=PAPER, width=2).grid(row=row, column=column)
    tk.Label(left, text="高音的数字上加点，低音的下加点", font=fonts.small, fg=MUTED, bg=PAPER,
             anchor="w").pack(fill="x", pady=(px(2), 0))

    for column, sections in ((left, LEFT), (right, RIGHT)):
        for index, (title, rows) in enumerate(sections):
            heading(column, title, first=column is right and index == 0)
            table = tk.Frame(column, bg=PAPER)
            table.pack(fill="x")
            table.columnconfigure(0, minsize=widest + px(12))
            for row, (code, meaning) in enumerate(rows):
                tk.Label(table, text=code, font=fonts.mono, fg=INK, bg=PAPER, anchor="w").grid(row=row, column=0,
                                                                                             sticky="w")
                tk.Label(table, text=meaning, font=fonts.body, fg=MUTED, bg=PAPER, anchor="w", justify="left",
                         wraplength=px(210)).grid(row=row, column=1, sticky="w", pady=(0, px(1)))
    tk.Label(frame, text=FOOT, font=fonts.small, fg=MUTED, bg=PAPER, anchor="w").pack(fill="x", pady=(px(12), 0))
