"""The pieces LyreBot's window is built of, each drawn in its own style rather than the system's:
icon buttons, rounded buttons and choices, tips, menus, a fine scrollbar, an ornamental rule and
a writing line.

``PopupMenu`` keeps the part of ``tk.Menu``'s interface LyreBot uses, so the
code that fills a menu, and the tests that read one, need not know the
difference.
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

from . import icons
from .theme import (FAINT, GOLD, GOLD_SOFT, HAIR, HOVER, INK, MUTED, ORANGE, ORANGE_DEEP, PAPER, PEACH, WARN,
                    WARN_DEEP, WHITE, Theme, fit, geometry, round_corners, screen_area)


def rounded(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, radius: float, **options) -> int:
    """A rounded rectangle on ``canvas`` (Tk's smoothed polygon, for soft fills that need no crisp edge)."""
    return canvas.create_polygon(rounded_points(x0, y0, x1, y1, radius), smooth=True, **options)


def rounded_points(x0: float, y0: float, x1: float, y1: float, radius: float) -> list[float]:
    r = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    return [x0 + r, y0, x0 + r, y0, x1 - r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y0 + r, x1, y1 - r,
            x1, y1 - r, x1, y1, x1 - r, y1, x1 - r, y1, x0 + r, y1, x0 + r, y1, x0, y1, x0, y1 - r,
            x0, y1 - r, x0, y0 + r, x0, y0 + r, x0, y0]


class Tooltip:
    """A few words in a small rounded card above a widget, once the pointer rests on it."""

    DELAY = 550  # milliseconds

    def __init__(self, widget: tk.Widget, text: str, theme: Theme):
        self.widget, self.text, self.theme = widget, text, theme
        self.window: tk.Toplevel | None = None
        self.job: str | None = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _: tk.Event | None = None) -> None:
        self.cancel()
        self.job = self.widget.winfo_toplevel().after(self.DELAY, self.show)

    def cancel(self) -> None:
        if self.job:
            try:
                self.widget.winfo_toplevel().after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def show(self) -> None:
        self.job = None
        if self.window or not self.widget.winfo_viewable():
            return
        window = self.window = tk.Toplevel(self.widget)
        window.withdraw()
        window.overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        own_edge = not round_corners(window, border=HAIR, small=True)
        label = tk.Label(window, text=self.text, bg=PAPER, fg=INK, font=self.theme.fonts.small,
                         padx=self.theme.px(8), pady=self.theme.px(3),
                         highlightthickness=1 if own_edge else 0, highlightbackground=HAIR)
        label.pack()
        window.update_idletasks()
        window.geometry(geometry(*self.position(window.winfo_reqwidth(), window.winfo_reqheight())))
        window.deiconify()

    def position(self, width: int, height: int) -> tuple[int, int]:
        """Centred above the widget, or below it at the top of its screen, and whole on that screen."""
        widget, gap = self.widget, self.theme.px(6)
        left, top = widget.winfo_rootx(), widget.winfo_rooty()
        area = screen_area(widget, left + widget.winfo_width() / 2, top + widget.winfo_height() / 2)
        x = left + (widget.winfo_width() - width) // 2
        y = top - height - gap
        if y < area[1]:
            y = top + widget.winfo_height() + gap
        return min(max(x, area[0]), area[2] - width), min(y, area[3] - height)

    def hide(self, _: tk.Event | None = None) -> None:
        self.cancel()
        if self.window:
            self.window.destroy()
            self.window = None


