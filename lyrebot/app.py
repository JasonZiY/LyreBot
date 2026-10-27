"""A small window: pick a song, switch to the game, it plays."""

from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from . import icons
from .collate import pinyin_key
from .keyboard import Keyboard, foreground_title_and_pid
from .player import play
from .score import Score, ScoreError, load_score
from .timeline import compile_timeline

PROJECT = Path(__file__).resolve().parent.parent
MUSIC_DIR = PROJECT / "music"
ICON = PROJECT / "assets" / "lyrebot.ico"
COUNTDOWN = 2.0
FAMILY = "Microsoft YaHei UI"

# Claude's palette: warm paper, ink, and one orange accent.
PAPER = "#FAF9F5"
INK = "#3D3929"
MUTED = "#8C8578"
FAINT = "#C9C2B6"
LINE = "#E8E2D7"
TRACK = "#ECE6DC"
ORANGE = "#D97757"
ORANGE_DEEP = "#C15F3C"
PEACH = "#F0D3C4"
HIGHLIGHT = "#F5E4DA"
HOVER = "#EFE9DF"
WARN = "#B5502F"

SPEEDS = ("80%", "100%", "120%", "150%", "200%")
SINGLE, SHUFFLE, IN_ORDER = MODES = ("单曲", "随机演奏", "顺序演奏")
GAP = 1.0  # seconds of silence between songs when playing on
COOLDOWN = 0.5  # seconds after a stop during which clicks cannot start a song again


def clock(seconds: float) -> str:
    minutes, seconds = divmod(max(0, round(seconds)), 60)
    return f"{minutes}:{seconds:02d}"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin() -> bool:
    try:
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "-m lyrebot", str(PROJECT), 1)
    except (AttributeError, OSError):
        return False
    return int(result) > 32


def next_song(songs: list[Path], current: Path | None, mode: str) -> Path | None:
    if not songs:
        return None
    if mode == SHUFFLE:
        others = [song for song in songs if song != current]
        return random.choice(others or songs)
    index = songs.index(current) if current in songs else -1
    return songs[(index + 1) % len(songs)]


class IconButton(tk.Label):
    """A flat image button: fires on release inside it, and does nothing while disabled."""

    def __init__(self, parent: tk.Widget, images: dict[str, tk.PhotoImage], command: Callable[[], None]):
        super().__init__(parent, image=images["normal"], bg=PAPER, borderwidth=0, cursor="hand2")
        self.images, self.command = images, command
        self.enabled, self.inside, self.pressed = True, False, False
        self.bind("<Enter>", lambda _: self.hover(True))
        self.bind("<Leave>", lambda _: self.hover(False))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)

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


class Choice(tk.Label):
    """Flat text like "100% ▾" that opens a small menu of options."""

    def __init__(self, parent: tk.Widget, variable: tk.StringVar, options: tuple[str, ...],
                 on_change: Callable[[], None], font: tuple):
        super().__init__(parent, bg=PAPER, fg=INK, font=font, cursor="hand2", padx=8, pady=3)
        self.variable, self.enabled = variable, True
        self.menu = tk.Menu(self, tearoff=False, bg="#FFFFFF", fg=INK, activebackground=HIGHLIGHT,
                            activeforeground=INK, borderwidth=0, font=font)
        for option in options:
            self.menu.add_radiobutton(label=option, value=option, variable=variable,
                                      selectcolor=ORANGE, command=on_change)
        variable.trace_add("write", lambda *_: self.redraw())
        self.bind("<Enter>", lambda _: self.enabled and self.configure(bg=HOVER))
        self.bind("<Leave>", lambda _: self.configure(bg=PAPER))
        self.bind("<Button-1>", self.open)
        self.redraw()

    def redraw(self) -> None:
        self.configure(text=f"{self.variable.get()}  ▾", fg=INK if self.enabled else FAINT,
                       cursor="hand2" if self.enabled else "arrow")

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.configure(bg=PAPER)
        self.redraw()

    def open(self, _: tk.Event) -> None:
        if self.enabled:
            self.menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())


