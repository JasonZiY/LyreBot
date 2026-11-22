"""LyreBot's own dialogs, in place of the system's: a question set in the serif, a gold rule under it,
the details, and rounded buttons, the one Enter presses in orange (red when something is deleted).
Where the window they belong to wears LyreBot's own leather title bar, so do they: the lyre, the title
and × in gilt, a bar to move the dialog by.

Each waits for an answer, as ``tkinter.simpledialog`` and ``tkinter.messagebox`` do.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import chrome, icons
from .theme import GOLD_SOFT, INK, MUTED, PAPER, WARN, Theme, dress, geometry, round_corners, screen_area
from .widgets import PillButton, TitleBar, WritingLine


def ask_text(title: str, prompt: str, initialvalue: str = "", parent: tk.Misc | None = None) -> str | None:
    """What was written, or None if the dialog was cancelled."""
    answer = _show(parent, title, title, prompt, [("取消", "ghost", None), ("确定", "primary", True)],
                   default=True, text=initialvalue)
    return answer[1] if answer[0] else None


def confirm(title: str, message: str, parent: tk.Misc | None = None, yes: str = "确定", no: str = "取消",
            danger: bool = False) -> bool:
    """The first paragraph of ``message`` is the question, the rest its details.  Enter says yes,
    or, when ``danger``, no."""
    heading, _, body = message.partition("\n\n")
    choice = _show(parent, title, heading, body.strip(), [(no, "ghost", False), (yes, "danger" if danger else "primary", True)],
                   default=not danger)
    return bool(choice[0])


def error(title: str, message: str, parent: tk.Misc | None = None) -> None:
    _show(parent, title, title, message, [("知道了", "primary", True)], default=True, warning=True)


def info(title: str, message: str, parent: tk.Misc | None = None) -> None:
    _show(parent, title, title, message, [("知道了", "primary", True)], default=True)


def card(parent: tk.Misc, title: str, fill: Callable[[tk.Frame, Theme], None],
         on_close: Callable[[], None] | None = None, note: str | None = None) -> tk.Toplevel:
    """A card to keep open while working, in the dialogs' dress, beside LyreBot's window rather than over it;
    ``fill`` writes what it holds into the frame it is given, and ``note``, if any, is a line of small words
    at its foot, left of the button.  Unlike the dialogs it does not wait and takes no grab; it closes with
    ×, Escape or its button, and ``on_close`` is told."""
    owner = parent.winfo_toplevel()
    theme = Theme.of(parent)
    px = theme.px
    window = tk.Toplevel(owner)
    window.withdraw()
    window.title(title)
    window.configure(bg=PAPER)
    window.resizable(False, False)
    leather = chrome.available() and getattr(owner, "lyrebot_frame", None) is not None
    if owner.winfo_viewable() and not leather:
        window.transient(owner)

    def close() -> None:
        if window.winfo_exists():
            window.destroy()
        if on_close is not None:
            on_close()
    if leather:
        edges = chrome.Frame(edge=0, caption=0)
        bar = TitleBar(window, theme, edges, title, "", None, close)
        bar.pack(fill="x")
    frame = tk.Frame(window, bg=PAPER)
    frame.pack(fill="both", padx=px(22), pady=(px(16), px(16)))
    fill(frame, theme)
    row = tk.Frame(frame, bg=PAPER)
    row.pack(fill="x", pady=(px(14), 0))
    PillButton(row, theme, "收起", close, kind="ghost", least=px(72)).pack(side="right")
    if note:
        tk.Label(row, text=note, font=theme.fonts.small, fg=MUTED, bg=PAPER, anchor="w").pack(side="left")
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _: close())
    window.update_idletasks()
    window.geometry(geometry(*_beside(owner, window.winfo_reqwidth(), window.winfo_reqheight())))
    keep_over(owner, window)
    if leather and chrome.adopt(window, edges, see_through=(window, bar), owner=owner):
        round_corners(window, border=None)
    else:
        if leather:
            bar.destroy()
        window.deiconify()
        dress(window)
    return window


def keep_over(owner: tk.Misc, window: tk.Toplevel) -> None:
    """Kept on top as ``owner`` is (its title bar's pin): Windows keeps a window over its owner only among the
    windows not kept on top, so one that is not would open, and stay, under a pinned LyreBot."""
    try:
        window.attributes("-topmost", bool(owner.attributes("-topmost")))
    except tk.TclError:
        pass


def _beside(owner: tk.Misc, width: int, height: int) -> tuple[int, int]:
    """Beside LyreBot's window, level with its top: to its left where there is room on its screen, else to
    its right, else over its right-hand side.  Whole on that screen."""
    gap = Theme.of(owner).px(8)
    left, top, right, bottom = screen_area(owner, owner.winfo_rootx() + owner.winfo_width() / 2,
                                           owner.winfo_rooty() + owner.winfo_height() / 2)
    x = owner.winfo_rootx() - width - gap
    if x < left:
        x = owner.winfo_rootx() + owner.winfo_width() + gap
        if x + width > right:
            x = right - width
    return min(max(x, left), right - width), min(max(owner.winfo_rooty(), top), bottom - height)


def _show(parent: tk.Misc | None, title: str, heading: str, body: str, buttons: list[tuple[str, str, object]],
          default: object, text: str | None = None, warning: bool = False) -> tuple[object, str]:
    parent = parent or tk._get_default_root()  # type: ignore[attr-defined]
    owner = parent.winfo_toplevel()
    theme = Theme.of(parent)
    px, fonts = theme.px, theme.fonts
    window = tk.Toplevel(owner)
    window.withdraw()
    window.title(title)
    window.configure(bg=PAPER)
    window.resizable(False, False)
    leather = chrome.available() and getattr(owner, "lyrebot_frame", None) is not None
    if owner.winfo_viewable() and not leather:
        window.transient(owner)
    answer: list[object] = [None, ""]
    cancel = buttons[0][2] if len(buttons) > 1 else default
    if leather:  # the owner's dress: a title bar of leather, × alone on it
        edges = chrome.Frame(edge=0, caption=0)  # (no edges to size it by: a press on the bar moves it)
        bar = TitleBar(window, theme, edges, title, "", None, lambda: finish(cancel))
        bar.pack(fill="x")

    frame = tk.Frame(window, bg=PAPER)
    frame.pack(fill="both", padx=px(24), pady=(px(20), px(18)))
    head = tk.Frame(frame, bg=PAPER)
    head.pack(fill="x")
    if warning:
        mark = theme.icon("warning", px(18), WARN, icons.warning)
        tk.Label(head, image=mark, bg=PAPER).pack(side="left", padx=(0, px(8)), anchor="n", pady=(px(2), 0))
    width = px(320)
    tk.Label(head, text=heading, font=fonts.title, fg=INK, bg=PAPER, justify="left", anchor="w",
             wraplength=width).pack(side="left", fill="x")
    tk.Frame(frame, bg=GOLD_SOFT, height=max(1, px(1))).pack(fill="x", pady=(px(10), px(10)))
    if body:
        tk.Label(frame, text=body, font=fonts.body, fg=MUTED if text is None else INK, bg=PAPER, justify="left",
                 anchor="w", wraplength=width).pack(fill="x")
    line = None
    if text is not None:
        line = WritingLine(frame, theme, text=text)
        line.pack(fill="x", pady=(px(12), 0))
    row = tk.Frame(frame, bg=PAPER)
    row.pack(fill="x", pady=(px(18), 0))

    def finish(choice: object) -> None:
        answer[0] = choice
        answer[1] = line.value.get() if line else ""
        try:
            window.grab_release()
        except tk.TclError:
            pass
        window.destroy()

    for label, kind, value in reversed(buttons):
        PillButton(row, theme, label, lambda value=value: finish(value), kind=kind,
                   least=px(72)).pack(side="right", padx=(px(8), 0))
    window.protocol("WM_DELETE_WINDOW", lambda: finish(cancel))
    window.bind("<Return>", lambda _: finish(default))
    window.bind("<KP_Enter>", lambda _: finish(default))
    window.bind("<Escape>", lambda _: finish(cancel))

    window.update_idletasks()
    window.geometry(geometry(*_place(owner, window.winfo_reqwidth(), window.winfo_reqheight())))
    keep_over(owner, window)
    if leather and chrome.adopt(window, edges, see_through=(window, bar), owner=owner):
        round_corners(window, border=None)
    else:
        if leather:  # Windows would not lend the frame after all: its own bar, as other dialogs have
            bar.destroy()
        window.deiconify()
        dress(window)
    try:
        window.grab_set()
    except tk.TclError:
        pass
    if line is not None:
        line.entry.focus_force()
        line.entry.select_range(0, "end")
        line.entry.icursor("end")
    else:
        window.focus_force()
    owner.wait_window(window)
    return answer[0], str(answer[1])


def _place(owner: tk.Misc, width: int, height: int) -> tuple[int, int]:
    """Over LyreBot's window, a third of the way down, on whichever screen that is; while the window is
    hidden, in the middle of the screen the pointer is on.  Whole on that screen either way."""
    if owner.winfo_viewable():
        left, top, right, bottom = screen_area(owner, owner.winfo_rootx() + owner.winfo_width() / 2,
                                               owner.winfo_rooty() + owner.winfo_height() / 2)
        x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2
        y = owner.winfo_rooty() + max((owner.winfo_height() - height) // 3, 0)
    else:
        left, top, right, bottom = screen_area(owner, *owner.winfo_pointerxy())
        x, y = (left + right - width) // 2, top + (bottom - top - height) // 3
    return min(max(x, left), right - width), min(max(y, top), bottom - height)
