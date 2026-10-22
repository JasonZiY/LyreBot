"""A small window: pick a song, switch to the game, it plays."""

from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import queue
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

        root.title("LyreBot" if is_admin() else "LyreBot（未以管理员身份运行）")
        scale = root.winfo_fpixels("1i") / 96
        root.geometry(f"{int(300 * scale)}x{int(400 * scale)}")
        root.minsize(int(260 * scale), int(260 * scale))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F8>", lambda _: self.stop())

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
        self.listbox.bind("<Double-Button-1>", lambda _: self.toggle())
        self.listbox.bind("<Return>", lambda _: self.toggle())
        menu = tk.Menu(root, tearoff=False)
        menu.add_command(label="打开曲谱文件夹", command=self.open_folder)
        self.listbox.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))

        row = ttk.Frame(frame)
        row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(row, text="速度", font=FONT).pack(side="left")
        self.speed = tk.StringVar(value="100")
        self.speed_box = ttk.Spinbox(row, from_=25, to=300, increment=5, width=4, textvariable=self.speed, font=FONT)
        self.speed_box.pack(side="left", padx=(4, 2))
        ttk.Label(row, text="%", font=FONT).pack(side="left")
        self.button = ttk.Button(row, text="演奏", width=8, command=self.toggle)
        self.button.pack(side="right")

        self.progress = ttk.Progressbar(frame, maximum=1.0)
        self.progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, font=FONT, foreground="#555").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.refresh()
        if self.songs:
            self.listbox.selection_set(0)
            self.show_selected()
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
                self.listbox.selection_set(songs.index(chosen))
                self.listbox.see(songs.index(chosen))
            self.show_selected()
        if not songs:
            self.status.set("music 文件夹里还没有 .txt 曲谱（右键列表可打开文件夹）")

    def selected(self) -> Path | None:
        chosen = self.listbox.curselection()
        return self.songs[chosen[0]] if chosen and chosen[0] < len(self.songs) else None

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
        self.status.set(f"{tempo} · {clock(score.duration_seconds)}")

    def open_folder(self) -> None:
        self.music_dir.mkdir(exist_ok=True)
        os.startfile(self.music_dir)  # type: ignore[attr-defined]

    # ---- playing ----
    def toggle(self) -> None:
        if self.worker:
            self.stop()
            return
        path = self.selected()
        if path is None:
            return
        self.cache.pop(path, None)  # always play the file as it is now
        score = self.read(path)
        if isinstance(score, ScoreError):
            messagebox.showerror("曲谱有误", str(score), parent=self.root)
            self.refresh()
            return
        try:
            speed = float(self.speed.get()) / 100
            if not 0.25 <= speed <= 3:
                raise ValueError
        except ValueError:
            messagebox.showerror("速度无效", "速度请填 25 到 300 之间的数字。", parent=self.root)
            return
        timeline = compile_timeline(score, speed)
        self.duration = timeline.duration
        self.cancel = threading.Event()
        self.button.configure(text="停止")
        self.speed_box.configure(state="disabled")
        self.listbox.configure(state="disabled")
        self.worker = threading.Thread(target=self.run, args=(timeline,), daemon=True)
        self.worker.start()

    def run(self, timeline) -> None:
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
            post(("status", "正在演奏 · 按 F8 或切换窗口停止"))
            reason = play(timeline, keyboard, target, self.cancel, lambda t: post(("progress", t)))
            post(("status", {"done": "演奏完成", "stopped": "已停止", "focus": "切换了窗口，已停止"}[reason]))
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
            elif kind == "progress":
                self.progress["value"] = value / self.duration if self.duration else 0
                self.status.set(f"{clock(value)} / {clock(self.duration)} · 按 F8 或切换窗口停止")
            elif kind == "error":
                self.status.set("出错，已停止")
                if not self.closing:
                    messagebox.showerror("演奏出错", value, parent=self.root)
            elif kind == "done":
                self.worker = None
                self.button.configure(text="演奏")
                self.speed_box.configure(state="normal")
                self.listbox.configure(state="normal")
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
    root = tk.Tk()
    App(root)
    root.mainloop()
