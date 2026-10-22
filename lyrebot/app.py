"""桌面界面：从 music 文件夹选曲，倒计时后把曲谱按键发送到前台窗口。"""

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

from .playback import compile_timeline, play_timeline
from .score import Score, load_score
from .windows_input import WindowsInput, get_foreground_window


PROJECT = Path(__file__).resolve().parent.parent
MUSIC_DIR = PROJECT / "music"
APP_TITLE = "LyreBot 自动演奏"
BACKGROUND = "#f4f6fb"


def _clock_label(seconds: float) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{seconds:02d}"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin() -> bool:
    """Ask Windows (UAC) to start a new elevated LyreBot. True if it started."""
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, "-m lyrebot", str(PROJECT), 1)
    except (AttributeError, OSError):
        return False
    return int(result) > 32


class _DisplayBackend:
    """Forward real input and report the held keys to the interface."""

    def __init__(self, updates: queue.Queue, real: WindowsInput):
        self.updates = updates
        self.real = real
        self.held: set[str] = set()

    def foreground_window(self) -> int:
        return self.real.foreground_window()

    def stop_requested(self) -> bool:
        return self.real.stop_requested()

    def held_modifiers(self) -> tuple[str, ...]:
        return self.real.held_modifiers()

    def send_keys(self, keys: tuple[str, ...], down: bool) -> None:
        self.real.send_keys(keys, down)
        if down:
            self.held.update(keys)
        else:
            self.held.difference_update(keys)
        self.updates.put(("keys", tuple(self.held)))

    def release_all(self) -> None:
        try:
            self.real.release_all()
        finally:
            self.held.clear()
            self.updates.put(("keys", ()))