class App:
    def __init__(self, root: tk.Tk, music_dir: Path = MUSIC_DIR):
        self.root = root
        self.music_dir = music_dir
        self.songs: list[Path] = []
        self.cache: dict[Path, tuple[float, Score | ScoreError]] = {}
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()
        self.updates: queue.Queue = queue.Queue()
        self.duration = 0.0
        self.fraction = 0.0
        self.closing = False
        self.quiet_until = 0.0
        self.base_title = "LyreBot" if is_admin() else "LyreBot（未以管理员身份运行）"

        scale = self.scale = root.winfo_fpixels("1i") / 96
        px = lambda n: max(1, round(n * scale))  # noqa: E731
        root.title(self.base_title)
        root.geometry(f"{px(340)}x{px(500)}")
        root.minsize(px(300), px(360))
        root.configure(bg=PAPER)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F8>", lambda _: self.stop())
        self.build_images(px(40), px(34))

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Songs.Treeview", background=PAPER, fieldbackground=PAPER, foreground=INK,
                        font=(FAMILY, 11), rowheight=px(32), borderwidth=0, relief="flat", indent=0)
        style.layout("Songs.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.map("Songs.Treeview", background=[("selected", HIGHLIGHT)], foreground=[("selected", INK)])
        style.configure("Thin.Vertical.TScrollbar", background=LINE, troughcolor=PAPER, bordercolor=PAPER,
                        lightcolor=LINE, darkcolor=LINE, gripcount=0, arrowsize=px(6))
        style.map("Thin.Vertical.TScrollbar", background=[("active", FAINT)])
        style.layout("Thin.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])

        # ---- song list ----
        top = tk.Frame(root, bg=PAPER)
        top.pack(fill="both", expand=True, padx=px(10), pady=(px(10), 0))
        self.tree = ttk.Treeview(top, show="tree", selectmode="browse", style="Songs.Treeview")
        self.tree.column("#0", stretch=True)
        self.tree.tag_configure("bad", foreground=WARN)
        self.tree.pack(side="left", fill="both", expand=True)
        self.scrollbar = ttk.Scrollbar(top, command=self.tree.yview, style="Thin.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=self.scrolled)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_selected())
        self.tree.bind("<Double-Button-1>", lambda _: self.locked() or self.start())
        self.tree.bind("<Return>", lambda _: self.locked() or self.start())
        for sequence in ("<Button-1>", "<KeyPress-Up>", "<KeyPress-Down>", "<KeyPress-Home>", "<KeyPress-End>",
                         "<KeyPress-Prior>", "<KeyPress-Next>"):
            self.tree.bind(sequence, self.locked, add=True)
        menu = tk.Menu(root, tearoff=False, bg="#FFFFFF", fg=INK, activebackground=HIGHLIGHT,
                       activeforeground=INK, borderwidth=0, font=(FAMILY, 10))
        menu.add_command(label="打开曲谱文件夹", command=self.open_folder)
        self.tree.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))

        tk.Frame(root, bg=LINE, height=1).pack(fill="x", padx=px(14), pady=(px(6), 0))

        # ---- now playing ----
        panel = tk.Frame(root, bg=PAPER)
        panel.pack(fill="x", padx=px(18), pady=(px(12), px(14)))
        heading = tk.Frame(panel, bg=PAPER)
        heading.pack(fill="x")
        self.time_label = tk.Label(heading, bg=PAPER, fg=MUTED, font=(FAMILY, 9))
        self.time_label.pack(side="right", anchor="s")
        self.title_label = tk.Label(heading, bg=PAPER, fg=INK, font=(FAMILY, 13, "bold"), anchor="w")
        self.title_label.pack(side="left", fill="x", expand=True)

        self.bar_height = px(14)  # the clickable band; the drawn track is thinner
        self.bar = tk.Canvas(panel, height=self.bar_height, bg=PAPER, highlightthickness=0, cursor="hand2")
        self.bar.pack(fill="x", pady=(px(6), 0))
        self.bar.bind("<Configure>", lambda _: self.draw_bar())
        self.bar.bind("<Button-1>", self.seek)

        self.status_label = tk.Label(panel, bg=PAPER, fg=MUTED, font=(FAMILY, 9), anchor="w", justify="left")
        self.status_label.pack(fill="x", pady=(px(2), 0))
        panel.bind("<Configure>", lambda event: self.status_label.configure(wraplength=event.width))

        controls = tk.Frame(panel, bg=PAPER)
        controls.pack(fill="x", pady=(px(10), 0))
        self.speed = tk.StringVar(value="100%")
        self.mode = tk.StringVar(value=SINGLE)
        self.speed_choice = Choice(controls, self.speed, SPEEDS, self.show_selected, (FAMILY, 10))
        self.speed_choice.pack(side="left")
        self.mode_choice = Choice(controls, self.mode, MODES, lambda: None, (FAMILY, 10))
        self.mode_choice.pack(side="left", padx=(px(4), 0))
        # While a song plays these stay disabled: clicking LyreBot already stops the song
        # (the game loses focus), and a live button would start it again on release.
        self.next_button = IconButton(controls, self.next_images, self.play_next)
        self.next_button.pack(side="right")
        self.button = IconButton(controls, self.play_images, self.start)
        self.button.pack(side="right", padx=(0, px(6)))

        self.refresh()
        if self.songs:
            self.select(self.songs[0])
        root.after(30, self.poll)
        root.after(2000, self.watch)

    def build_images(self, big: int, small: int) -> None:
        def image(size: int, layers) -> tk.PhotoImage:
            picture = tk.PhotoImage(master=self.root, width=size, height=size)
            picture.put(" ".join(icons.render(size, PAPER, layers)))
            return picture

        c = big / 2
        disc = lambda color: (color, icons.circle(c, c, c - 0.5))  # noqa: E731
        self.play_images = {"normal": image(big, [disc(ORANGE), ("#FFFFFF", icons.play(big))]),
                            "hover": image(big, [disc(ORANGE_DEEP), ("#FFFFFF", icons.play(big))]),
                            "disabled": image(big, [disc(PEACH), ("#FFFFFF", icons.play(big))])}
        stop = image(big, [disc(PEACH), ("#FFFFFF", icons.stop(big))])
        self.stop_images = {"normal": stop, "hover": stop, "disabled": stop}
        s = small / 2
        mark = lambda color: [(color, shape) for shape in icons.next_track(small)]  # noqa: E731
        self.next_images = {"normal": image(small, mark(INK)),
                            "hover": image(small, [(HOVER, icons.circle(s, s, s - 0.5))] + mark(INK)),
                            "disabled": image(small, mark(FAINT))}

    def watch(self) -> None:
        """Pick up scores added, removed or edited while the window is open."""
        self.refresh()
        self.root.after(2000, self.watch)

    # ---- small view helpers ----
    def scrolled(self, first: str, last: str) -> None:
        if float(first) <= 0 and float(last) >= 1:
            self.scrollbar.pack_forget()
        else:
            self.scrollbar.pack(side="right", fill="y")
        self.scrollbar.set(first, last)

    def locked(self, _: tk.Event | None = None) -> str | None:
        return "break" if self.worker else None

    def say(self, text: str, warn: bool = False) -> None:
        self.status_label.configure(text=text, fg=WARN if warn else MUTED)

    def draw_bar(self) -> None:
        bar, width = self.bar, self.bar.winfo_width()
        thick = max(2, round(4 * self.scale))
        top = (self.bar_height - thick) // 2
        bar.delete("all")
        bar.create_rectangle(0, top, width, top + thick, fill=TRACK, width=0)
        filled = round(width * min(max(self.fraction, 0.0), 1.0))
        if filled > 0:
            bar.create_rectangle(0, top, filled, top + thick, fill=ORANGE, width=0)

    def set_fraction(self, fraction: float) -> None:
        self.fraction = fraction
        self.draw_bar()

    # ---- songs ----
    def read(self, path: Path) -> Score | ScoreError:
        try:
            stamp = path.stat().st_mtime
        except OSError as error:
            return ScoreError(str(error))
        cached = self.cache.get(path)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            result: Score | ScoreError = load_score(path)
        except (ScoreError, OSError) as error:
            result = error if isinstance(error, ScoreError) else ScoreError(str(error))
        self.cache[path] = (stamp, result)
        return result

    def refresh(self) -> None:
        if self.worker:
            return
        chosen = self.selected()
        songs = sorted(self.music_dir.glob("*.txt"), key=lambda p: pinyin_key(p.stem)) if self.music_dir.is_dir() else []
        bad = [isinstance(self.read(p), ScoreError) for p in songs]
        rows = [("  " + ("⚠ " if wrong else "") + p.stem, wrong) for p, wrong in zip(songs, bad)]
        current = [(self.tree.item(iid, "text"), "bad" in self.tree.item(iid, "tags"))
                   for iid in self.tree.get_children()]
        if songs != self.songs or current != rows:
            self.songs = songs
            self.tree.delete(*self.tree.get_children())
            for index, (text, wrong) in enumerate(rows):
                self.tree.insert("", "end", iid=str(index), text=text, tags=("bad",) if wrong else ())
            if chosen in songs:
                self.select(chosen)
            self.show_selected()
        if not songs:
            self.title_label.configure(text="还没有曲谱")
            self.time_label.configure(text="")
            self.say("把 .txt 曲谱放进 music 文件夹（右键列表可以打开它）")

    def selected(self) -> Path | None:
        chosen = self.tree.selection()
        index = int(chosen[0]) if chosen else -1
        return self.songs[index] if 0 <= index < len(self.songs) else None

    def select(self, path: Path) -> None:
        if path not in self.songs:
            return
        iid = str(self.songs.index(path))
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self.show_selected()

    def speed_factor(self) -> float:
        return int(self.speed.get().rstrip("%")) / 100

    def show_selected(self) -> None:
        if self.worker:
            return
        path = self.selected()
        if path is None:
            return
        score = self.read(path)
        self.title_label.configure(text=path.stem)
        self.set_fraction(0.0)
        if isinstance(score, ScoreError):
            self.time_label.configure(text="")
            self.say(f"曲谱有误：{score}", warn=True)
            return
        self.time_label.configure(text=clock(score.duration_seconds / self.speed_factor()))
        tempo = f"{score.bpm:g} BPM" + (f" · 变速 {score.tempo_changes} 处" if score.tempo_changes else "")
        self.say(tempo)

    def open_folder(self) -> None:
        self.music_dir.mkdir(exist_ok=True)
        os.startfile(self.music_dir)  # type: ignore[attr-defined]

    # ---- playing ----
    def start(self, path: Path | None = None, fraction: float = 0.0) -> None:
        # The click that pulls focus back to LyreBot stops the song; it must not start one too.
        if self.worker or time.monotonic() < self.quiet_until:
            return
        path = path or self.selected()
        if path is None:
            return
        self.cache.pop(path, None)  # always play the file as it is now
        score = self.read(path)
        if isinstance(score, ScoreError):
            messagebox.showerror("曲谱有误", f"{path.stem}：{score}", parent=self.root)
            self.refresh()
            return
        speed = self.speed_factor()
        timeline = compile_timeline(score, speed)
        self.select(path)
        self.duration = timeline.duration
        self.set_fraction(fraction)
        self.cancel = threading.Event()
        self.set_playing(True)
        args = (path, timeline, fraction * timeline.duration, speed, self.mode.get(), list(self.songs))
        self.worker = threading.Thread(target=self.run, args=args, daemon=True)
        self.worker.start()

    def play_next(self) -> None:
        if self.worker:
            return
        self.start(next_song(self.songs, self.selected(), self.mode.get()))

    def seek(self, event: tk.Event) -> None:
        if self.worker:
            return
        width = self.bar.winfo_width()
        self.start(fraction=min(max(event.x / width, 0.0), 0.999) if width > 0 else 0.0)

    def set_playing(self, playing: bool) -> None:
        self.button.set(self.stop_images if playing else self.play_images, enabled=not playing)
        self.next_button.set(enabled=not playing)
        self.speed_choice.set_enabled(not playing)
        self.mode_choice.set_enabled(not playing)
        self.bar.configure(cursor="arrow" if playing else "hand2")
        if not playing:
            self.root.title(self.base_title)

    def run(self, path: Path, timeline, begin: float, speed: float, mode: str, songs: list[Path]) -> None:
        post = self.updates.put
        try:
            keyboard = Keyboard()
            end = time.perf_counter() + COUNTDOWN
            while (left := end - time.perf_counter()) > 0:
                if self.cancel.is_set() or keyboard.stop_pressed():
                    post(("status", "已取消"))
                    return
                post(("status", f"{math.ceil(left)} 秒后开始，请切换到游戏窗口"))
                self.cancel.wait(min(0.05, left))
            target = keyboard.foreground()
            title, pid = foreground_title_and_pid(target)
            if not target or pid == os.getpid():
                post(("status", "倒计时结束时前台还是 LyreBot，没有演奏"))
                return
            while True:
                post(("song", (path, timeline.duration)))
                reason = play(timeline, keyboard, target, self.cancel,
                              lambda t: post(("progress", t)), start=begin)
                if reason != "done" or mode == SINGLE:
                    post(("status", {"done": "演奏完成", "stopped": "已停止", "focus": "切换了窗口，已停止"}[reason]))
                    return
                for _ in songs:  # skip scores with mistakes, but give up after one lap
                    path = next_song(songs, path, mode)
                    try:
                        timeline, begin = compile_timeline(load_score(path), speed), 0.0
                        break
                    except (ScoreError, OSError):
                        continue
                else:
                    post(("status", "没有可以接着演奏的曲谱"))
                    return
                if self.cancel.wait(GAP):
                    post(("status", "已停止"))
                    return
        except Exception as error:  # report anything to the user rather than dying silently
            post(("error", str(error)))
        finally:
            post(("done", None))

    def stop(self) -> None:
        self.cancel.set()

    def poll(self) -> None:
        while True:
            try:
                kind, value = self.updates.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.say(value)
            elif kind == "song":
                path, self.duration = value
                self.select(path)
                self.title_label.configure(text=path.stem)
                self.time_label.configure(text=f"0:00 / {clock(self.duration)}")
                self.root.title(f"{path.stem} · LyreBot")
            elif kind == "progress":
                self.set_fraction(value / self.duration if self.duration else 0)
                self.time_label.configure(text=f"{clock(value)} / {clock(self.duration)}")
                self.say("正在演奏 · 按 F8 或切换窗口停止")
            elif kind == "error":
                self.say("出错，已停止", warn=True)
                if not self.closing:
                    messagebox.showerror("演奏出错", value, parent=self.root)
            elif kind == "done":
                self.worker = None
                self.quiet_until = time.monotonic() + COOLDOWN
                self.set_playing(False)
        if self.closing and not self.worker:
            self.root.destroy()
            return
        self.root.after(30, self.poll)

    def close(self) -> None:
        self.closing = True
        self.stop()
        if not self.worker:
            self.root.destroy()


def main() -> None:
    if sys.platform == "win32" and not is_admin() and relaunch_as_admin():
        return  # the elevated copy takes over
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:  # own taskbar entry, so Windows doesn't borrow another pythonw app's icon
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LyreBot")
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    try:
        root.iconbitmap(default=str(ICON))
    except tk.TclError:
        pass
    try:
        App(root)
    except Exception:
        if sys.stderr is not None:
            raise
        import traceback  # windowless start: show the error instead of vanishing
        root.withdraw()
        messagebox.showerror("LyreBot 启动失败", traceback.format_exc(limit=3))
        return
    root.mainloop()