class IconButton(tk.Label):
    """A flat image button: fires on release inside it, and does nothing while disabled."""

    def __init__(self, parent: tk.Widget, images: dict[str, tk.PhotoImage], command: Callable[[], None],
                 tip: str = "", theme: Theme | None = None):
        super().__init__(parent, image=images["normal"], bg=PAPER, borderwidth=0, cursor="hand2")
        self.images, self.command = images, command
        self.enabled, self.inside, self.pressed = True, False, False
        self.bind("<Enter>", lambda _: self.hover(True))
        self.bind("<Leave>", lambda _: self.hover(False))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        self.tooltip = Tooltip(self, tip, theme) if tip and theme else None

    def set(self, images: dict[str, tk.PhotoImage] | None = None, enabled: bool | None = None) -> None:
        if images is not None:
            self.images = images
        if enabled is not None:
            self.enabled = enabled
            self.pressed = False
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self.redraw()

    def redraw(self) -> None:
        if not self.enabled:
            self.configure(image=self.images["disabled"])
        else:
            self.configure(image=self.images["hover" if self.inside else "normal"])

    def hover(self, inside: bool) -> None:
        self.inside = inside
        self.redraw()

    def press(self, _: tk.Event) -> None:
        self.pressed = self.enabled

    def release(self, event: tk.Event) -> None:
        fire = self.pressed and self.enabled and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self.pressed = False
        if fire:
            self.command()


class PillButton(tk.Canvas):
    """A rounded button with a hairline edge ("ghost"), or filled orange ("primary") or red ("danger")."""

    LOOKS = {  # state -> (fill, edge, text)
        "ghost": {"normal": (PAPER, HAIR, INK), "hover": (HOVER, GOLD_SOFT, INK), "disabled": (PAPER, HAIR, FAINT)},
        "primary": {"normal": (ORANGE, ORANGE, WHITE), "hover": (ORANGE_DEEP, ORANGE_DEEP, WHITE),
                    "disabled": (PEACH, PEACH, WHITE)},
        "danger": {"normal": (WARN, WARN, WHITE), "hover": (WARN_DEEP, WARN_DEEP, WHITE),
                   "disabled": (PEACH, PEACH, WHITE)},
    }

    def __init__(self, parent: tk.Widget, theme: Theme, text: str, command: Callable[[], None],
                 kind: str = "ghost", chevron: bool = False, font: tuple | None = None, least: int = 0):
        self.theme, self.command, self.kind, self.chevron = theme, command, kind, chevron
        self.font = font or theme.fonts.body
        self.least = least  # the narrowest it may be, in pixels
        self.enabled, self.inside, self.pressed = True, False, False
        self.tall = theme.px(24)
        self.wide = self.tall
        self._text = ""
        super().__init__(parent, width=self.tall, height=self.tall, bg=parent.cget("bg"), highlightthickness=0,
                         borderwidth=0, cursor="hand2", takefocus=False)
        self.bind("<Enter>", lambda _: self.hover(True))
        self.bind("<Leave>", lambda _: self.hover(False))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        self.set_text(text)

    @property
    def text(self) -> str:
        return self._text

    @staticmethod
    def width_of(theme: Theme, font: tuple, text: str, chevron: bool) -> int:
        """How wide a button with these words is, before any least width: the words with room either
        side, and with a chevron, the chevron and a little room before it."""
        width = theme.measure(font, text) + 2 * theme.px(10)
        return width + theme.px(14) if chevron else width

    def set_text(self, text: str) -> None:
        self._text = text
        width = self.width_of(self.theme, self.font, text, self.chevron)
        self.wide = max(width, self.least, self.tall + self.theme.px(8))
        self.configure(width=self.wide)
        self.redraw()

    def look(self) -> tuple[str, str, str]:
        state = "disabled" if not self.enabled else "hover" if self.inside else "normal"
        return self.LOOKS[self.kind][state]

    def redraw(self) -> None:
        self.delete("all")
        fill, edge, ink = self.look()
        self.create_image(0, 0, anchor="nw", image=self.theme.pill(self.wide, self.tall, fill, edge))
        middle = self.tall / 2
        if self.chevron:
            size = self.theme.px(10)
            right = self.wide - self.theme.px(9)
            self.create_text(self.theme.px(10), middle, anchor="w", text=self._text, fill=ink, font=self.font)
            color = FAINT if not self.enabled else GOLD
            self.create_image(right - size / 2, middle, image=self.theme.icon(
                "chevron-down", size, color, lambda s: icons.chevron(s, down=True)))
        else:
            self.create_text(self.wide / 2, middle, text=self._text, fill=ink, font=self.font)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.pressed = False
        self.configure(cursor="hand2" if enabled else "arrow")
        self.redraw()

    def hover(self, inside: bool) -> None:
        self.inside = inside
        self.redraw()

    def press(self, _: tk.Event) -> None:
        self.pressed = self.enabled

    def release(self, event: tk.Event) -> None:
        fire = self.pressed and self.enabled and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self.pressed = False
        if fire:
            self.command()


