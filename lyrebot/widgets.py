"""The pieces LyreBot's window is built of, each drawn in its own style rather than the system's:
icon buttons, rounded buttons and choices, tips, menus, a fine scrollbar, an ornamental rule,
a writing line, the window's leather title bar, and the sheets, words, pictures and the line to write on
that lie on the main window's page (see ``paper``) where a Frame, a Label or an Entry would hide it.

``PopupMenu`` keeps the part of ``tk.Menu``'s interface LyreBot uses, so the
code that fills a menu, and the tests that read one, need not know the
difference.
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

from . import icons, ime
from .paper import OnPage
from .theme import (EMBER, FAINT, GILT, GILT_BRIGHT, GILT_DEEP, GOLD, GOLD_SOFT, HAIR, HOVER, INK, LEATHER_EDGE,
                    LEATHER_FOOT, LEATHER_HOVER, LEATHER_TOP, MUTED, ORANGE, ORANGE_DEEP, PAPER, PEACH, VEIL,
                    VEIL_FAINT, VEIL_HOVER, WARN, WARN_DEEP, WHITE, Theme, fit, geometry, round_corners, screen_area)


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


class IconButton(OnPage, tk.Canvas):
    """A flat image button: fires on release inside it, and does nothing while disabled.  It lies on the
    page, as big as a Label showing the image would be."""

    def __init__(self, parent: tk.Widget, images: dict[str, tk.PhotoImage], command: Callable[[], None],
                 tip: str = "", theme: Theme | None = None):
        image = images["normal"]
        width, height = image.width() + 2, image.height() + 2  # a Label's padding of a pixel all round
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"), highlightthickness=0,
                         borderwidth=0, cursor="hand2")
        self.face = self.create_image(width / 2, height / 2, image=image)
        self.images, self.command = images, command
        self.enabled, self.inside, self.pressed = True, False, False
        self.bind("<Enter>", lambda _: self.hover(True))
        self.bind("<Leave>", lambda _: self.hover(False))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        self.tooltip = Tooltip(self, tip, theme) if tip and theme else None
        if theme is not None:
            self.take_page(theme)

    def set(self, images: dict[str, tk.PhotoImage] | None = None, enabled: bool | None = None) -> None:
        if images is not None:
            self.images = images
        if enabled is not None:
            self.enabled = enabled
            self.pressed = False
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self.redraw()

    def redraw(self) -> None:
        state = "disabled" if not self.enabled else "hover" if self.inside else "normal"
        self.itemconfigure(self.face, image=self.images[state])

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


class Callout(Tooltip):
    """A few words in the tips' card, under a point of the screen rather than over a widget, said when asked
    (``say``) and gone after a while or when ``hide`` is called."""

    def __init__(self, widget: tk.Widget, theme: Theme):  # (no bindings: it is not the widget's tip)
        self.widget, self.text, self.theme = widget, "", theme
        self.window: tk.Toplevel | None = None
        self.job: str | None = None
        self.ending: str | None = None
        self.at = (0, 0)

    def say(self, text: str, x: int, y: int, wait: int = 0, last: int = 2200) -> None:
        """Show ``text`` centred under (x, y), after ``wait`` milliseconds, for ``last`` milliseconds."""
        self.hide()
        self.text, self.at = text, (x, y)
        top = self.widget.winfo_toplevel()
        if wait:
            self.job = top.after(wait, self.show)
        else:
            self.show()
        self.ending = top.after(wait + last, self.hide)

    def position(self, width: int, height: int) -> tuple[int, int]:
        x, y = self.at
        gap = self.theme.px(6)
        area = screen_area(self.widget, x, y)
        return min(max(x - width // 2, area[0]), area[2] - width), min(y + gap, area[3] - height)

    def hide(self, _: tk.Event | None = None) -> None:
        if self.ending:
            try:
                self.widget.winfo_toplevel().after_cancel(self.ending)
            except tk.TclError:
                pass
            self.ending = None
        super().hide()


class PillButton(OnPage, tk.Canvas):
    """A rounded button with a hairline edge ("ghost"), or filled orange ("primary") or red ("danger").  On
    the main window's page a ghost's face is a veil of white over the paper rather than paper of its own."""

    LOOKS = {  # state -> (fill, edge, text)
        "ghost": {"normal": (PAPER, HAIR, INK), "hover": (HOVER, GOLD_SOFT, INK), "disabled": (PAPER, HAIR, FAINT)},
        "primary": {"normal": (ORANGE, ORANGE, WHITE), "hover": (ORANGE_DEEP, ORANGE_DEEP, WHITE),
                    "disabled": (PEACH, PEACH, WHITE)},
        "danger": {"normal": (WARN, WARN, WHITE), "hover": (WARN_DEEP, WARN_DEEP, WHITE),
                   "disabled": (PEACH, PEACH, WHITE)},
    }
    ON_PAGE = {"normal": (VEIL, HAIR, INK), "hover": (VEIL_HOVER, GOLD_SOFT, INK),
               "disabled": (VEIL_FAINT, HAIR, FAINT)}  # a ghost's looks on the page

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
        self.take_page(theme)
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
        if self.page is not None and self.kind == "ghost":
            return self.ON_PAGE[state]
        return self.LOOKS[self.kind][state]

    def redraw(self) -> None:
        self.delete("!paper")
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
        watch = variable.trace_add("write", lambda *_: self.set_text(self.variable.get()))
        # The variable outlives the choice when the window is drawn again: stop watching it then.
        self.bind("<Destroy>", lambda event: event.widget is self and variable.trace_remove("write", watch), add="+")

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


class Ornament(OnPage, tk.Canvas):
    """A hairline across, with a small gold ·◆· set in the middle."""

    def __init__(self, parent: tk.Widget, theme: Theme):
        self.theme = theme
        super().__init__(parent, height=theme.px(10), bg=parent.cget("bg"), highlightthickness=0, borderwidth=0)
        self.take_page(theme)
        self.bind("<Configure>", lambda _: self.redraw(), add="+")

    def redraw(self) -> None:
        self.delete("!paper")
        width, middle = self.winfo_width(), self.winfo_height() / 2
        ornament = self.theme.fleuron(self.theme.px(7), GOLD)
        centre, gap = width / 2, ornament.width() / 2 + self.theme.px(6)
        self.create_line(0, middle, centre - gap, middle, fill=HAIR)
        self.create_line(centre + gap, middle, width, middle, fill=HAIR)
        self.create_image(centre, middle, image=ornament)


class WritingLine(tk.Frame):
    """A place to write on a single gold-and-hair line, with a hint in faint ink while it is empty."""

    def __init__(self, parent: tk.Widget, theme: Theme, hint: str = "", icon: Callable[[int], object] | None = None,
                 text: str = "", bg: str = PAPER):
        """``bg`` is what it is written on: a dialog's paper, or the even head of the window's page."""
        super().__init__(parent, bg=bg)
        self.theme = theme
        self.value = tk.StringVar(master=self, value=text)
        row = tk.Frame(self, bg=bg)
        row.pack(fill="x")
        if icon is not None:
            size = theme.px(13)
            tk.Label(row, image=theme.icon(icon.__name__, size, MUTED, icon, GOLD), bg=bg).pack(
                side="left", padx=(0, theme.px(6)))
        self.entry = tk.Entry(row, textvariable=self.value, font=theme.fonts.body, relief="flat", borderwidth=0,
                              bg=bg, fg=INK, insertbackground=INK, insertwidth=max(1, theme.px(1)),
                              highlightthickness=0, selectbackground=HOVER, selectforeground=INK)
        self.entry.pack(side="left", fill="x", expand=True, ipady=theme.px(3))
        self.hint = tk.Label(row, text=hint, font=theme.fonts.body, bg=bg, fg=FAINT, cursor="xterm")
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


class Sheet(OnPage, tk.Canvas):
    """A stretch of the page that holds other pieces, as a Frame would, with the paper showing between them.

    A canvas asks for its own ``width`` and ``height`` again whenever it is configured, over what its packed
    pieces asked for, so a sheet that holds pieces is left alone once they are in it."""

    def __init__(self, parent: tk.Misc, theme: Theme, **options):
        options = {"width": 1, "height": 1, "bg": parent.cget("bg"), "highlightthickness": 0, "borderwidth": 0,
                   **options}
        super().__init__(parent, **options)
        self.take_page(theme)


class Words(OnPage, tk.Canvas):
    """Words written straight on the page, standing in for a tk.Label: ``configure`` and ``cget`` answer for
    ``text``, ``fg``, ``font`` and ``wraplength`` as a Label's would, and it is as big as that Label would
    be, padding and all, so the window keeps its spacing."""

    PAD = 3  # a Label's border and padding, in pixels at any scale
    OWN = ("text", "fg", "font", "wraplength", "anchor", "justify")

    def __init__(self, parent: tk.Misc, theme: Theme, text: str = "", fg: str = INK, font: tuple | None = None,
                 anchor: str = "w", justify: str = "left", wraplength: int = 0):
        self.theme = theme
        self.words = {"text": text, "fg": fg, "font": font or theme.fonts.body, "anchor": anchor,
                      "justify": justify, "wraplength": wraplength}
        super().__init__(parent, width=1, height=1, bg=parent.cget("bg"), highlightthickness=0, borderwidth=0)
        self.take_page(theme)
        self.bind("<Configure>", lambda _: self.redraw(), add="+")
        self.redraw()

    def configure(self, cnf=None, **options):  # type: ignore[override]
        own = {key: options.pop(key) for key in self.OWN if key in options}
        result = super().configure(cnf, **options) if cnf or options else None
        if own and any(self.words[key] != value for key, value in own.items()):
            self.words.update(own)
            self.redraw()
        return result

    config = configure  # type: ignore[assignment]

    def cget(self, key: str):  # type: ignore[override]
        return self.words[key] if key in self.words else super().cget(key)

    def redraw(self) -> None:
        self.delete("!paper")
        words, pad = self.words, self.PAD
        wrap = max(int(words["wraplength"] or 0) - 2 * pad, 0)
        item = self.create_text(pad, pad, text=words["text"], font=words["font"], fill=words["fg"], anchor="nw",
                                width=wrap, justify=words["justify"])
        line = int(self.tk.call("font", "metrics", words["font"], "-linespace"))
        box = self.bbox(item) if words["text"] else None
        wide = max(box[2] - box[0] - 2, 0) if box else 0  # a text's box has a pixel to spare either side
        tall = max(box[3] - box[1] if box else 0, line)
        need = (wide + 2 * pad, tall + 2 * pad)
        if (int(super().cget("width")), int(super().cget("height"))) != need:
            super().configure(width=need[0], height=need[1])
        width = self.winfo_width() if self.winfo_width() > 1 else need[0]
        height = self.winfo_height() if self.winfo_height() > 1 else need[1]
        x = {"w": pad, "e": width - pad - wide}.get(words["anchor"], (width - wide) / 2)
        self.coords(item, x, (height - tall) / 2)


class Picture(OnPage, tk.Canvas):
    """An image on the page, standing in for a tk.Label showing it (with a Label's padding)."""

    def __init__(self, parent: tk.Misc, theme: Theme, image: tk.PhotoImage, pad: int = 3):
        width, height = image.width() + 2 * pad, image.height() + 2 * pad
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"), highlightthickness=0,
                         borderwidth=0)
        self.create_image(width / 2, height / 2, image=image)
        self.take_page(theme)