class LyreBotApp:
    def __init__(self, root: tk.Tk, music_dir: Path = MUSIC_DIR):
        self.root = root
        self.music_dir = Path(music_dir)
        self.root.title(APP_TITLE)
        scale = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.wrap = int(600 * scale)
        self.root.geometry(f"{int(900 * scale)}x{int(640 * scale)}")
        self.root.minsize(int(800 * scale), int(580 * scale))
        self.root.configure(background=BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<F8>", lambda _event: self.stop())
        self.songs: list[Path] = []
        self.score: Score | None = None
        self.score_path: Path | None = None
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()
        self.updates: queue.Queue = queue.Queue()
        self.closing = False
        self.duration = 0.0
        self.song_title = tk.StringVar(value="未选择曲目")
        self.summary = tk.StringVar(value="在左侧列表中选择一首曲子。")
        self.speed = tk.StringVar(value="100")
        self.tap_ms = tk.StringVar(value="35")
        self.countdown = tk.StringVar(value="3")
        self.status = tk.StringVar(value="就绪。")
        self.elapsed = tk.StringVar(value="00:00 / 00:00")
        self.controls: list[tk.Widget] = []
        self.key_labels: dict[str, tk.Label] = {}
        self._build()
        self.refresh_songs()
        self.root.after(25, self._poll)

    # ---------- 界面 ----------
    def _build(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        font = "Microsoft YaHei UI"
        style.configure("TFrame", background=BACKGROUND)
        style.configure("TLabel", background=BACKGROUND, font=(font, 10))
        style.configure("TButton", font=(font, 10), padding=(12, 6))
        style.configure("Title.TLabel", font=(font, 20, "bold"), foreground="#183b63")
        style.configure("Song.TLabel", font=(font, 14, "bold"), foreground="#183b63")
        style.configure("Sub.TLabel", foreground="#54657a")
        style.configure("Warn.TLabel", foreground="#b45309", font=(font, 10, "bold"))

        body = ttk.Frame(self.root, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(2, weight=1)

        ttk.Label(body, text=APP_TITLE, style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        if not is_admin():
            banner = ttk.Frame(body)
            banner.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 10))
            ttk.Label(banner, text="当前未以管理员身份运行：原神等游戏会忽略按键。",
                      style="Warn.TLabel").pack(side="left")
            self._button(banner, "以管理员身份重新启动", self.restart_as_admin).pack(side="left", padx=10)
        else:
            ttk.Label(body, text="已以管理员身份运行。", style="Sub.TLabel").grid(
                row=1, column=0, columnspan=2, sticky="w", pady=(4, 10))

        # 左侧：曲目列表
        left = ttk.Frame(body)
        left.grid(row=2, column=0, sticky="ns", padx=(0, 16))
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="曲目（music 文件夹，双击开始演奏）").grid(row=0, column=0, columnspan=2, sticky="w")
        self.song_list = tk.Listbox(left, width=26, font=("Microsoft YaHei UI", 11), activestyle="none",
                                    exportselection=False, selectbackground="#2766bc")
        self.song_list.grid(row=1, column=0, sticky="ns", pady=(4, 6))
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.song_list.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(4, 6))
        self.song_list.configure(yscrollcommand=scrollbar.set)
        self.song_list.bind("<<ListboxSelect>>", lambda _event: self.select_song())
        self.song_list.bind("<Double-Button-1>", lambda _event: self.start())
        self.song_list.bind("<Return>", lambda _event: self.start())
        self.controls.append(self.song_list)
        buttons = ttk.Frame(left)
        buttons.grid(row=2, column=0, columnspan=2, sticky="w")
        self._button(buttons, "刷新列表", self.refresh_songs).pack(side="left")
        self._button(buttons, "打开文件夹", self.open_music_dir).pack(side="left", padx=6)

        # 右侧：信息、设置、键盘、控制
        right = ttk.Frame(body)
        right.grid(row=2, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        ttk.Label(right, textvariable=self.song_title, style="Song.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(right, textvariable=self.summary, style="Sub.TLabel", wraplength=self.wrap).grid(
            row=1, column=0, sticky="w", pady=(2, 12))

        settings = ttk.Frame(right)
        settings.grid(row=2, column=0, sticky="w")
        for column, (label, value, low, high, step) in enumerate((
            ("速度（%）", self.speed, 25, 300, 5),
            ("按键时长（毫秒）", self.tap_ms, 1, 500, 5),
            ("倒计时（秒）", self.countdown, 1, 30, 1),
        )):
            cell = ttk.Frame(settings)
            cell.grid(row=0, column=column, padx=(0, 22), sticky="w")
            ttk.Label(cell, text=label).pack(anchor="w")
            field = ttk.Spinbox(cell, from_=low, to=high, increment=step, textvariable=value, width=10)
            field.pack(anchor="w", pady=(3, 0))
            self.controls.append(field)

        keyboard = tk.Frame(right, background="#e5ebf4", padx=8, pady=8)
        keyboard.grid(row=3, column=0, sticky="ew", pady=14)
        for row, keys in enumerate(("qwertyu", "asdfghj", "zxcvbnm")):
            for column, key in enumerate(keys):
                keyboard.columnconfigure(column, weight=1)
                label = tk.Label(keyboard, text=key.upper(), font=("Segoe UI", 12, "bold"),
                                 background="white", foreground="#183b63", pady=6)
                label.grid(row=row, column=column, padx=3, pady=3, sticky="ew")
                self.key_labels[key] = label

        actions = ttk.Frame(right)
        actions.grid(row=4, column=0, sticky="w")
        self._button(actions, "开始演奏", self.start).pack(side="left")
        self.stop_button = ttk.Button(actions, text="停止（F8）", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.progress = ttk.Progressbar(right, maximum=1)
        self.progress.grid(row=5, column=0, sticky="ew", pady=(12, 2))
        ttk.Label(right, textvariable=self.elapsed, style="Sub.TLabel").grid(row=6, column=0, sticky="e")
        ttk.Label(right, textvariable=self.status, wraplength=self.wrap).grid(row=7, column=0, sticky="w", pady=4)
        ttk.Label(right, style="Sub.TLabel", wraplength=self.wrap, justify="left", text=(
            "用法：选好曲子后点「开始演奏」，在倒计时内切换到游戏并打开琴。"
            "倒计时结束时位于前台的窗口就是演奏目标。\n"
            "停止方式：按住 F8、切换到其他窗口、按住 Ctrl/Shift/Alt/Win，或把鼠标移到主屏幕左上角。\n"
            "如果游戏里没有声音：确认已以管理员身份运行，并把输入法切到英文模式（中文输入法会拦截字母键）。"
        )).grid(row=8, column=0, sticky="w", pady=(8, 0))

    def _button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        self.controls.append(button)
        return button

    # ---------- 曲目 ----------
    def refresh_songs(self) -> None:
        current = self.score_path
        self.songs = sorted(self.music_dir.glob("*.txt"), key=lambda path: path.stem) \
            if self.music_dir.is_dir() else []
        self.song_list.delete(0, "end")
        for path in self.songs:
            self.song_list.insert("end", path.stem)
        if not self.songs:
            self.status.set(f"没有找到曲谱：请把 .txt 曲谱放进 {self.music_dir}")
            return
        if current in self.songs:
            index = self.songs.index(current)
            self.song_list.selection_set(index)
            self.song_list.see(index)
            self.load(current)

    def open_music_dir(self) -> None:
        try:
            self.music_dir.mkdir(exist_ok=True)
            os.startfile(self.music_dir)  # type: ignore[attr-defined]
        except (AttributeError, OSError) as error:
            messagebox.showerror("无法打开文件夹", str(error), parent=self.root)

    def select_song(self) -> None:
        selection = self.song_list.curselection()
        if selection and not self.worker:
            self.load(self.songs[selection[0]])

    def load(self, path: Path) -> bool:
        self.score_path = path
        self.song_title.set(path.stem)
        self.progress["value"] = 0
        try:
            score = load_score(path)
        except (OSError, ValueError, UnicodeError) as error:
            self.score = None
            self.summary.set(f"曲谱有误，无法演奏：{error}")
            self.status.set("请修改该曲谱文件后点「刷新列表」。")
            self.elapsed.set("00:00 / 00:00")
            return False
        self.score = score
        notes = sum(bool(event.keys) for event in score.events)
        details = [f"速度 {score.bpm:g} BPM", f"{score.measures} 小节", f"{score.parts} 个声部",
                   f"{notes} 个音符/和弦", f"时长 {_clock_label(score.duration_seconds)}"]
        if score.tempo_changes:
            details.insert(1, f"曲中变速 {len(score.tempo_changes)} 处")
        self.summary.set(" · ".join(details))
        self.status.set("就绪。点「开始演奏」或双击曲目。")
        self.duration = score.duration_seconds
        self.elapsed.set(f"00:00 / {_clock_label(self.duration)}")
        return True

    def restart_as_admin(self) -> None:
        if self.worker:
            return
        if relaunch_as_admin():
            self.root.destroy()
        else:
            messagebox.showerror("无法以管理员身份启动",
                                 "请右键「Start LyreBot.cmd」，选择「以管理员身份运行」。", parent=self.root)

    # ---------- 演奏 ----------
    def _read_settings(self) -> tuple[float, float, float]:
        try:
            speed = float(self.speed.get())
            tap_ms = float(self.tap_ms.get())
            delay = float(self.countdown.get())
        except ValueError:
            raise ValueError("速度、按键时长和倒计时必须是数字。") from None
        if not math.isfinite(speed) or not 25 <= speed <= 300:
            raise ValueError("速度必须在 25% 到 300% 之间。")
        if not math.isfinite(tap_ms) or not 1 <= tap_ms <= 500:
            raise ValueError("按键时长必须在 1 到 500 毫秒之间。")
        if not math.isfinite(delay) or not 1 <= delay <= 30:
            raise ValueError("倒计时必须在 1 到 30 秒之间。")
        return speed, tap_ms, delay

    def start(self) -> None:
        if self.worker:
            return
        self.select_song()
        if not self.score:
            messagebox.showinfo("请选择曲目", "请先在左侧选择一首可以演奏的曲子。", parent=self.root)
            return
        try:
            speed, tap_ms, delay = self._read_settings()
            timeline = compile_timeline(self.score, bpm=self.score.bpm * speed / 100, tap_ms=tap_ms)
            backend = WindowsInput()
        except (ValueError, OSError, RuntimeError) as error:
            messagebox.showerror("无法开始演奏", str(error), parent=self.root)
            return
        self.cancel = threading.Event()
        self.duration = timeline.duration_seconds
        self.progress["value"] = 0
        self.elapsed.set(f"00:00 / {_clock_label(self.duration)}")
        self._set_running(True)
        display = _DisplayBackend(self.updates, backend)

        def run() -> None:
            last_update = -1.0

            def progress(elapsed: float) -> None:
                nonlocal last_update
                if elapsed - last_update >= 0.03 or elapsed >= self.duration:
                    self.updates.put(("progress", elapsed))
                    last_update = elapsed

            try:
                deadline = time.perf_counter() + delay
                previous_seconds = None
                while True:
                    remaining = deadline - time.perf_counter()
                    if self.cancel.is_set() or display.stop_requested():
                        self.updates.put(("status", "已在倒计时中停止。"))
                        return
                    if remaining <= 0:
                        break
                    seconds = math.ceil(remaining)
                    if seconds != previous_seconds:
                        self.updates.put(("status", f"{seconds} 秒后开始……请切换到游戏窗口。"))
                        previous_seconds = seconds
                    self.cancel.wait(min(0.01, remaining))
                target = get_foreground_window()
                if not target.hwnd or target.process_id == os.getpid():
                    self.updates.put(("status", "倒计时结束时前台仍是 LyreBot，未发送按键。请在倒计时内切换到游戏窗口。"))
                    return
                name = target.title or "无标题窗口"
                self.updates.put(("status", f"正在向「{name}」演奏……按住 F8 停止。"))
                result = play_timeline(timeline, display, target.hwnd, self.cancel, on_progress=progress)
                self.updates.put(("status", "演奏完成。" if result.reason == "completed" else "演奏已停止。"))
            except Exception as error:
                self.updates.put(("error", str(error)))
            finally:
                try:
                    display.release_all()
                except Exception as error:
                    self.updates.put(("error", f"松开按键失败：{error}。请手动松开相关按键。"))
                self.updates.put(("done", None))

        self.worker = threading.Thread(target=run, name="lyrebot-playback", daemon=True)
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        for control in self.controls:
            control.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def stop(self) -> None:
        if self.worker:
            self.cancel.set()
            self.status.set("正在停止并松开按键……")

    def _poll(self) -> None:
        for _ in range(1500):
            try:
                kind, value = self.updates.get_nowait()
            except queue.Empty:
                break
            if kind == "keys":
                for key, label in self.key_labels.items():
                    label.configure(background="#2766bc" if key in value else "white",
                                    foreground="white" if key in value else "#183b63")
            elif kind == "progress":
                self.progress["value"] = min(1, value / self.duration) if self.duration else 0
                self.elapsed.set(f"{_clock_label(value)} / {_clock_label(self.duration)}")
            elif kind == "status":
                self.status.set(value)
            elif kind == "error":
                self.status.set(f"已停止：{value}")
                if not self.closing:
                    messagebox.showerror("演奏已停止", value, parent=self.root)
            elif kind == "done":
                self.worker = None
                self._set_running(False)
        if self.closing and self.worker is None:
            self.root.destroy()
            return
        self.root.after(25, self._poll)

    def close(self) -> None:
        self.closing = True
        if self.worker:
            self.stop()
        else:
            self.root.destroy()


def main() -> None:
    try:
        # Sharp text on high-DPI screens.
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    LyreBotApp(root)
    root.mainloop()
