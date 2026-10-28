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
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from . import icons
from .arrange import ArrangeError, arrange_file
from .collate import pinyin_key
from .keyboard import Keyboard, foreground_title_and_pid
from .midi import MidiError
from .player import play
from .record import SILENCE, WAIT, record
from .score import Score, ScoreError, load_score
from .timeline import compile_timeline
from .transcribe import AUDIO_SUFFIXES, Tools, TranscribeError, find_tools, tool_folders, transcribe

PROJECT = Path(__file__).resolve().parent.parent
SCORES_DIR = PROJECT / "txt"
MIDI_DIR = PROJECT / "midi"
TOOL_DIRS = tool_folders(PROJECT)
MIDI_SUFFIXES = (".mid", ".midi")
SETTLE = 1.5  # seconds a MIDI or audio file must stay unchanged before it is used: it may still be copying
BAD_NAME_CHARACTERS = set('\\/:*?"<>|')
RECORDING_SUFFIX = "-录音"  # a recording of 飞鼠进行曲 is saved as 飞鼠进行曲-录音
NO_TOOLS = ("录音编配要用到 audio.cpp 和 MuScriptor 模型，LyreBot 没有自带。\n\n"
            "把它们放进 LyreBot 文件夹里的 tools 文件夹，或与 LyreBot 文件夹并排的 LyreBot-tools 文件夹。"
            "下载地址和放法见 README 的「从录音生成曲谱」。")
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


def midi_files(midi_dir: Path) -> list[Path]:
    if not midi_dir.is_dir():
        return []
    files = [path for path in midi_dir.iterdir() if path.suffix.lower() in MIDI_SUFFIXES and path.is_file()]
    return sorted(files, key=lambda path: pinyin_key(path.stem))


def pending_midi(midi_dir: Path, scores_dir: Path, failed: dict[Path, float], now: float | None = None) -> list[Path]:
    """MIDI files that have no score of the same name yet.

    A score that already exists is never replaced, so edits to it are safe.
    Files still being copied (changed in the last ``SETTLE`` seconds) and files
    that already failed as they are now wait.
    """
    now = time.time() if now is None else now
    found = []
    for path in midi_files(midi_dir):
        if (scores_dir / f"{path.stem}.txt").exists():
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if failed.get(path) != stamp and now - stamp >= SETTLE:
            found.append(path)
    return found


def audio_files(midi_dir: Path) -> list[Path]:
    if not midi_dir.is_dir():
        return []
    files = [path for path in midi_dir.iterdir() if path.suffix.lower() in AUDIO_SUFFIXES and path.is_file()]
    return sorted(files, key=lambda path: pinyin_key(path.stem))


def pending_audio(midi_dir: Path, scores_dir: Path, failed: dict[Path, float], now: float | None = None) -> list[Path]:
    """Recordings and other audio files in the midi folder with no MIDI file or score of the same name yet."""
    now = time.time() if now is None else now
    done = {path.stem.casefold() for path in midi_files(midi_dir)}
    found = []
    for path in audio_files(midi_dir):
        if path.stem.casefold() in done or (scores_dir / f"{path.stem}.txt").exists():
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if failed.get(path) != stamp and now - stamp >= SETTLE:
            found.append(path)
    return found


def recording_name(title: str) -> str:
    """The name a recording of ``title`` is saved under: 曲名-录音, or the
    time of day when no title is given."""
    title = title.strip()
    if not title:
        return "录音 " + time.strftime("%m%d-%H%M")  # Windows strftime cannot take Chinese in the format
    return title if title.endswith(RECORDING_SUFFIX) else title + RECORDING_SUFFIX


def name_problem(name: str, scores_dir: Path, midi_dir: Path) -> str | None:
    """Why a recording cannot be called ``name``, or None if it can."""
    if not name or name.endswith(".") or any(c in BAD_NAME_CHARACTERS or ord(c) < 32 for c in name):
        return '名字不能为空，也不能有 \\ / : * ? " < > | 这些字符，请换一个：'
    taken = (scores_dir / f"{name}.txt").exists() or (
        midi_dir.is_dir() and any(path.stem.casefold() == name.casefold() for path in midi_dir.iterdir()))
    return f"已经有叫「{name}」的曲谱或文件了，请换一个：" if taken else None