class PageLine(Sheet):
    """A line to write on, written straight on the window's page: a magnifier, the words as they are typed,
    and a hair line under them that turns gold while they have the keyboard.

    A tk.Entry is solid, which shows wherever the page is not one even colour, so the words are a canvas
    text item edited by hand (``Typing``).  What an input method chooses reaches Tk as ordinary key
    presses, and what it is still composing is shown in the line itself (``ime``), so Chinese goes in as
    it would into an Entry.  The magnifier is part of the line: the pointer is an I-beam over all of it,
    and a press there gives it the keyboard.  ``entry`` answers the part of an Entry's interface LyreBot
    uses: get, insert, delete, focus_set and bind."""

    def __init__(self, parent: tk.Misc, theme: Theme, icon: Callable[[int], object] | None = None):
        super().__init__(parent, theme)
        px = theme.px
        self.columnconfigure(1, weight=1)
        if icon is not None:
            mark = Picture(self, theme, theme.icon(icon.__name__, px(13), MUTED, icon, GOLD))
            mark.configure(cursor="xterm")
            mark.grid(row=0, column=0, padx=(0, px(6)))
            mark.bind("<Button-1>", lambda _: self.entry.focus_set())
        self.typing = Typing(self, theme, self.restyle)
        self.typing.grid(row=0, column=1, sticky="ew")
        self.line = tk.Frame(self, bg=HAIR, height=max(1, px(1)))
        self.line.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.entry = self.typing

    def restyle(self, focused: bool) -> None:
        self.line.configure(bg=GOLD if focused else HAIR)


