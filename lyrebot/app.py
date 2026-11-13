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
from .arrange import ARRANGED, ArrangeError, arrange_file
from .collate import pinyin_key
from .keyboard import Keyboard, foreground_title_and_pid
from .listen import QUIET, listen
from .midi import MidiError
from .notate import notate
from .player import play
from .record import SILENCE, WAIT, record
from .score import Score, ScoreError, load_score, score_files
from .timeline import compile_timeline
from .transcribe import AUDIO_SUFFIXES, Tools, TranscribeError, find_tools, tool_folders, transcribe

PROJECT = Path(__file__).resolve().parent.parent
SCORES_DIR = PROJECT / "txt"
MIDI_DIR = PROJECT / "midi"
TOOL_DIRS = tool_folders(PROJECT)
MIDI_SUFFIXES = (".mid", ".midi")
SETTLE = 1.5  # seconds a MIDI or audio file must stay unchanged before it is used: it may still be copying
BAD_NAME_CHARACTERS = set('\\/:*?"<>|')
FOLDER = "folder:"  # the list's id for a folder row; songs have their index as id
RECORDING_SUFFIX = "-录音"  # a recording of 飞鼠进行曲 is saved as 飞鼠进行曲-录音
PLAYING_SUFFIX = "-演奏"  # and the keys played for it as 飞鼠进行曲-演奏
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


def score_names(scores_dir: Path) -> set[str]:
    """The names (casefolded, without .txt) of the scores in the txt folder and its folders."""
    return {path.stem.casefold() for path in score_files(scores_dir)}


def song_list(scores_dir: Path) -> list[tuple[str, list[Path]]]:
    """The scores as the list shows them: (folder name, its scores) for each
    folder inside the txt folder, in pinyin order, then ("", the scores
    directly in it).  Scores sort by pinyin within each."""
    groups: dict[str, list[Path]] = {}
    for path in score_files(scores_dir):
        folder = "" if path.parent == scores_dir else path.parent.name
        groups.setdefault(folder, []).append(path)
    if scores_dir.is_dir():  # an empty folder still shows, ready to drop scores into
        for folder in scores_dir.iterdir():
            if folder.is_dir():
                groups.setdefault(folder.name, [])
    order = sorted((name for name in groups if name), key=pinyin_key) + ([""] if "" in groups else [])
    return [(name, sorted(groups[name], key=lambda path: pinyin_key(path.stem))) for name in order]


def arranged_from(scores_dir: Path) -> dict[str, Path]:
    """Scores arranged from a MIDI file, by that file's name (casefolded).

    The arranger names its source on a score's first line, so a score keeps
    its MIDI file when it is renamed.
    """
    found: dict[str, Path] = {}
    if scores_dir.is_dir():
        for score in score_files(scores_dir):
            name = arranged_name(score)
            if name:
                found.setdefault(name, score)
    return found


def arranged_name(score: Path) -> str | None:
    """The name (casefolded) of the MIDI file a score's first line says it was arranged from."""
    try:
        with score.open(encoding="utf-8-sig", errors="replace") as text:
            first = text.readline(1024)
    except OSError:
        return None
    if not first.startswith(ARRANGED):
        return None
    return first[len(ARRANGED):].strip().casefold() or None


def pending_midi(midi_dir: Path, scores_dir: Path, failed: dict[Path, float], now: float | None = None) -> list[Path]:
    """MIDI files that have no score yet: none of the same name, and none
    arranged from them and renamed since.

    A score that already exists is never replaced, so edits to it are safe.
    Files still being copied (changed in the last ``SETTLE`` seconds) and files
    that already failed as they are now wait.
    """
    now = time.time() if now is None else now
    arranged = arranged_from(scores_dir)
    names = score_names(scores_dir)
    found = []
    for path in midi_files(midi_dir):
        if path.stem.casefold() in names or path.name.casefold() in arranged:
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
    """Recordings and other audio files in the midi folder with no MIDI file or score of the same name yet
    (nor a score arranged from their MIDI file and renamed since)."""
    now = time.time() if now is None else now
    done = {path.stem.casefold() for path in midi_files(midi_dir)}
    done |= {name.rsplit(".", 1)[0] for name in arranged_from(scores_dir)}
    done |= score_names(scores_dir)
    found = []
    for path in audio_files(midi_dir):
        if path.stem.casefold() in done:
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if failed.get(path) != stamp and now - stamp >= SETTLE:
            found.append(path)
    return found


def recording_name(title: str, suffix: str = RECORDING_SUFFIX) -> str:
    """The name a recording of ``title`` is saved under: 曲名-录音 (or with
    another ``suffix``), or the time of day when no title is given."""
    title = title.strip()
    if not title:
        # Windows strftime cannot take Chinese in the format.
        return suffix.lstrip("-") + " " + time.strftime("%m%d-%H%M")
    return title if title.endswith(suffix) else title + suffix