class Choice(PillButton):
    """A rounded "100% ⌄" that opens a small menu of options."""

    def __init__(self, parent: tk.Widget, theme: Theme, variable: tk.StringVar, options: tuple[str, ...],
                 on_change: Callable[[], None], actions: tuple[tuple[str, Callable[[], None], object], ...] = ()):
        self.variable, self.options = variable, options
        super().__init__(parent, theme, variable.get(), self.open, chevron=True)
        self.menu = PopupMenu(self, theme)
        for option in options:
            self.menu.add_radiobutton(label=option, value=option, variable=variable, command=on_change)
        if actions:
            self.menu.add_separator()
        for label, command, icon in actions:
            self.menu.add_command(label=label, command=command, icon=icon)
        variable.trace_add("write", lambda *_: self.set_text(self.variable.get()))

    def allow(self, allowed: tuple[str, ...] | None) -> None:
        """Grey out the options not in ``allowed``; None allows them all."""
        for index, option in enumerate(self.options):
            self.menu.entryconfigure(index, state="normal" if allowed is None or option in allowed else "disabled")

    def open(self) -> None:
        """The menu opens under the choice, or over it when the screen ends below."""
        if self.enabled:
            gap = self.theme.px(4)
            self.menu.post(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + gap,
                           bottom_instead=self.winfo_rooty() - gap)