class Typing(OnPage, tk.Canvas):
    """The words of a ``PageLine``: one canvas text item, its insertion cursor and selection, kept in sight
    as they grow longer than the line, and what an input method is composing, shown at the cursor with a
    gold line under it.  It answers ``get``, ``insert``, ``delete`` and ``focus_set`` as a tk.Entry does
    (an Entry's ``delete(first, last)`` stops short of ``last``; a canvas's ``dchars`` does not), and the
    canvas's own methods of those names are reached through tk.Canvas."""

    def __init__(self, parent: tk.Misc, theme: Theme, on_focus: Callable[[bool], None]):
        self.theme, self.on_focus = theme, on_focus
        px = theme.px
        self.font = theme.fonts.body
        self.pad = px(3)  # above and below the words, as an Entry in a WritingLine has
        line = self.line_height = int(theme.root.tk.call("font", "metrics", self.font, "-linespace"))
        tk.Canvas.__init__(self, parent, width=1, height=line + 2 * self.pad, bg=parent.cget("bg"),
                           highlightthickness=0, borderwidth=0, cursor="xterm", takefocus=True, insertbackground=INK,
                           insertwidth=max(1, px(1)), selectbackground=HOVER, selectforeground=INK)
        self.take_page(theme)
        self.words = self.create_text(0, self.pad + line / 2, anchor="w", text="", font=self.font, fill=INK)
        self.shift = 0  # how far the words are moved left, to keep the cursor in sight
        self.anchor = 0  # where a selection made with the mouse began
        self.composition = ime.attach(self.winfo_toplevel())  # what an input method is composing here
        self.seen = 0  # the composition's version last shown
        self.watching: str | None = None
        self.bind("<Configure>", lambda _: self.follow(), add="+")
        self.bind("<FocusIn>", lambda _: self.focused(True))
        self.bind("<FocusOut>", lambda _: self.focused(False))
        self.bind("<Button-1>", self.press)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<Double-Button-1>", lambda _: self.select_all())
        self.bind("<Key>", self.key)

    # -- what an Entry answers --
    def get(self) -> str:
        return self.itemcget(self.words, "text")

    def insert(self, index, text: str) -> None:  # type: ignore[override]
        tk.Canvas.insert(self, self.words, index, text)
        self.follow()

    def delete(self, first, last=None) -> None:  # type: ignore[override]
        start = self.place_of(first)
        end = start + 1 if last is None else self.place_of(last)
        if end > start:
            self.dchars(self.words, start, end - 1)
        self.follow()

    def focus_set(self) -> None:
        tk.Canvas.focus_set(self)
        self.focus(self.words)

    def place_of(self, index) -> int:
        return len(self.get()) if index == "end" else int(tk.Canvas.index(self, self.words, index))

    # -- typing --
    def key(self, event: tk.Event) -> str | None:
        name, held = event.keysym, event.state
        control = bool(held & 0x4) and not held & 0x20000  # (AltGr reports Control and Alt together)
        at, chosen = self.place_of("insert"), self.chosen()
        if name in ("BackSpace", "Delete") and chosen:
            self.cut(chosen)
        elif name == "BackSpace":
            if at:
                self.delete(at - 1)
        elif name == "Delete":
            self.delete(at)
        elif name in ("Left", "Right"):
            if chosen:  # to that end of the selection
                self.put_cursor(chosen[0] if name == "Left" else chosen[1])
            else:
                self.put_cursor(at - 1 if name == "Left" else at + 1)
        elif name in ("Home", "End"):
            self.put_cursor(0 if name == "Home" else len(self.get()))
        elif control and name.lower() == "a":
            self.select_all()
        elif control and name.lower() in ("c", "x") and chosen:
            self.clipboard_clear()
            self.clipboard_append(self.get()[chosen[0]:chosen[1]])
            if name.lower() == "x":
                self.cut(chosen)
        elif control and name.lower() == "v":
            try:
                pasted = " ".join(self.clipboard_get().split())
            except tk.TclError:
                return "break"
            self.write(pasted)
        elif event.char and event.char.isprintable() and not control:
            self.write(event.char)
        else:
            return None  # Tab and the rest go on to whoever else listens
        return "break"

    def write(self, text: str) -> None:
        chosen = self.chosen()
        if chosen:
            self.cut(chosen)
        self.insert("insert", text)

    def chosen(self) -> tuple[int, int] | None:
        """The selected characters, as an Entry would slice them, if any are selected here."""
        try:
            if str(self.select_item()) != str(self.words):
                return None
            first = int(tk.Canvas.index(self, self.words, "sel.first"))
            last = int(tk.Canvas.index(self, self.words, "sel.last"))  # the last one selected, not past it
        except (tk.TclError, TypeError, ValueError):
            return None
        return (first, last + 1) if last >= first else None

    def cut(self, chosen: tuple[int, int]) -> None:
        self.select_clear()
        self.delete(chosen[0], chosen[1])
        self.put_cursor(chosen[0])

    def put_cursor(self, place: int) -> None:
        self.select_clear()
        self.icursor(self.words, min(max(place, 0), len(self.get())))
        self.follow()

    def select_all(self) -> None:
        self.focus_set()
        if self.get():
            self.select_from(self.words, 0)
            self.select_to(self.words, len(self.get()) - 1)

    def press(self, event: tk.Event) -> None:
        self.focus_set()
        self.select_clear()
        self.anchor = int(tk.Canvas.index(self, self.words, f"@{self.canvasx(event.x)},{self.canvasy(event.y)}"))
        self.icursor(self.words, self.anchor)
        self.follow()

    def drag(self, event: tk.Event) -> None:
        here = int(tk.Canvas.index(self, self.words, f"@{self.canvasx(event.x)},{self.canvasy(event.y)}"))
        self.icursor(self.words, here)
        if here == self.anchor:
            self.select_clear()
        else:  # the characters between the two places (a canvas selects from one character to another, both in)
            first, last = sorted((self.anchor, here))
            self.select_from(self.words, first)
            self.select_to(self.words, last - 1)
        self.follow()

    def focused(self, inside: bool) -> None:
        if inside:
            self.focus(self.words)  # the cursor shows (by a click, or by Tab)
        else:
            self.select_clear()
            self.focus("")
        if self.composition is not None:
            self.composition.active = inside
            if not inside:
                self.composition.show("")
            if self.watching is not None:
                self.winfo_toplevel().after_cancel(self.watching)
                self.watching = None
            if inside:
                self.watch()
        self.follow()
        self.on_focus(inside)

    def watch(self) -> None:
        """While the line has the keyboard, show what the input method composes as it changes (it is read
        in the window procedure, which may not touch Tk)."""
        self.watching = None
        if self.composition is None or not self.composition.active:
            return
        if self.composition.version != self.seen:
            self.seen = self.composition.version
            self.follow()
        self.watching = self.winfo_toplevel().after(30, self.watch)

    def follow(self) -> None:
        """Keep the cursor in the line (the words slide left as they run past its end, and back), show what
        an input method is composing there, and tell the input method where the cursor and the line are."""
        width = self.winfo_width() if self.winfo_width() > 1 else int(self.cget("width"))
        text, at = self.get(), self.place_of("insert")
        composing = self.composition.text if self.composition is not None and self.composition.active else ""
        shown = text[:at] + composing + text[at:]
        cursor = self.theme.measure(self.font, shown[:at + len(composing)])
        whole = self.theme.measure(self.font, shown)
        room = max(width - self.theme.px(2), 1)
        shift = min(max(self.shift, cursor - room), cursor)
        self.shift = shift = max(0, min(shift, max(whole - room, 0)))
        middle = self.coords(self.words)[1]
        self.coords(self.words, -shift, middle)
        tk.Canvas.delete(self, "composing")
        if composing:  # the words as they will be, the part still being composed underlined in gold
            self.itemconfigure(self.words, state="hidden")
            self.create_text(-shift, middle, anchor="w", text=shown, font=self.font, fill=INK, tags="composing")
            start = -shift + self.theme.measure(self.font, text[:at])
            foot = middle + self.line_height / 2
            self.create_line(start, foot, start + self.theme.measure(self.font, composing), foot, fill=GOLD,
                             width=max(1, self.theme.px(1)), tags="composing")
        else:
            self.itemconfigure(self.words, state="normal")
        self.tell(cursor - shift)

    def tell(self, cursor: float) -> None:
        """Where the cursor and the line are in the window, for the input method to put its candidates."""
        if self.composition is None:
            return
        try:
            window = self.winfo_toplevel()
            left, top = self.winfo_rootx() - window.winfo_rootx(), self.winfo_rooty() - window.winfo_rooty()
            line = self.master
            line_left = line.winfo_rootx() - window.winfo_rootx()
        except tk.TclError:
            return
        height = self.winfo_height() if self.winfo_height() > 1 else int(self.cget("height"))
        self.composition.caret = (round(left + cursor), top, top + height)
        self.composition.area = (line_left, top, line_left + line.winfo_width(), top + height)