def name_problem(name: str, scores_dir: Path, midi_dir: Path) -> str | None:
    """Why a recording cannot be called ``name``, or None if it can."""
    if not name or name.endswith(".") or any(c in BAD_NAME_CHARACTERS or ord(c) < 32 for c in name):
        return '名字不能为空，也不能有 \\ / : * ? " < > | 这些字符，请换一个：'
    taken = name.casefold() in score_names(scores_dir) or (
        midi_dir.is_dir() and any(path.stem.casefold() == name.casefold() for path in midi_dir.iterdir()))
    return f"已经有叫「{name}」的曲谱或文件了，请换一个：" if taken else None


def midi_for(score: Path, midi_dir: Path) -> Path | None:
    """The MIDI file a score was arranged from: the one its first line names,
    or else the one of the same name."""
    files = midi_files(midi_dir)
    named = arranged_name(score)
    for path in files:
        if path.name.casefold() == named:
            return path
    for path in files:
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
        self.rows: list[tuple[str, str, str, tuple[str, ...]]] = []
        self.told_empty = False
        # A note such as "just arranged" kept in front of a song's details while it stays selected:
        # selecting a row also fires <<TreeviewSelect>> later, which redraws the details.
        self.notice: tuple[Path, str] | None = None
        self.cache: dict[Path, tuple[float, Score | ScoreError]] = {}
        self.worker: threading.Thread | None = None  # playing or recording
        self.recording = False
        self.listening = False
        self.written: tuple[Path, str] | None = None  # a score just written from the keys played
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
        # Folders show their own ▾ / ▸, so the rows draw no indicator of their own.
        style.layout("Songs.Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
            ("Treeitem.image", {"side": "left", "sticky": ""}), ("Treeitem.text", {"sticky": "nswe"})]})])
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
        self.tree.tag_configure("folder", foreground=MUTED, font=(FAMILY, 10, "bold"))
        self.tree.pack(side="left", fill="both", expand=True)
        self.scrollbar = ttk.Scrollbar(top, command=self.tree.yview, style="Thin.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=self.scrolled)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_selected())
        self.tree.bind("<Double-Button-1>", lambda event: self.locked() or self.folder_at(event) or self.start())
        self.tree.bind("<<TreeviewOpen>>", lambda _: self.mark_folders())
        self.tree.bind("<<TreeviewClose>>", lambda _: self.mark_folders())
        self.tree.bind("<Return>", lambda _: self.locked() or self.start())
        for sequence in ("<Button-1>", "<KeyPress-Up>", "<KeyPress-Down>", "<KeyPress-Home>", "<KeyPress-End>",
                         "<KeyPress-Prior>", "<KeyPress-Next>"):
            self.tree.bind(sequence, self.locked, add=True)
        self.tree.bind("<Button-1>", self.toggle_folder, add=True)
        self.closed: set[str] = set()  # folders the user closed
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
        self.keys_button = IconButton(controls, self.keys_images, self.listen)
        self.keys_button.pack(side="right", padx=(0, px(2)))

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
        def keys(color: str, back: str) -> list:
            body, *holes = icons.keyboard(small)
            return [(color, body)] + [(back, hole) for hole in holes]
        self.keys_images = {"normal": image(small, keys(INK, PAPER)),
                            "hover": image(small, [(HOVER, icons.circle(s, s, s - 0.5))] + keys(INK, HOVER)),
                            "disabled": image(small, keys(FAINT, PAPER))}
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
        groups = song_list(self.scores_dir)
        songs = [path for _, paths in groups for path in paths]
        rows = []  # (parent iid, iid, text, tags)
        for folder, paths in groups:
            parent = ""
            if folder:
                parent = FOLDER + folder
                rows.append(("", parent, folder, ("folder",)))
            for path in paths:
                wrong = isinstance(self.read(path), ScoreError)
                indent = "      " if folder else "  "
                rows.append((parent, str(songs.index(path)), indent + ("⚠ " if wrong else "") + path.stem,
                             ("bad",) if wrong else ()))
        if songs != self.songs or rows != self.rows:
            self.songs, self.rows = songs, rows
            self.tree.delete(*self.tree.get_children())
            for parent, iid, text, tags in rows:
                self.tree.insert(parent, "end", iid=iid, text=text, tags=tags,
                                 open=iid.startswith(FOLDER) and iid not in self.closed)
            self.mark_folders()
            if chosen in songs:
                self.select(chosen)
            self.show_selected()
            self.told_empty = False
        if not songs and not self.told_empty:  # once, so it does not cover later messages
            self.told_empty = True
            self.title_label.configure(text="还没有曲谱")
            self.time_label.configure(text="")
            self.say("把 .txt 曲谱放进 txt 文件夹，或把 MIDI 放进 midi 文件夹（右键列表可以打开它们），"
                     "也可以点 ● 录一段音乐，或点 ● 左边的键盘记下自己弹的琴")

    def selected(self) -> Path | None:
        chosen = self.tree.selection()
        index = int(chosen[0]) if chosen and chosen[0].isdigit() else -1
        return self.songs[index] if 0 <= index < len(self.songs) else None

    def mark_folders(self) -> None:
        """Put ▾ before an open folder and ▸ before a closed one, and remember which are closed."""
        for iid in self.tree.get_children():
            if iid.startswith(FOLDER):
                is_open = bool(self.tree.item(iid, "open"))
                (self.closed.discard if is_open else self.closed.add)(iid)
                self.tree.item(iid, text=("  ▾ " if is_open else "  ▸ ") + iid[len(FOLDER):])

    def folder_at(self, event: tk.Event) -> str | None:
        """"break" when the click is on a folder: double-clicking one plays nothing."""
        return "break" if self.tree.identify_row(event.y).startswith(FOLDER) else None

    def toggle_folder(self, event: tk.Event) -> str | None:
        """A click on a folder opens or closes it, without selecting it."""
        if self.worker:
            return "break"
        row = self.tree.identify_row(event.y)
        if not row.startswith(FOLDER):
            return None
        self.tree.item(row, open=not self.tree.item(row, "open"))
        self.mark_folders()
        return "break"

    def select(self, path: Path) -> None:
        if path not in self.songs:
            return
        iid = str(self.songs.index(path))
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)  # opens its folder
        self.mark_folders()
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

    def ask_name(self, title: str = "录音", suffix: str = RECORDING_SUFFIX, verb: str = "录") -> str | None:
        prompt = f"要{verb}的是哪首曲子？曲谱会叫「曲名{suffix}」（不填就按时间起名）："
        answer = ""
        while True:
            answer = simpledialog.askstring(title, prompt, initialvalue=answer, parent=self.root)
            if answer is None:
                return None
            name = recording_name(answer, suffix)
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

    # ---- writing down the keys played ----
    def listen(self) -> None:
        """Listen to the lyre keys pressed in the game and write them down as a score."""
        if self.listening:
            self.stop()
            return
        if self.worker or time.monotonic() < self.quiet_until:
            return
        name = self.ask_name("记录演奏", PLAYING_SUFFIX, "弹")
        if name is None:
            return
        self.cancel = threading.Event()
        self.listening = True
        self.set_playing(False, listening=True)
        self.title_label.configure(text=name)
        self.time_label.configure(text="")
        self.set_fraction(0.0)
        self.root.title(f"{name} · 记录演奏 · LyreBot")
        self.say("切换到游戏弹琴，弹完按 F8 或回来点 ■ 结束")
        self.worker = threading.Thread(target=self.run_listening, args=(self.scores_dir / f"{name}.txt",),
                                       daemon=True)
        self.worker.start()

    def run_listening(self, target: Path) -> None:
        post = self.updates.put
        try:
            presses = listen(self.cancel, lambda state, seconds, count: post(("listening", (state, seconds, count))))
            if not presses:
                post(("status", "已取消" if self.cancel.is_set() else "一直没有弹琴，没有记下什么"))
                return
            post(("status", f"记下了 {len(presses)} 个音，正在写成曲谱…"))
            notation = notate(presses, target.stem)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(notation.text, encoding="utf-8", newline="\n")
            post(("notated", (target, notation.faithful)))
        except Exception as error:  # report anything to the user rather than dying silently
            post(("listen failed", str(error)))
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

    def set_playing(self, playing: bool, recording: bool = False, listening: bool = False) -> None:
        busy = playing or recording or listening
        self.button.set(self.stop_images if playing else self.play_images, enabled=not busy)
        self.next_button.set(enabled=not busy)
        self.speed_choice.set_enabled(not busy)
        self.mode_choice.set_enabled(not busy)
        self.bar.configure(cursor="arrow" if busy else "hand2")
        # Unlike ■ while playing, ■ while recording is live: LyreBot keeps recording when clicked.
        self.record_button.set(self.recording_images if recording else self.record_images,
                               enabled=recording or (not busy and self.transcriber is None))
        self.keys_button.set(self.recording_images if listening else self.keys_images, enabled=listening or not busy)
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

    def notated(self, path: Path, faithful: float) -> None:
        self.say(f"已写成曲谱 {path.name}")
        self.written = (path, f"刚从演奏记录好，{faithful:.0%} 的按键照原样弹回")

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
            elif kind == "listening":
                state, seconds, count = value
                if state == "listening":
                    self.time_label.configure(text=clock(seconds))
                    self.say(f"已记下 {count} 个音 · 按 F8 结束，{QUIET:g} 秒不弹也会自动结束")
            elif kind == "notated":
                self.notated(*value)
            elif kind == "listen failed":
                self.say(f"记录失败：{value}", warn=True)
            elif kind == "transcribed":
                self.transcribed(*value)
            elif kind == "done":
                self.worker = None
                self.recording = False
                self.listening = False
                self.quiet_until = time.monotonic() + COOLDOWN
                self.set_playing(False)
                if self.written:  # select the new score once the list may refresh again
                    path, note = self.written
                    self.written = None
                    self.refresh()
                    self.notice = (path, note)
                    self.select(path)
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