class PopupMenu:
    """A menu drawn in LyreBot's style: paper, a gold edge, a quiet hover, a gold or orange mark per item."""

    def __init__(self, master: tk.Misc, theme: Theme):
        self.master, self.theme = master, theme
        self.items: list[dict] = []
        self.window: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.parent: PopupMenu | None = None
        self.child: PopupMenu | None = None
        self.active = -1
        self.rows: list[tuple[int, int]] = []
        self.posted_at = 0.0
        self.moved = False
        self.own_edge = False
        self.size = (0, 0)
        self.focus_before: tk.Misc | None = None
        self.jobs: list[str] = []

    # -- the part of tk.Menu's interface LyreBot uses --
    def add_command(self, label: str, command: Callable[[], None] | None = None, state: str = "normal",
                    accelerator: str = "", icon: Callable[[int], object] | None = None, danger: bool = False) -> None:
        self.items.append(dict(type="command", label=label, command=command, state=state, accelerator=accelerator,
                               icon=icon, danger=danger))

    def add_radiobutton(self, label: str, value: str, variable: tk.StringVar,
                        command: Callable[[], None] | None = None, state: str = "normal") -> None:
        self.items.append(dict(type="radiobutton", label=label, value=value, variable=variable, command=command,
                               state=state, accelerator="", icon=None, danger=False))

    def add_cascade(self, label: str, menu: "PopupMenu", icon: Callable[[int], object] | None = None,
                    state: str = "normal") -> None:
        self.items.append(dict(type="cascade", label=label, menu=menu, state=state, accelerator="", icon=icon,
                               danger=False))

    def add_separator(self) -> None:
        self.items.append(dict(type="separator", label="", state="disabled", accelerator=""))

    def delete(self, first: int, last: int | str | None = None) -> None:
        end = len(self.items) if last == "end" else first + 1 if last is None else int(last) + 1
        del self.items[first:end]
        self.redraw()

    def index(self, which: int | str) -> int | None:
        if which == "end":
            return len(self.items) - 1 if self.items else None
        return int(which)

    def type(self, index: int) -> str:
        return self.items[index]["type"]

    def entrycget(self, index: int, option: str) -> object:
        return self.items[index].get(option, "")

    def entryconfigure(self, index: int, **options) -> None:
        self.items[index].update(options)
        self.redraw()

    def invoke(self, index: int) -> None:
        item = self.items[index]
        if item["state"] == "disabled" or item["type"] in ("separator", "cascade"):
            return
        if item["type"] == "radiobutton":
            item["variable"].set(item["value"])
        if item.get("command"):
            item["command"]()

    def tk_popup(self, x: int, y: int) -> None:
        self.post(x, y)

    # -- drawing --
    def metrics(self) -> dict[str, int]:
        px = self.theme.px
        return dict(edge=px(5), row=px(26), rule=px(9), text=px(34), mark=px(14), right=px(14), gap=px(26),
                    corner=px(5), chevron=px(10))

    def measure(self) -> tuple[int, int]:
        m, fonts = self.metrics(), self.theme.fonts
        widest = max((self.theme.measure(fonts.body, item["label"]) for item in self.items), default=0)
        extra = max((self.theme.measure(fonts.small, item["accelerator"]) for item in self.items
                     if item.get("accelerator")), default=0)
        if any(item["type"] == "cascade" for item in self.items):
            extra = max(extra, m["chevron"])
        width = m["text"] + widest + (m["gap"] + extra if extra else 0) + m["right"]
        height = 2 * m["edge"] + sum(m["rule"] if item["type"] == "separator" else m["row"] for item in self.items)
        return max(width, self.theme.px(150)), height

    def redraw(self) -> None:
        if not self.canvas:
            return
        canvas, m, fonts = self.canvas, self.metrics(), self.theme.fonts
        width, height = self.size
        canvas.delete("all")
        if self.own_edge:
            canvas.create_rectangle(0, 0, width - 1, height - 1, outline=GOLD)
        y = m["edge"]
        self.rows = []
        for index, item in enumerate(self.items):
            if item["type"] == "separator":
                middle = y + m["rule"] // 2
                ornament = self.theme.fleuron(self.theme.px(5), GOLD_SOFT)
                gap = ornament.width() / 2 + self.theme.px(4)
                canvas.create_line(m["right"], middle, width / 2 - gap, middle, fill=HAIR)
                canvas.create_line(width / 2 + gap, middle, width - m["right"], middle, fill=HAIR)
                canvas.create_image(width / 2, middle, image=ornament)
                self.rows.append((y, y + m["rule"]))
                y += m["rule"]
                continue
            top, bottom = y, y + m["row"]
            self.rows.append((top, bottom))
            y = bottom
            enabled = item["state"] != "disabled"
            if index == self.active and enabled:
                rounded(canvas, self.theme.px(4), top + 1, width - self.theme.px(4), bottom - 1, m["corner"],
                        fill=HOVER, outline="")
            middle = (top + bottom) / 2
            ink = FAINT if not enabled else WARN if item.get("danger") else INK
            mark = None
            if item["type"] == "radiobutton" and item["variable"].get() == item["value"]:
                mark = self.theme.icon("check", m["mark"], ORANGE if enabled else FAINT, icons.check)
            elif item.get("icon"):  # drawn in ink with its gilt in gold; in red for deleting; faint when off
                color = FAINT if not enabled else WARN if item.get("danger") else INK
                gold = FAINT if not enabled else WARN if item.get("danger") else GOLD
                mark = self.theme.icon(item["icon"].__name__, m["mark"], color, item["icon"], gold)
            if mark is not None:
                canvas.create_image(m["text"] / 2 + self.theme.px(2), middle, image=mark)
            canvas.create_text(m["text"], middle, anchor="w", text=item["label"], fill=ink, font=fonts.body)
            if item["type"] == "cascade":
                canvas.create_image(width - m["right"] - m["chevron"] / 2, middle,
                                    image=self.theme.icon("chevron", m["chevron"], MUTED if enabled else FAINT,
                                                          icons.chevron))
            elif item.get("accelerator"):
                canvas.create_text(width - m["right"], middle, anchor="e", text=item["accelerator"],
                                   fill=FAINT if not enabled else MUTED, font=fonts.small)

    # -- showing and hiding --
    def post(self, x: int, y: int, parent: "PopupMenu | None" = None, bottom_instead: int | None = None) -> None:
        """Show the menu with its top left at (x, y); where that leaves too little room below, with its
        bottom at ``bottom_instead`` (for a menu of its own, the point it was opened at)."""
        if self.window:
            self.unpost()
        self.parent = parent
        x, y = int(x), int(y)
        width, height = self.size = self.measure()
        window = self.window = tk.Toplevel(self.master)
        window.withdraw()
        window.overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(window, width=width, height=height, bg=PAPER, highlightthickness=0, borderwidth=0)
        self.canvas.pack()
        # Kept whole on the screen it opens on: a second screen may lie left of or above the main
        # one, at negative coordinates.  A submenu that cannot open to the right of its menu opens
        # to the left of it; one that cannot open downwards ends where it would have begun.
        left_instead = None
        if parent and parent.window:
            anchor = (parent.window.winfo_rootx() + parent.size[0] / 2, parent.window.winfo_rooty() + parent.size[1] / 2)
            left_instead = parent.window.winfo_rootx() - width + self.theme.px(4)
        else:
            anchor = (x, y)
            bottom_instead = y if bottom_instead is None else bottom_instead
        margin = self.theme.px(4)
        left, top, right, bottom = screen_area(window, *anchor)
        x, y = fit(width, height, x, y, (left + margin, top + margin, right - margin, bottom - margin),
                   left_instead=left_instead, bottom_instead=bottom_instead)
        window.geometry(geometry(x, y, width, height))
        self.own_edge = not round_corners(window, border=GOLD)
        self.active = -1
        self.redraw()
        window.deiconify()
        window.lift()
        self.posted_at, self.moved = time.monotonic(), False
        window.bind("<Motion>", lambda event: self.top().pointer("motion", event))
        window.bind("<ButtonPress>", lambda event: self.top().pointer("press", event))
        window.bind("<ButtonRelease>", lambda event: self.top().pointer("release", event))
        window.bind("<KeyPress>", lambda event: self.top().key(event))
        if parent is None:
            try:
                self.focus_before = self.master.focus_get()
            except (KeyError, tk.TclError):
                self.focus_before = None
            self.jobs.append(window.after(200, self.watch))
        self.grab()

    def grab(self) -> None:
        if not self.window:
            return
        try:
            self.window.grab_set()
            self.window.focus_force()
        except tk.TclError:  # not on screen yet
            self.jobs.append(self.window.after(20, self.grab))

    def top(self) -> "PopupMenu":
        menu = self
        while menu.parent is not None:
            menu = menu.parent
        return menu

    def chain(self) -> list["PopupMenu"]:
        menus, menu = [], self.top()
        while menu is not None and menu.window is not None:
            menus.append(menu)
            menu = menu.child
        return menus

    def contains(self, x: int, y: int) -> bool:
        if not self.window:
            return False
        left, top = self.window.winfo_rootx(), self.window.winfo_rooty()
        return left <= x < left + self.size[0] and top <= y < top + self.size[1]

    def item_at(self, y_root: int) -> int:
        y = y_root - self.window.winfo_rooty()
        for index, (top, bottom) in enumerate(self.rows):
            if top <= y < bottom:
                return index
        return -1

    def pointer(self, kind: str, event: tk.Event) -> str:
        """Every pointer event while a menu is open comes here, to the first menu of the chain."""
        menus = self.chain()
        target = next((menu for menu in reversed(menus) if menu.contains(event.x_root, event.y_root)), None)
        if kind == "motion":
            self.moved = self.moved or time.monotonic() - self.posted_at > 0.15
            if target is not None:
                target.hover(target.item_at(event.y_root))
        elif kind == "press" and target is None:
            self.close()
        elif kind == "release" and target is not None and (self.moved or time.monotonic() - self.posted_at > 0.4):
            target.choose(target.item_at(event.y_root))
        return "break"

    def hover(self, index: int) -> None:
        if index == self.active:
            return
        self.active = index
        self.redraw()
        if self.child and (index < 0 or self.items[index].get("menu") is not self.child):
            self.child.unpost()
        if 0 <= index and self.items[index]["type"] == "cascade" and self.items[index]["state"] != "disabled":
            self.jobs.append(self.window.after(160, lambda: self.active == index and self.open_child(index)))

    def choose(self, index: int) -> None:
        if index < 0:
            return
        item = self.items[index]
        if item["state"] == "disabled" or item["type"] == "separator":
            return
        if item["type"] == "cascade":
            self.open_child(index)
            return
        self.top().close()
        self.invoke(index)

    def open_child(self, index: int) -> None:
        menu = self.items[index]["menu"]
        if not self.window or (self.child is menu and menu.window):
            return
        if self.child:
            self.child.unpost()
        row_top, row_bottom = self.rows[index]
        edge, origin = self.metrics()["edge"], self.window.winfo_rooty()
        self.child = menu
        menu.post(self.window.winfo_rootx() + self.size[0] - self.theme.px(4), origin + row_top - edge, parent=self,
                  bottom_instead=origin + row_bottom + edge)

    def key(self, event: tk.Event) -> str:
        menu = self.chain()[-1]
        steps = {"Up": -1, "Down": 1}
        if event.keysym in steps:
            menu.move(steps[event.keysym])
        elif event.keysym in ("Return", "KP_Enter", "space"):
            menu.choose(menu.active)
        elif event.keysym == "Escape":
            if menu.parent:
                menu.unpost()
            else:
                self.close()
        elif event.keysym == "Right" and 0 <= menu.active and menu.items[menu.active]["type"] == "cascade":
            menu.open_child(menu.active)
            if menu.child:
                menu.child.move(1)
        elif event.keysym == "Left" and menu.parent:
            menu.unpost()
        return "break"

    def move(self, step: int) -> None:
        live = [index for index, item in enumerate(self.items) if item["type"] != "separator" and item["state"] != "disabled"]
        if not live:
            return
        if self.active not in live:
            index = live[0] if step > 0 else live[-1]
        else:
            index = live[(live.index(self.active) + step) % len(live)]
        self.hover(index)

    def watch(self) -> None:
        """Close when LyreBot itself loses the focus, as when another program is clicked."""
        if not self.window:
            return
        try:
            away = self.master.focus_get() is None
        except (KeyError, tk.TclError):
            away = False
        if away:
            self.close()
        else:
            self.jobs.append(self.window.after(200, self.watch))

    def unpost(self) -> None:
        if self.child:
            self.child.unpost()
        if not self.window:
            return
        for job in self.jobs:
            try:
                self.window.after_cancel(job)
            except tk.TclError:
                pass
        self.jobs = []
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        self.window.destroy()
        self.window = self.canvas = None
        self.active = -1
        parent, self.parent = self.parent, None
        if parent is not None:
            parent.child = None
            parent.grab()

    def close(self) -> None:
        """Close this menu and every menu opened from it, and give the focus back."""
        top = self.top()
        focus = top.focus_before
        top.unpost()
        if focus is not None:
            try:
                focus.focus_set()
            except tk.TclError:
                pass


