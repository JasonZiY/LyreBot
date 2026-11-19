"""LyreBot's own dialogs, in place of the system's: a question set in the serif, a gold rule under it,
the details, and rounded buttons, the one Enter presses in orange (red when something is deleted).

Each waits for an answer, as ``tkinter.simpledialog`` and ``tkinter.messagebox`` do.
"""

from __future__ import annotations

import tkinter as tk

from . import icons
from .theme import GOLD_SOFT, INK, MUTED, PAPER, WARN, Theme, dress
from .widgets import PillButton, WritingLine


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
    if owner.winfo_viewable():
        window.transient(owner)
    answer: list[object] = [None, ""]
    cancel = buttons[0][2] if len(buttons) > 1 else default

    frame = tk.Frame(window, bg=PAPER)
    frame.pack(fill="both", padx=px(24), pady=(px(20), px(18)))
    head = tk.Frame(frame, bg=PAPER)
    head.pack(fill="x")
    if warning:
        size = px(18)
        shape, marks = icons.warning(size)
        mark = theme.image(f"warning-badge:{WARN}", size, size, lambda: [(WARN, shape), *[("#FFFFFF", m) for m in marks]])
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
    w, h = window.winfo_reqwidth(), window.winfo_reqheight()
    if owner.winfo_viewable():
        x = owner.winfo_rootx() + (owner.winfo_width() - w) // 2
        y = owner.winfo_rooty() + max((owner.winfo_height() - h) // 3, 0)
    else:
        x, y = (window.winfo_screenwidth() - w) // 2, (window.winfo_screenheight() - h) // 3
    window.geometry(f"+{max(0, x)}+{max(0, y)}")
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