def midi_for(score: Path, midi_dir: Path) -> Path | None:
    """The MIDI file a score was arranged from, matched by name."""
    for path in midi_files(midi_dir):
        if path.stem.casefold() == score.stem.casefold():
            return path
    return None


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
    def __init__(self, root: tk.Tk, scores_dir: Path = SCORES_DIR, midi_dir: Path = MIDI_DIR,
                 tool_dirs: list[Path] | None = None):
        self.root = root
        self.scores_dir = scores_dir
        self.midi_dir = midi_dir
        self.tool_dirs = TOOL_DIRS if tool_dirs is None else tool_dirs
        self.failed_midi: dict[Path, float] = {}  # MIDI files that could not be arranged, by change time
        self.failed_audio: dict[Path, float] = {}  # audio files that could not be transcribed, likewise
        self.songs: list[Path] = []
        self.told_empty = False
        # A note such as "just arranged" kept in front of a song's details while it stays selected:
        # selecting a row also fires <<TreeviewSelect>> later, which redraws the details.
        self.notice: tuple[Path, str] | None = None
        self.cache: dict[Path, tuple[float, Score | ScoreError]] = {}
        self.worker: threading.Thread | None = None  # playing or recording
        self.recording = False
        self.transcriber: threading.Thread | None = None
        self.stop_transcribing = threading.Event()
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
        self.menu = tk.Menu(root, tearoff=False, bg="#FFFFFF", fg=INK, activebackground=HIGHLIGHT,
                            activeforeground=INK, disabledforeground=FAINT, borderwidth=0, font=(FAMILY, 10))
        self.menu.add_command(label="打开曲谱文件夹（txt）", command=lambda: self.open_folder(self.scores_dir))
        self.menu.add_command(label="打开 MIDI 和录音文件夹（midi）", command=lambda: self.open_folder(self.midi_dir))
        self.menu.add_separator()
        self.menu.add_command(label="从 MIDI 重新编配", command=self.rearrange)
        self.tree.bind("<Button-3>", self.context_menu)

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
        self.record_button = IconButton(controls, self.record_images, self.record)
        self.record_button.pack(side="right", padx=(0, px(6)))

        self.refresh()
        if self.songs:
            self.select(self.songs[0])
        root.after(30, self.poll)
        root.after(300, self.arrange_next)
        root.after(600, self.transcribe_next)
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
        dot = lambda color: (color, icons.circle(s, s, small * 0.17))  # noqa: E731
        self.record_images = {"normal": image(small, [dot(ORANGE)]),
                              "hover": image(small, [(HOVER, icons.circle(s, s, s - 0.5)), dot(ORANGE_DEEP)]),
                              "disabled": image(small, [dot(PEACH)])}
        square = lambda color: (color, icons.stop(small))  # noqa: E731
        self.recording_images = {"normal": image(small, [(HIGHLIGHT, icons.circle(s, s, s - 0.5)), square(ORANGE)]),
                                 "hover": image(small, [(PEACH, icons.circle(s, s, s - 0.5)), square(ORANGE_DEEP)]),
                                 "disabled": image(small, [square(FAINT)])}

    def watch(self) -> None:
        """Pick up scores, MIDI and audio files added, removed or edited while the window is open."""
        self.refresh()
        self.arrange_next()
        self.transcribe_next()
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
        songs = sorted(self.scores_dir.glob("*.txt"), key=lambda p: pinyin_key(p.stem)) if self.scores_dir.is_dir() else []
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
            self.told_empty = False
        if not songs and not self.told_empty:  # once, so it does not cover later messages
            self.told_empty = True
            self.title_label.configure(text="还没有曲谱")
            self.time_label.configure(text="")
            self.say("把 .txt 曲谱放进 txt 文件夹，或把 MIDI 放进 midi 文件夹（右键列表可以打开它们），也可以点 ● 录一段音乐")

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
        if self.notice and self.notice[0] != path:
            self.notice = None
        if isinstance(score, ScoreError):
            self.time_label.configure(text="")
            self.say(f"曲谱有误：{score}", warn=True)
            return
        self.time_label.configure(text=clock(score.duration_seconds / self.speed_factor()))
        tempo = f"{score.bpm:g} BPM" + (f" · 变速 {score.tempo_changes} 处" if score.tempo_changes else "")
        self.say(f"{self.notice[1]} · {tempo}" if self.notice else tempo)

    def open_folder(self, folder: Path) -> None:
        folder.mkdir(exist_ok=True)
        os.startfile(folder)  # type: ignore[attr-defined]

    def context_menu(self, event: tk.Event) -> None:
        if not self.worker:
            row = self.tree.identify_row(event.y)
            if row:
                self.tree.selection_set(row)
                self.tree.focus(row)
        chosen = self.selected()
        can = not self.worker and chosen is not None and midi_for(chosen, self.midi_dir) is not None
        self.menu.entryconfigure(3, state="normal" if can else "disabled")
        self.menu.tk_popup(event.x_root, event.y_root)

    # ---- MIDI ----
    def arrange_next(self) -> None:
        """Arrange one new MIDI file from the midi folder, then come back for the next."""
        if self.worker or self.closing:
            return
        pending = pending_midi(self.midi_dir, self.scores_dir, self.failed_midi)
        if not pending:
            return
        self.arrange_one(pending[0])
        if len(pending) > 1:
            self.root.after(50, self.arrange_next)

    def arrange_one(self, source: Path, done: str = "刚从 MIDI 编配好") -> None:
        target = self.scores_dir / f"{source.stem}.txt"
        self.say(f"正在编配 {source.name}…")
        self.root.update_idletasks()
        try:
            arrange_file(source, target)
        except (OSError, MidiError, ArrangeError) as error:
            try:
                self.failed_midi[source] = source.stat().st_mtime
            except OSError:
                pass
            self.say(f"{source.name} 无法编配：{error}", warn=True)
        else:
            self.refresh()
            self.notice = (target, done)
            self.select(target)

    def rearrange(self) -> None:
        """Arrange the selected score again from its MIDI file, replacing it."""
        score = self.selected()
        source = midi_for(score, self.midi_dir) if score and not self.worker else None
        if source is None:
            return
        question = f"用 midi 文件夹里的 {source.name} 重新生成「{score.stem}」？\n\n这份曲谱里手动改过的地方会被覆盖。"
        if not messagebox.askyesno("从 MIDI 重新编配", question, parent=self.root):
            return
        try:
            arrange_file(source, score)
        except (OSError, MidiError, ArrangeError) as error:
            messagebox.showerror("无法编配", f"{source.name}：{error}", parent=self.root)
            return
        self.failed_midi.pop(source, None)
        self.cache.pop(score, None)
        self.refresh()
        self.notice = (score, "已重新编配")
        self.select(score)

    # ---- recording and transcribing ----
    def record(self) -> None:
        """Record what the speakers play into the midi folder; it is then transcribed and arranged."""
        if self.recording:
            self.stop()
            return
        if self.worker or self.transcriber or time.monotonic() < self.quiet_until:
            return
        if find_tools(self.tool_dirs) is None:
            messagebox.showinfo("还不能录音", NO_TOOLS, parent=self.root)
            return
        name = self.ask_name()
        if name is None:
            return
        self.midi_dir.mkdir(exist_ok=True)
        self.cancel = threading.Event()
        self.recording = True
        self.set_playing(False, recording=True)
        self.title_label.configure(text=name)
        self.time_label.configure(text="")
        self.set_fraction(0.0)
        self.root.title(f"{name} · 录音 · LyreBot")
        self.say("等待声音：播放想编配的音乐，录完点 ■ 结束")
        self.worker = threading.Thread(target=self.run_recording, args=(self.midi_dir / f"{name}.wav",), daemon=True)
        self.worker.start()

    def ask_name(self) -> str | None:
        prompt = f"要录的是哪首曲子？曲谱会叫「曲名{RECORDING_SUFFIX}」（不填就按时间起名）："
        answer = ""
        while True:
            answer = simpledialog.askstring("录音", prompt, initialvalue=answer, parent=self.root)
            if answer is None:
                return None
            name = recording_name(answer)
            problem = name_problem(name, self.scores_dir, self.midi_dir)
            if problem is None:
                return name
            prompt = problem

    def run_recording(self, path: Path) -> None:
        post = self.updates.put
        partial = path.with_name(path.name + ".part")  # not an audio file yet, so nothing picks it up early
        try:
            seconds = record(partial, self.cancel, lambda state, value: post(("recording", (state, value))))
            if seconds > 0:
                os.replace(partial, path)
                post(("recorded", (path, seconds)))
            else:
                partial.unlink(missing_ok=True)
                post(("status", "已取消录音" if self.cancel.is_set() else f"等了 {WAIT / 60:g} 分钟也没有声音，没有录音"))
        except Exception as error:  # report anything to the user rather than dying silently
            partial.unlink(missing_ok=True)
            post(("record failed", str(error)))
        finally:
            post(("done", None))

    def transcribe_next(self) -> None:
        """Transcribe one new audio file from the midi folder in the background."""
        if self.worker or self.transcriber or self.closing:
            return
        pending = pending_audio(self.midi_dir, self.scores_dir, self.failed_audio)
        if pending:
            self.start_transcription(pending[0])

    def start_transcription(self, source: Path) -> None:
        tools = find_tools(self.tool_dirs)
        if tools is None:
            self.mark_failed(source)
            self.say(f"{source.name} 无法转录：没有找到转录工具，见 README「从录音生成曲谱」", warn=True)
            return
        self.say(f"正在把 {source.name} 转成 MIDI…")
        self.stop_transcribing = threading.Event()
        self.transcriber = threading.Thread(target=self.run_transcription,
                                            args=(source, tools, self.stop_transcribing), daemon=True)
        self.transcriber.start()
        if not self.recording:
            self.record_button.set(enabled=False)

    def run_transcription(self, source: Path, tools: Tools, cancel: threading.Event) -> None:
        target = source.with_suffix(".mid")
        error = None
        try:
            transcribe(source, target, tools, cancel)
        except TranscribeError as failure:
            error = str(failure)
        except Exception as failure:  # report anything to the user rather than dying silently
            error = f"出错：{failure}"
        self.updates.put(("transcribed", (source, target, error)))

    def mark_failed(self, source: Path) -> None:
        try:
            self.failed_audio[source] = source.stat().st_mtime
        except OSError:
            pass

    def transcribed(self, source: Path, target: Path, error: str | None) -> None:
        self.transcriber = None
        if not self.worker:
            self.record_button.set(enabled=True)
        if self.closing:
            return
        if error:
            self.mark_failed(source)
            self.say(f"{source.name} 无法转录：{error}", warn=True)
        elif not self.worker:  # while a song plays, the watcher arranges it afterwards
            self.arrange_one(target, "刚从录音编配好")

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

    def set_playing(self, playing: bool, recording: bool = False) -> None:
        busy = playing or recording
        self.button.set(self.stop_images if playing else self.play_images, enabled=not busy)
        self.next_button.set(enabled=not busy)
        self.speed_choice.set_enabled(not busy)
        self.mode_choice.set_enabled(not busy)
        self.bar.configure(cursor="arrow" if busy else "hand2")
        # Unlike ■ while playing, ■ while recording is live: LyreBot keeps recording when clicked.
        self.record_button.set(self.recording_images if recording else self.record_images,
                               enabled=recording or (not busy and self.transcriber is None))
        if not busy:
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
            elif kind == "recording":
                state, seconds = value
                if state == "recording":
                    self.time_label.configure(text=clock(seconds))
                    self.say(f"正在录音 · 点 ■ 结束，安静 {SILENCE:g} 秒也会自动结束")
            elif kind == "recorded":
                path, seconds = value
                self.say(f"录好了 {clock(seconds)}")
                if not self.closing:  # otherwise the recording is transcribed next time
                    self.start_transcription(path)
            elif kind == "record failed":
                self.say(f"录音失败：{value}", warn=True)
            elif kind == "transcribed":
                self.transcribed(*value)
            elif kind == "done":
                self.worker = None
                self.recording = False
                self.quiet_until = time.monotonic() + COOLDOWN
                self.set_playing(False)
        if self.closing and not self.worker and not self.transcriber:
            self.root.destroy()
            return
        self.root.after(30, self.poll)

    def close(self) -> None:
        self.closing = True
        self.stop()
        self.stop_transcribing.set()
        if not self.worker and not self.transcriber:
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