class TitleBar(tk.Canvas):
    """The window's title bar as the cover of a leather-bound book: the gilt lyre and name on dark
    leather, a gilt fillet along its foot, and — and × in gilt lines on the right.

    It tells ``frame`` (a ``chrome.Frame``) how tall it is and where its buttons are, so that a press
    on the rest of it moves the window and a press near the edges resizes it, as Windows' own bar
    would; only presses on the buttons reach it.  A dialog's bar has × alone (``minimise`` None)."""

    def __init__(self, parent: tk.Misc, theme: Theme, frame, title: str, note: str,
                 minimise: Callable[[], None] | None, close: Callable[[], None]):
        px = theme.px
        self.theme, self.frame, self.title, self.note = theme, frame, title, note
        self.commands = {"close": close, **({"minimise": minimise} if minimise is not None else {})}
        self.bar_height = px(34)
        super().__init__(parent, height=self.bar_height, bg=LEATHER_TOP, highlightthickness=0, borderwidth=0)
        size = self.button_size = px(24)
        half = size / 2

        def face(name: str, draw: Callable[[int], object], color: str, disc: str | None = None) -> tk.PhotoImage:
            return theme.image(f"title-{name}", size, size,
                               lambda: ([(disc, icons.circle(half, half, half - 0.5))] if disc else [])
                               + icons.paint(draw(size), color))  # type: ignore[arg-type]
        self.faces = {
            "minimise": {False: face("minimise", icons.minimise, GILT),
                         True: face("minimise-hover", icons.minimise, GILT_BRIGHT, LEATHER_HOVER)},
            "close": {False: face("close", icons.cross, GILT), True: face("close-hover", icons.cross, PAPER, WARN)},
        }
        self.crest = theme.icon("title-crest", px(17), GILT, icons.lyre)
        self.boxes: dict[str, tuple[float, float, float, float]] = {}
        self.inside: str | None = None  # the button under the pointer
        self.pressed: str | None = None
        self.watching: str | None = None
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<Motion>", lambda event: self.point(self.button_at(event.x, event.y)))
        self.bind("<Leave>", lambda _: self.point(None))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)

    def redraw(self) -> None:
        self.delete("all")
        px, width, height = self.theme.px, self.winfo_width(), self.bar_height
        top, foot = ([int(color[i:i + 2], 16) for i in (1, 3, 5)] for color in (LEATHER_TOP, LEATHER_FOOT))
        for y in range(height):  # the leather, lit from above
            t = y / max(1, height - 1)
            shade = tuple(round(a + (b - a) * t) for a, b in zip(top, foot))
            self.create_line(0, y, width, y, fill="#%02x%02x%02x" % shade)
        line = max(1, round(self.theme.scale))
        fillet = height - px(3)
        self.create_rectangle(0, fillet - line, width, fillet - 1, fill=GILT_DEEP, outline="")
        self.create_rectangle(0, height - line, width, height, fill=LEATHER_EDGE, outline="")
        middle = fillet / 2
        x = px(16)
        self.create_image(x + self.crest.width() / 2, middle, image=self.crest)
        x += self.crest.width() + px(7)
        title = self.create_text(x, middle, text=self.title, font=self.theme.fonts.shelf, fill=GILT_BRIGHT, anchor="w")
        if self.note:
            right = self.bbox(title)[2] if self.bbox(title) else x
            self.create_text(right + px(8), middle, text=self.note, font=self.theme.fonts.small, fill=EMBER, anchor="w")
        size = self.button_size
        close = width - px(8) - size / 2
        for name, centre in (("close", close), ("minimise", close - size - px(4)))[:len(self.commands)]:
            self.boxes[name] = (centre - size / 2, middle - size / 2, centre + size / 2, middle + size / 2)
            self.create_image(centre, middle, image=self.faces[name][self.inside == name], tags=name)
        dx, dy = self.winfo_x(), self.winfo_y()
        self.frame.caption = dy + height
        self.frame.buttons = [(round(l + dx), round(t + dy), round(r + dx), round(b + dy))
                              for l, t, r, b in self.boxes.values()]

    def button_at(self, x: float, y: float) -> str | None:
        return next((name for name, (l, t, r, b) in self.boxes.items() if l <= x < r and t <= y < b), None)

    def point(self, name: str | None) -> None:
        if name != self.inside:
            for lit in (self.inside, name):
                if lit:
                    self.itemconfigure(lit, image=self.faces[lit][lit == name])
            self.inside = name
        if name and not self.watching:
            self.watching = self.winfo_toplevel().after(80, self.watch)

    def watch(self) -> None:
        """Moving off a button onto the bar leaves Tk no event (Windows has that part), so look."""
        self.watching = None
        try:
            x, y = self.winfo_pointerx() - self.winfo_rootx(), self.winfo_pointery() - self.winfo_rooty()
        except tk.TclError:
            return
        self.point(self.button_at(x, y))

    def press(self, event: tk.Event) -> None:
        self.pressed = self.button_at(event.x, event.y)

    def release(self, event: tk.Event) -> None:
        pressed, self.pressed = self.pressed, None
        if pressed and self.button_at(event.x, event.y) == pressed:
            self.point(None)
            self.commands[pressed]()