class ThinScrollbar(tk.Canvas):
    """A slim round-ended thumb that is barely there until the pointer comes near."""

    def __init__(self, parent: tk.Widget, theme: Theme, command: Callable[..., object]):
        self.theme, self.command = theme, command
        self.first, self.last = 0.0, 1.0
        self.inside = False
        self.drag: tuple[int, float] | None = None
        super().__init__(parent, width=theme.px(10), bg=PAPER, highlightthickness=0, borderwidth=0)
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<Enter>", lambda _: self.hover(True))
        self.bind("<Leave>", lambda _: self.hover(False))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.motion)
        self.bind("<ButtonRelease-1>", self.release)

    def set(self, first: float | str, last: float | str) -> None:
        self.first, self.last = float(first), float(last)
        self.redraw()

    def thumb(self) -> tuple[float, float]:
        height = max(self.winfo_height(), 1)
        top, bottom = self.first * height, self.last * height
        least = self.theme.px(28)
        if bottom - top < least:
            middle = (top + bottom) / 2
            top = min(max(middle - least / 2, 0), height - least)
            bottom = top + least
        return top, bottom

    def redraw(self) -> None:
        self.delete("all")
        busy = self.inside or self.drag is not None
        thick = self.theme.px(6 if busy else 4)
        top, bottom = self.thumb()
        middle = self.winfo_width() / 2
        inset = thick / 2 + self.theme.px(2)
        self.create_line(middle, top + inset, middle, max(bottom - inset, top + inset + 1), width=thick,
                         capstyle="round", fill=MUTED if busy else FAINT)

    def hover(self, inside: bool) -> None:
        self.inside = inside
        self.redraw()

    def press(self, event: tk.Event) -> None:
        top, bottom = self.thumb()
        if top <= event.y <= bottom:
            self.drag = (event.y, self.first)
        else:
            self.command("scroll", -1 if event.y < top else 1, "pages")

    def motion(self, event: tk.Event) -> None:
        if self.drag is not None:
            start, first = self.drag
            self.command("moveto", first + (event.y - start) / max(self.winfo_height(), 1))

    def release(self, _: tk.Event) -> None:
        self.drag = None
        self.redraw()


