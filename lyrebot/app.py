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

from .keyboard import Keyboard, foreground_title_and_pid
from .player import play
from .score import Score, ScoreError, load_score
from .timeline import compile_timeline

PROJECT = Path(__file__).resolve().parent.parent
MUSIC_DIR = PROJECT / "music"
ICON = PROJECT / "assets" / "lyrebot.ico"
COUNTDOWN = 2.0
FONT = ("Microsoft YaHei UI", 10)


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


SPEEDS = ("80%", "100%", "120%", "150%", "200%")
SINGLE, SHUFFLE, IN_ORDER = MODES = ("单曲", "随机演奏", "顺序演奏")
PLAY, STOP, NEXT = "▶", "■", "⏭"
GAP = 1.0  # seconds of silence between songs when playing on
COOLDOWN = 0.5  # seconds after a stop during which clicks cannot start a song again


def next_song(songs: list[Path], current: Path | None, mode: str) -> Path | None:
    if not songs:
        return None
    if mode == SHUFFLE:
        others = [song for song in songs if song != current]
        return random.choice(others or songs)
    index = songs.index(current) if current in songs else -1
    return songs[(index + 1) % len(songs)]


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
        self.closing = False
        self.quiet_until = 0.0

        root.title("LyreBot" if is_admin() else "LyreBot（未以管理员身份运行）")
        scale = root.winfo_fpixels("1i") / 96
        root.geometry(f"{int(320 * scale)}x{int(420 * scale)}")
        root.minsize(int(300 * scale), int(260 * scale))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F8>", lambda _: self.stop())
        ttk.Style(root).configure("Icon.TButton", font=("Segoe UI Symbol", 11))

        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(frame, font=(FONT[0], 11), activestyle="none", exportselection=False,
                                  borderwidth=1, highlightthickness=0)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(frame, command=self.listbox.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=bar.set)
        self.listbox.bind("<<ListboxSelect>>", lambda _: self.show_selected())
        self.listbox.bind("<Double-Button-1>", lambda _: self.start())
        self.listbox.bind("<Return>", lambda _: self.start())
        menu = tk.Menu(root, tearoff=False)
        menu.add_command(label="打开曲谱文件夹", command=self.open_folder)
        self.listbox.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))

        row = ttk.Frame(frame)
        row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(row, text="速度", font=FONT).pack(side="left")
        self.speed = tk.StringVar(value="100%")
        self.speed_box = ttk.Combobox(row, values=SPEEDS, textvariable=self.speed, state="readonly",
                                      width=5, font=FONT)
        self.speed_box.pack(side="left", padx=(4, 8))
        self.speed_box.bind("<<ComboboxSelected>>", lambda _: self.show_selected())
        self.mode = tk.StringVar(value=SINGLE)
        self.mode_box = ttk.Combobox(row, values=MODES, textvariable=self.mode, state="readonly",
                                     width=8, font=FONT)
        self.mode_box.pack(side="left")
        # While a song plays these stay disabled: clicking LyreBot already stops the song
        # (the game loses focus), and a live button would start it again on release.
        self.next_button = ttk.Button(row, text=NEXT, width=3, style="Icon.TButton", command=self.play_next)
        self.next_button.pack(side="right")
        self.button = ttk.Button(row, text=PLAY, width=3, style="Icon.TButton", command=self.start)
        self.button.pack(side="right", padx=(0, 4))

        self.progress = ttk.Progressbar(frame, maximum=1.0, cursor="hand2")
        self.progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.progress.bind("<Button-1>", self.seek)
        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, font=FONT, foreground="#555").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.refresh()
        if self.songs:
            self.select(self.songs[0])
        root.after(30, self.poll)
        root.after(2000, self.watch)

    def watch(self) -> None:
        """Pick up scores added, removed or edited while the window is open."""
        self.refresh()
        self.root.after(2000, self.watch)

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
        songs = sorted(self.music_dir.glob("*.txt"), key=lambda p: p.stem) if self.music_dir.is_dir() else []
        labels = [("⚠ " if isinstance(self.read(p), ScoreError) else "") + p.stem for p in songs]
        if songs != self.songs or list(self.listbox.get(0, "end")) != labels:
            self.songs = songs
            self.listbox.delete(0, "end")
            for label in labels:
                self.listbox.insert("end", label)
            if chosen in songs:
                self.select(chosen)
            self.show_selected()
        if not songs:
            self.status.set("music 文件夹里还没有 .txt 曲谱（右键列表可打开文件夹）")

    def selected(self) -> Path | None:
        chosen = self.listbox.curselection()
        return self.songs[chosen[0]] if chosen and chosen[0] < len(self.songs) else None

    def select(self, path: Path) -> None:
        if path not in self.songs:
            return
        index = self.songs.index(path)
        state = self.listbox.cget("state")
        self.listbox.configure(state="normal")
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.see(index)
        self.listbox.configure(state=state)
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
        self.progress["value"] = 0
        if isinstance(score, ScoreError):
            self.status.set(f"曲谱有误：{score}")
            return
        tempo = f"{score.bpm:g} BPM" + (f"（变速 {score.tempo_changes} 处）" if score.tempo_changes else "")
        self.status.set(f"{tempo} · {clock(score.duration_seconds / self.speed_factor())}")

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
        self.progress["value"] = fraction
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
        width = self.progress.winfo_width()
        self.start(fraction=min(max(event.x / width, 0.0), 0.999) if width > 0 else 0.0)

    def set_playing(self, playing: bool) -> None:
        state = "disabled" if playing else "normal"
        self.button.configure(text=STOP if playing else PLAY, state=state)
        self.next_button.configure(state=state)
        self.speed_box.configure(state=state if playing else "readonly")
        self.mode_box.configure(state=state if playing else "readonly")
        self.listbox.configure(state=state)

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
                self.status.set(value)
            elif kind == "song":
                path, self.duration = value
                self.select(path)
            elif kind == "progress":
                self.progress["value"] = value / self.duration if self.duration else 0
                self.status.set(f"{clock(value)} / {clock(self.duration)} · 按 F8 或切换窗口停止")
            elif kind == "error":
                self.status.set("出错，已停止")
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