class Ornament(tk.Canvas):
    """A hairline across, with a small gold ·◆· set in the middle."""

    def __init__(self, parent: tk.Widget, theme: Theme):
        self.theme = theme
        super().__init__(parent, height=theme.px(10), bg=PAPER, highlightthickness=0, borderwidth=0)
        self.bind("<Configure>", lambda _: self.redraw())

    def redraw(self) -> None:
        self.delete("all")
        width, middle = self.winfo_width(), self.winfo_height() / 2
        ornament = self.theme.fleuron(self.theme.px(7), GOLD)
        centre, gap = width / 2, ornament.width() / 2 + self.theme.px(6)
        self.create_line(0, middle, centre - gap, middle, fill=HAIR)
        self.create_line(centre + gap, middle, width, middle, fill=HAIR)
        self.create_image(centre, middle, image=ornament)


class WritingLine(tk.Frame):
    """A place to write on a single gold-and-hair line, with a hint in faint ink while it is empty."""

    def __init__(self, parent: tk.Widget, theme: Theme, hint: str = "", icon: Callable[[int], object] | None = None,
                 text: str = ""):
        super().__init__(parent, bg=PAPER)
        self.theme = theme
        self.value = tk.StringVar(master=self, value=text)
        row = tk.Frame(self, bg=PAPER)
        row.pack(fill="x")
        if icon is not None:
            size = theme.px(13)
            tk.Label(row, image=theme.icon(icon.__name__, size, MUTED, icon, GOLD), bg=PAPER).pack(
                side="left", padx=(0, theme.px(6)))
        self.entry = tk.Entry(row, textvariable=self.value, font=theme.fonts.body, relief="flat", borderwidth=0,
                              bg=PAPER, fg=INK, insertbackground=INK, insertwidth=max(1, theme.px(1)),
                              highlightthickness=0, selectbackground=HOVER, selectforeground=INK)
        self.entry.pack(side="left", fill="x", expand=True, ipady=theme.px(3))
        self.hint = tk.Label(row, text=hint, font=theme.fonts.body, bg=PAPER, fg=FAINT, cursor="xterm")
        self.hint.bind("<Button-1>", lambda _: self.entry.focus_set())
        self.line = tk.Frame(self, bg=HAIR, height=max(1, theme.px(1)))
        self.line.pack(fill="x")
        self.entry.bind("<FocusIn>", lambda _: self.restyle(), add="+")
        self.entry.bind("<FocusOut>", lambda _: self.restyle(), add="+")
        self.value.trace_add("write", lambda *_: self.restyle())
        self.restyle()

    def restyle(self) -> None:
        try:
            focused = self.entry.focus_get() is self.entry
        except (KeyError, tk.TclError):
            focused = False
        self.line.configure(bg=GOLD if focused else HAIR)
        if self.value.get() or focused or not self.hint.cget("text"):
            self.hint.place_forget()
        else:
            self.hint.place(in_=self.entry, x=0, rely=0.5, anchor="w")
