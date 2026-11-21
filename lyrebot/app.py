"""A small window: pick a song, switch to the game, it plays."""

from __future__ import annotations

from bisect import bisect_right
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox as system_dialogs
from typing import Callable

from . import chrome, dialogs, guide, icons, quickstart
from .arrange import ARRANGED, ArrangeError, arrange_file
from .audition import INSTRUMENTS, PIANO, Audition, AuditionError
from .collate import pinyin_key
from . import jianpu
from .jianpu import export as export_jianpu
from .keyboard import Keyboard, foreground_title_and_pid, start_pressed
from .listen import QUIET, listen
from .midi import MidiError
from .notate import notate
from .paper import OnPage, Page, ScrollingPage
from .player import play
from .record import SILENCE, WAIT, record
from .score import Score, ScoreError, load_score, measure_lines, parse_score, score_files
from .scoreview import ScoreView
from .theme import (CACHE_ROOT, CHOSEN_TINT, FAINT, GOLD, GOLD_SOFT, HAIR, HOVER_TINT, INK, MUTED, ORANGE,
                    ORANGE_DEEP, PAPER, PEACH, WARN, WHITE, Theme, dress, geometry, round_corners, screen_area,
                    screen_holding)
from .timeline import compile_ringing, compile_timeline
from .transcribe import AUDIO_SUFFIXES, Tools, TranscribeError, find_tools, tool_folders, transcribe
from .widgets import (Callout, Choice, IconButton, Manuscript, OneLine, Ornament, PageLine, Picture, PopupMenu,
                      Sheet, TitleBar, Words)

PROJECT = Path(__file__).resolve().parent.parent
SCORES_DIR = PROJECT / "txt"
MIDI_DIR = PROJECT / "midi"
JIANPU_DIR = PROJECT / "jianpu_output"
TOOL_DIRS = tool_folders(PROJECT)
MIDI_SUFFIXES = (".mid", ".midi")
SETTLE = 1.5  # seconds a MIDI or audio file must stay unchanged before it is used: it may still be copying
BAD_NAME_CHARACTERS = set('\\/:*?"<>|')
OST, SONGS = "OST", "歌曲"  # the two folders of txt that are playlists; other scores are unfiled
OTHER_REGION = "其他"  # the last region of OST, which also takes scores not in any region's folder
REGIONS = ("蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "挪德卡莱", "至冬", "夏日", OTHER_REGION)
SONG_COLUMNS = 2  # beside an open region, and across the 歌曲 shelf
RECORDING_SUFFIX = "-录音"  # a recording of 飞鼠进行曲 is saved as 飞鼠进行曲-录音
PLAYING_SUFFIX = "-演奏"  # and the keys played for it as 飞鼠进行曲-演奏
MIDI_FILE_SUFFIX = "-MIDI"  # and the score arranged from a MIDI file of it, 飞鼠进行曲.mid, as 飞鼠进行曲-MIDI
NO_TOOLS = ("录音编配要用到 audio.cpp 和 MuScriptor 模型，LyreBot 没有自带。\n\n"
            "把它们放进 LyreBot 文件夹里的 tools 文件夹，或与 LyreBot 文件夹并排的 LyreBot-tools 文件夹。"
            "下载地址和放法见 docs 文件夹里的「从录音生成曲谱-安装说明」。")
ICON = PROJECT / "assets" / "lyrebot.ico"
COUNTDOWN = 2.0  # seconds to switch to the game after starting a song from the window; F7 needs none
HOTKEY_POLL = 30  # milliseconds between looks at F7
READY = "请切换到游戏窗口，按 F7 立刻开始"  # under the bar once a song is chosen
GO_ON = "点 ▶ 或按 F7 从该小节继续"  # once it was stopped part way: the bar counter shows which bar
LET_GO = "松开后从该小节开始"  # while the knob is dragged: the bar counter shows which bar
# The 简谱 page, behind the ribbon: a song is heard there on the computer, from ▶ or from a note clicked.
HEARING = "正在试听 · 点 ■ 或按 F8 停止，点别的音从那里接着听"
TO_HEAR = "点简谱上的任何一个音，从那里开始听"
HEAR_TIP, HALT_TIP = "试听", "停止试听（F8）"
TO_SCORE, TO_SONGS = "翻到简谱，试听", "翻回曲目"
GROW_TIP, SHRINK_TIP = "放大：一行四小节，放得下相邻四行", "缩回窄窗"
# The second ribbon, green, on the 简谱 page and the page behind it, where the score's text is written.
GREEN_FROM_RIGHT = 74  # its left edge, from the window's right, at 100% scaling: beside the orange one
TO_TEXT, TO_LEAF = "翻到原文，修改", "翻回简谱"
UNDO_TIP, SAVE_TIP, GUIDE_TIP = "撤销（Ctrl+Z）", "保存（Ctrl+S）", "曲谱格式速查"
SAVED, CHANGED = "已保存", "有改动，还没保存"
TEXT_HINT = "停手半秒就查一遍错；翻页时没存的改动会自动存"
CHECK_AFTER = 500  # milliseconds after the last change before the text is read for mistakes
LEAVES = 6  # songs kept read for the 简谱 page, the latest last
# A click on the ribbon tugs it: how much longer than at rest it hangs, frame by frame (pixels at 100%), pulled
# down, let go, springing up a little past its rest and settling.  The page turns where it is pulled furthest;
# till the last frame, clicks on it count for nothing.
TUG = (3, 6, 8.5, 10, 8, 4, 0, -2.5, -3, -2, -0.5, 0.5, 0.5, 0)
TUG_TURN = 3
TUG_STEP = 24  # milliseconds a frame  # what the ribbon does, said when the pointer rests on it
EMPTY = ("把 .txt 曲谱放进 txt 文件夹，或把 MIDI 放进 midi 文件夹，"
         "也可以点 ● 录一段音乐，或点 ● 左边的键盘记下自己弹的琴")  # when there is no score at all
SPEEDS = ("80%", "100%", "120%", "150%", "200%")
CUSTOM_SPEED = "自定义…"
SLOWEST_SPEED, FASTEST_SPEED = 25.0, 300.0  # percent
SINGLE, REPEAT, SHUFFLE, IN_ORDER = MODES = ("单曲", "单曲循环", "随机演奏", "顺序演奏")
ON_ITS_OWN = (SINGLE, REPEAT)  # the modes a song in no playlist can play in
UNFILED = "未归类"
GAP = 1.0  # seconds of silence between songs when playing on
COOLDOWN = 0.5  # seconds after a stop during which clicks cannot start a song again
ROW_HEIGHT = 22  # of the song list, in pixels at 100% scaling
MARK_WIDTH = 14  # the H of strings before the song being played, post to post, at 100% scaling ...
STRING_GAP = 3  # ... and between its strings, which are the game lyre's rows:
LYRE_ROWS = {key: row for row, keys in enumerate(("zxcvbnm", "asdfghj", "qwertyu")) for key in keys}
SHADES = 5  # steps from a string's gold at rest to the deep orange of one just struck
FADE = 0.25  # seconds for a struck string's orange to fade to about a third (gold again by 0.6 s)
SHIVER = 0.15  # seconds a struck string shivers ...
SWELLS = (1.2, 0.5)  # ... swelling this many pixels at its middle, by turns at each look
RING_STEP = 40  # milliseconds between looks at the strings while one still rings
WINDOW_WIDTH = 350  # the window's width when it opens, and the narrowest it goes: the widest controls fit
OPENING_HEIGHT = 640  # and its height when it opens
WATERMARK_SIZE = 136  # the lyre pressed into the page, at 100% scaling
RIBBON_FROM_RIGHT = 59  # the ribbon's left edge, from the window's right, at 100% scaling


def parse_speed(text: str) -> float | None:
    """The percentage typed for the speed, as in 135 or 87.5%, or None if it is not one to play at."""
    try:
        percent = float(text.strip().rstrip("%％").strip())
    except ValueError:
        return None
    return percent if SLOWEST_SPEED <= percent <= FASTEST_SPEED else None


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


FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI, FOF_WANTNUKEWARNING = 0x4, 0x10, 0x40, 0x400, 0x4000


class FileOperation(ctypes.Structure):
    """Windows' SHFILEOPSTRUCTW, which is packed tight on 32-bit Windows."""
    _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
    _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_void_p),
                ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]


def to_recycle_bin(path: Path) -> None:
    """Move a file to the Recycle Bin, where it can be restored from; OSError if it was not moved.

    Where it cannot be recycled (the bin turned off, a network drive), Windows
    asks before deleting it for good.
    """
    names = ctypes.create_unicode_buffer(str(path.resolve()) + "\0")  # a list of names, ended by an empty one
    operation = FileOperation(wFunc=FO_DELETE, pFrom=ctypes.addressof(names),
                              fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI
                              | FOF_WANTNUKEWARNING)
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    except AttributeError:
        raise OSError("没有回收站可用") from None
    if result:
        raise OSError(f"没能移到回收站（错误 {result:#x}）")
    if operation.fAnyOperationsAborted or path.exists():
        raise OSError("没有删除")


class Shuffle:
    """随机演奏 in rounds: every song of a playlist once, in a random order, before any of them again.

    A round lasts across stops, F7, ⏭ and songs chosen by hand: whatever plays in 随机演奏 is
    taken out of what is left of its round.  OST and 歌曲 each have their own round, as the
    songs played are kept by path and each playlist only looks at its own.  A song
    added to the playlist joins the round under way; a new round never starts with the song
    just played.  Only the playing thread uses it while a song plays, and the window only when
    nothing does, so it needs no lock.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.played: set[Path] = set()

    def playing(self, path: Path) -> None:
        self.played.add(path)

    def draw(self, songs: list[Path], current: Path | None) -> Path:
        left = [song for song in songs if song not in self.played and song != current]
        if not left:  # the round is over
            self.played.difference_update(songs)
            left = [song for song in songs if song != current] or songs
        return self.rng.choice(left)


def next_song(songs: list[Path], current: Path | None, mode: str, shuffle: Shuffle | None = None) -> Path | None:
    if not songs:
        return None
    if mode == SHUFFLE:
        return (shuffle or Shuffle()).draw(songs, current)
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


@dataclass(frozen=True)
class Library:
    """The scores as the list shows them, each group in pinyin order."""
    unfiled: tuple[Path, ...]  # in no playlist
    regions: tuple[tuple[str, tuple[Path, ...]], ...]  # of OST, in playing order
    songs: tuple[Path, ...]

    @property
    def ost(self) -> list[Path]:
        return [path for _, paths in self.regions for path in paths]

    @property
    def everything(self) -> list[Path]:
        return [*self.unfiled, *self.ost, *self.songs]

    def playlist(self, path: Path | None) -> list[Path]:
        """The songs ``path`` plays on with: all of OST, region after region, or all of 歌曲."""
        ost = self.ost
        return ost if path in ost else list(self.songs) if path in self.songs else []

    def region_of(self, path: Path) -> str | None:
        return next((name for name, paths in self.regions if path in paths), None)


def read_library(scores_dir: Path) -> Library:
    """Sort the scores of the txt folder into the list's groups.

    Scores in txt/OST/地区 belong to that region, and those directly in txt/OST
    to 其他; a folder of OST with another name is one more region, before 其他.
    Everything in txt/歌曲 is one list.  The rest is unfiled.
    """
    unfiled: list[Path] = []
    songs: list[Path] = []
    regions: dict[str, list[Path]] = {name: [] for name in REGIONS}
    known = {name.casefold(): name for name in REGIONS}
    extra: set[str] = set()

    def region(folder: str) -> str:
        name = known.get(folder.casefold())
        if name is None:
            name = folder
            extra.add(name)
        return name

    for path in score_files(scores_dir):
        folders = path.relative_to(scores_dir).parts[:-1]
        shelf = folders[0].casefold() if folders else ""
        if shelf == OST.casefold():
            regions.setdefault(region(folders[1]) if len(folders) > 1 else OTHER_REGION, []).append(path)
        elif shelf == SONGS.casefold():
            songs.append(path)
        else:
            unfiled.append(path)
    if scores_dir.is_dir():  # an empty folder still shows, ready to drop scores into
        for shelf in scores_dir.iterdir():
            if shelf.is_dir() and shelf.name.casefold() == OST.casefold():
                for folder in shelf.iterdir():
                    if folder.is_dir():
                        regions.setdefault(region(folder.name), [])
    named = [name for name in REGIONS if name != OTHER_REGION]
    order = [*named, *sorted(extra, key=pinyin_key), OTHER_REGION]

    def sort(paths: list[Path]) -> tuple[Path, ...]:
        return tuple(sorted(paths, key=lambda path: pinyin_key(path.stem)))

    return Library(sort(unfiled), tuple((name, sort(regions[name])) for name in order), sort(songs))


def in_columns(paths: tuple[Path, ...], count: int) -> list[tuple[Path, ...]]:
    """``paths`` run down ``count`` columns, as even as they go, the first ones longer."""
    size, longer = divmod(len(paths), count)
    columns, start = [], 0
    for column in range(count):
        end = start + size + (column < longer)
        columns.append(paths[start:end])
        start = end
    return columns


def next_match(songs: list[Path], query: str, after: Path | None = None) -> Path | None:
    """The first song, in the list's order, whose name holds ``query`` (case aside);
    with ``after``, the first one below it, going round to the top."""
    wanted = query.strip().casefold()
    if not wanted:
        return None
    found = [index for index, path in enumerate(songs) if wanted in path.stem.casefold()]
    if not found:
        return None
    start = songs.index(after) if after in songs else -1
    return songs[next((index for index in found if index > start), found[0])]


def bar_seconds(score: Score, bar: int, speed: float) -> float:
    """When bar ``bar`` (from 0) starts, in seconds of the song played at ``speed``."""
    if not score.bars:
        return 0.0
    start = score.bars[min(max(bar, 0), len(score.bars) - 1)]
    return float(score.tempo_map(speed).seconds(Fraction(start)))


def bar_at(score: Score, seconds: float, speed: float) -> int:
    """The bar (from 0) being played ``seconds`` into the song played at ``speed``."""
    tempo = score.tempo_map(speed)
    starts = [float(tempo.seconds(Fraction(start))) for start in score.bars]
    return max(bisect_right(starts, seconds + 1e-6) - 1, 0)


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


def score_name(midi: Path) -> str:
    """The name of the score arranged from ``midi``.

    A transcribed recording keeps its own name, already 曲名-录音: it has
    audio of the same name beside it, or ends in -录音.  Any other MIDI file
    was put in the folder as it is, so its score is called 曲名-MIDI.
    """
    stem = midi.stem
    if stem.casefold().endswith((RECORDING_SUFFIX, MIDI_FILE_SUFFIX.casefold())):
        return stem
    if any(path.stem.casefold() == stem.casefold() for path in audio_files(midi.parent)):
        return stem
    return stem + MIDI_FILE_SUFFIX


def pending_midi(midi_dir: Path, scores_dir: Path, failed: dict[Path, float], now: float | None = None) -> list[Path]:
    """MIDI files that have no score yet: none of the same name or of the
    name it would get, and none arranged from them and renamed since.

    A score that already exists is never replaced, so edits to it are safe.
    Files still being copied (changed in the last ``SETTLE`` seconds) and files
    that already failed as they are now wait.
    """
    now = time.time() if now is None else now
    arranged = arranged_from(scores_dir)
    names = score_names(scores_dir)
    found = []
    for path in midi_files(midi_dir):
        if path.stem.casefold() in names or score_name(path).casefold() in names or path.name.casefold() in arranged:
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
    or else the one whose score would have this name."""
    files = midi_files(midi_dir)
    named = arranged_name(score)
    for path in files:
        if path.name.casefold() == named:
            return path
    for path in files:
        if score.stem.casefold() in (path.stem.casefold(), score_name(path).casefold()):
            return path
    return None


def song_files(score: Path, midi_dir: Path) -> list[Path]:
    """The files deleting ``score`` takes with it, the score first: the MIDI
    file and recording it was made from, which would otherwise make it again.

    A score written by hand or from the keys played (曲名, 曲名-演奏) goes
    alone.  One arranged from a MIDI file names it on its first line, or, if
    that line was taken out, ends in -MIDI or -录音; its recording is audio
    of the MIDI file's name.
    """
    named = arranged_name(score)
    if named is None and not score.stem.casefold().endswith((RECORDING_SUFFIX, MIDI_FILE_SUFFIX.casefold())):
        return [score]
    source = midi_for(score, midi_dir)
    stem = (source.stem if source else named.rsplit(".", 1)[0] if named else score.stem).casefold()
    audio = [path for path in audio_files(midi_dir) if path.stem.casefold() == stem]
    return [score, *([source] if source else []), *audio]


@dataclass(frozen=True)
class Cell:
    """One clickable place in the song list."""
    kind: str  # "song", "region" or "shelf"
    name: str  # of the region or shelf
    box: tuple[float, float, float, float]
    text: str
    path: Path | None = None
    count: int | None = None  # of the songs in a shelf or region, shown after its name
    is_open: bool = False  # a shelf or region showing what is in it


class SongBrowser(ScrollingPage, tk.Canvas):
    """The song list: unfiled scores across the top, then OST by region and 歌曲.

    Everything starts at one left edge and opens downwards: a shelf's
    regions (or 歌曲's songs) on the lines under its name, a region's songs
    under its own.  Songs run down two columns.  It starts with OST open,
    its regions closed, and 歌曲 closed.

    A shelf's title is set in the serif with a hairline running on to the
    edge; chevrons are old gold; the chosen song sits on a rounded breath of
    orange with an orange edge.  The song being played has three gold strings
    stretched from that edge to another like it, a wide H, and each string
    lights orange as the keys of its row are pressed in the game.  The page
    shows through it all, and stays
    still while the list scrolls over it; at an edge with more songs beyond
    it, the last lines sink into the paper instead of being cut off.
    """

    def __init__(self, parent: tk.Widget, theme: Theme, locked: Callable[[], object],
                 on_select: Callable[[], None], on_play: Callable[[], None], on_delete: Callable[[], None]):
        self.theme = theme
        px = theme.px
        self.row, self.pad = px(ROW_HEIGHT), px(6)
        self.margin = px(8)  # kept clear on either side of the list
        self.mark = px(12)  # the chevrons
        self.indent = self.mark + px(4)  # how far songs sit in from regions: past the chevron, to the name
        self.tail = px(2)  # room after a name: the next column's own margin keeps them apart
        self.shelf_gap = px(6)  # breathing room above a shelf with something over it
        self.corner = px(5)
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, borderwidth=0, takefocus=True,
                         width=px(460), height=px(300), yscrollincrement=self.row)
        self.locked, self.on_select, self.on_play, self.on_delete = locked, on_select, on_play, on_delete
        self.library: Library | None = None
        self.bad: frozenset[Path] = frozenset()
        self.chosen: Path | None = None
        self.playing: Path | None = None
        self.closed_shelves: set[str] = {SONGS}  # it opens with OST's regions in sight, each closed
        self.open_regions: set[str] = set()
        self.cells: list[Cell] = []
        self.total = 0
        self.fitted: dict[tuple[str, int, tuple], str] = {}
        self.edge = px(2)  # the orange edge of the chosen song, and the posts of the playing song's strings
        self.mark_width, self.string_gap = px(MARK_WIDTH), px(STRING_GAP)
        self.string_length = self.mark_width - 2 * self.edge
        self.struck = [-math.inf] * 3  # when each string (low, middle, high) was last struck (monotonic)
        self.ring_job: str | None = None
        self.grounds: dict[str, tuple[tuple[int, int], tk.PhotoImage]] = {}  # tints made for this width, by colour
        self.hover_box = self.create_image(0, 0, anchor="nw", state="hidden")
        self.chosen_box = self.create_image(0, 0, anchor="nw", state="hidden")
        self.chosen_edge = self.create_rectangle(0, 0, 0, 0, fill=ORANGE, width=0, state="hidden")
        self.make_fades()
        self.extent = 0  # how tall the list is, all of it
        self.bind("<Configure>", lambda _: self.lay_out())
        self.bind("<Button-1>", self.click)
        self.bind("<Double-Button-1>", self.double_click)
        self.bind("<Motion>", self.hover)
        self.bind("<Leave>", lambda _: self.itemconfigure(self.hover_box, state="hidden"))
        for key, step in (("Up", -1), ("Down", 1), ("Prior", -8), ("Next", 8), ("Home", -10 ** 6), ("End", 10 ** 6)):
            self.bind(f"<KeyPress-{key}>", lambda _, step=step: self.step(step))
        self.bind("<Return>", lambda _: None if self.locked() else self.on_play())
        self.bind("<Delete>", lambda _: None if self.locked() else self.on_delete())
        self.take_page(theme)

    def ground(self, item: int, color: str, box: tuple[float, float, float, float]) -> None:
        """Lay the rounded tint ``color`` over ``box`` (less a pixel above and below) as ``item``.

        Its width follows the list's, so it is not drawn for each width: Tk builds it from a short pill
        drawn once, its two rounded ends and, stretched between them, a column from its middle."""
        left, top, right, bottom = box
        reach = math.ceil(self.corner)
        width, height = max(2 * reach + 1, round(right - left)), max(1, round(bottom - top) - 2)
        made = self.grounds.get(color)
        if made is None or made[0] != (width, height):
            pill = self.theme.image(f"ground:{color}:{self.corner}", 2 * reach + 1, height,
                                    lambda: [(color, icons.rect(0, 0, 2 * reach + 1, height, self.corner))])
            image = tk.PhotoImage(master=self, width=width, height=height)
            for source, target in (((reach, 0, reach + 1, height), (reach, 0, width - reach, height)),
                                   ((0, 0, reach, height), (0, 0)), ((reach + 1, 0, 2 * reach + 1, height), (width - reach, 0))):
                image.tk.call(image, "copy", pill, "-from", *source, "-to", *target, "-compositingrule", "set")
            made = ((width, height), image)
        self.coords(item, round(left), round(top) + 1)
        self.itemconfigure(item, image=made[1], state="normal")
        self.grounds[color] = made

    def show(self, library: Library, bad: frozenset[Path]) -> None:
        self.library, self.bad = library, bad
        self.lay_out()

    @staticmethod
    def column_edges(left: float, right: float) -> list[float]:
        column = (right - left) / SONG_COLUMNS
        return [left + index * column for index in range(SONG_COLUMNS + 1)]

    def lay_out(self) -> None:
        library = self.library
        if library is None:
            return
        width = self.winfo_width() if self.winfo_width() > 1 else int(self.cget("width"))
        left, right = self.margin, width - self.margin
        songs_left = left + self.indent
        columns = self.column_edges(songs_left, right)
        row = self.row
        cells: list[Cell] = []

        def song(path: Path, left: float, top: int, right: float) -> None:
            cells.append(Cell("song", "", (left, top, right, top + row), path.stem, path))

        def run_down(paths: tuple[Path, ...], edges: list[float], top: int) -> int:
            columns = in_columns(paths, SONG_COLUMNS)
            for index, column in enumerate(columns):
                for line, path in enumerate(column):
                    song(path, edges[index], top + line * row, edges[index + 1])
            return max(map(len, columns), default=0) * row

        y = 0
        for path in library.unfiled:
            song(path, songs_left, y, right)
            y += row
        if library.unfiled:
            y += self.shelf_gap
        is_open = OST not in self.closed_shelves
        cells.append(Cell("shelf", OST, (left, y, right, y + row), OST, count=len(library.ost), is_open=is_open))
        y += row
        if is_open:
            for name, paths in library.regions:
                expanded = name in self.open_regions
                cells.append(Cell("region", name, (left, y, right, y + row), name, count=len(paths), is_open=expanded))
                y += row
                if expanded:
                    y += run_down(paths, columns, y)
        y += self.shelf_gap
        is_open = SONGS not in self.closed_shelves
        cells.append(Cell("shelf", SONGS, (left, y, right, y + row), SONGS, count=len(library.songs),
                          is_open=is_open))
        y += row
        if is_open:
            y += run_down(library.songs, columns, y)

        self.cells, self.total = cells, y
        self.delete("content")
        for cell in cells:
            self.draw(cell)
        bottom = y
        if not library.everything:  # nothing to play yet: a lyre drawn in gold, waiting
            size = self.theme.px(72)
            art = self.theme.icon("lyre", size, GOLD_SOFT, icons.lyre)
            self.create_image(width / 2, y + self.theme.px(28) + size / 2, image=art, tags="content")
            bottom = y + self.theme.px(40) + size
        self.configure(scrollregion=(0, 0, width, max(bottom, 1)))
        self.extent = max(bottom, 1)
        self.follow_page()  # a new scroll region may have moved the view (and the fades go over the new lines)
        self.itemconfigure(self.hover_box, state="hidden")
        self.mark_chosen()

    def draw(self, cell: Cell) -> None:
        """Draw one place in the list: a song, or a shelf or region with its chevron and count."""
        theme, fonts = self.theme, self.theme.fonts
        left, top, right, bottom = cell.box
        middle = (top + bottom) / 2
        x = left + self.pad
        if cell.kind == "song":
            ink = INK
            if cell.path == self.playing:
                x = self.draw_strings(cell.box)
            if cell.path in self.bad:
                size = theme.px(11)
                self.create_image(x + size / 2, middle, image=self.warning(size), tags="content")
                x += size + theme.px(4)
                ink = WARN
            text = self.fit(cell.text, int(right - x) - self.tail, fonts.body)
            self.create_text(x, middle, anchor="w", text=text, fill=ink, font=fonts.body, tags="content")
            return
        empty = cell.kind == "region" and not cell.count
        chevron = theme.icon("chevron-down" if cell.is_open else "chevron", self.mark, FAINT if empty else GOLD,
                             lambda size: icons.chevron(size, down=cell.is_open))
        self.create_image(x + self.mark / 2, middle, image=chevron, tags="content")
        x = left + self.pad + self.indent
        font = fonts.shelf if cell.kind == "shelf" else fonts.body
        text = self.fit(cell.text, int(right - x) - self.tail, font)
        self.create_text(x, middle, anchor="w", text=text, fill=MUTED if empty else INK, font=font, tags="content")
        x += theme.measure(font, text) + theme.px(6)
        count = str(cell.count)
        self.create_text(x, middle, anchor="w", text=count, fill=FAINT if empty else MUTED, font=fonts.figures,
                         tags="content")
        if cell.kind == "shelf":
            start, end = x + theme.measure(fonts.figures, count) + theme.px(8), right - self.pad
            if start < end:
                self.create_line(start, middle, end, middle, fill=HAIR, tags="content")

    def warning(self, size: int) -> tk.PhotoImage:
        return self.theme.icon("warning", size, WARN, icons.warning)

    def fit(self, text: str, width: int, font: tuple) -> str:
        """``text``, cut short with … if it is wider than ``width``."""
        key = (text, width, font)
        if key not in self.fitted:
            measure = lambda piece: self.theme.measure(font, piece)  # noqa: E731
            short = text
            if measure(text) > width:
                low, high = 0, len(text)
                while low < high:
                    middle = (low + high + 1) // 2
                    if measure(text[:middle] + "…") <= width:
                        low = middle
                    else:
                        high = middle - 1
                short = text[:low] + "…"
            self.fitted[key] = short
        return self.fitted[key]

    def set_playing(self, path: Path | None) -> None:
        """Mark the song being played (None when nothing plays).  A new song starts with its lyre at rest."""
        if path != self.playing:
            self.playing = path
            self.struck = [-math.inf] * len(self.struck)
            if self.ring_job:
                self.winfo_toplevel().after_cancel(self.ring_job)
                self.ring_job = None
            self.lay_out()

    def edge_of(self, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        """The orange edge at the left of the song in ``box``, as it stands for the chosen song: on whole
        pixels, so that the strings of the song being played start where it ends and end where its twin
        starts, at any scale."""
        left, top, _, bottom = box
        x = math.floor(left + self.edge / 2)
        return x, round(top) + self.theme.px(6), x + self.edge, round(bottom) - self.theme.px(6)

    def draw_strings(self, box: tuple[float, float, float, float]) -> float:
        """The mark of the song being played, in ``box``: the chosen song's orange edge with another like it
        to its right, three strings stretched between them as they ring now, high on top and low at the
        foot, like a wide H; and where the song's name begins after it."""
        x0, y0, x1, y1 = self.edge_of(box)
        far = x0 + self.mark_width - self.edge
        for x in (x0, far):
            self.create_rectangle(x, y0, x + self.edge, y1, fill=ORANGE, width=0, tags=("content", "posts"))
        middle, now = math.floor((y0 + y1 - 1) / 2), time.monotonic()  # the middle row of the edge's pixels
        for row in range(len(self.struck)):  # on whole pixels, so that the strings stay sharp
            self.create_image(x1, middle + self.string_gap * (1 - row) - 1, anchor="nw",
                              image=self.string(row, now), tags=("content", "string", f"string{row}"))
        return far + self.edge + self.theme.px(5)

    def shade(self, row: int, now: float) -> int:
        """How far string ``row`` is from gold towards orange now, 0 to ``SHADES``: all the way when just
        struck, and back to 0 as it fades."""
        return round(SHADES * math.exp(-(now - self.struck[row]) / FADE))

    def string(self, row: int, now: float) -> tk.PhotoImage:
        """The picture of string ``row`` as it rings now: in its shade, and swelling and settling by turns
        while it shivers."""
        age, length = now - self.struck[row], self.string_length
        swell = SWELLS[int(now * 1000 / RING_STEP) % len(SWELLS)] if age < SHIVER else 0.0
        color = icons.blend(GOLD, ORANGE_DEEP, self.shade(row, now) / SHADES)
        return self.theme.image(f"string:{color}:{swell}", length, 3,
                                lambda: [(color, icons.string(length, swell))])

    def strike(self, rows: set[int]) -> None:
        """Keys of these rows (0 low, 1 middle, 2 high) were just pressed in the game: their strings light."""
        now = time.monotonic()
        for row in rows:
            self.struck[row] = now
        self.ring()

    def ring(self) -> None:
        """Show the strings as they ring now, and look again shortly while any of them still does."""
        if self.ring_job:
            self.winfo_toplevel().after_cancel(self.ring_job)
            self.ring_job = None
        now = time.monotonic()
        for row in range(len(self.struck)):
            for item in self.find_withtag(f"string{row}"):
                self.itemconfigure(item, image=self.string(row, now))
        if any(self.shade(row, now) for row in range(len(self.struck))):
            # Timed on the window, as all of LyreBot's timers are, so closing it cancels them together.
            self.ring_job = self.winfo_toplevel().after(RING_STEP, self.ring)

    def cell_of(self, path: Path) -> Cell | None:
        return next((cell for cell in self.cells if cell.path == path), None)

    def mark_chosen(self) -> None:
        cell = self.cell_of(self.chosen) if self.chosen else None
        if cell is None:
            self.itemconfigure(self.chosen_box, state="hidden")
            self.itemconfigure(self.chosen_edge, state="hidden")
            return
        self.ground(self.chosen_box, CHOSEN_TINT, cell.box)
        self.coords(self.chosen_edge, *self.edge_of(cell.box))
        self.itemconfigure(self.chosen_edge, state="normal")

    def visible_songs(self) -> list[Path]:
        return [cell.path for cell in self.cells if cell.path is not None]

    def choose(self, path: Path | None, reveal: bool = True) -> None:
        """Select ``path``; with ``reveal``, open what it is in and scroll to it."""
        self.chosen = path
        if reveal and path is not None and self.library is not None:
            region = self.library.region_of(path)
            shelf = OST if region else SONGS if path in self.library.songs else None
            if shelf in self.closed_shelves or (region and region not in self.open_regions):
                self.closed_shelves.discard(shelf)
                if region:
                    self.open_regions.add(region)
                self.lay_out()
        self.mark_chosen()
        cell = self.cell_of(path) if reveal and path else None
        if cell is not None and self.total:
            view = self.winfo_height()
            top = self.canvasy(0)
            if cell.box[1] < top:
                self.yview_moveto(cell.box[1] / self.total)
            elif cell.box[3] > top + view:
                self.yview_moveto(max(cell.box[3] - view, 0) / self.total)

    def cell_at(self, event: tk.Event) -> Cell | None:
        x, y = self.canvasx(event.x), self.canvasy(event.y)
        return next((cell for cell in self.cells
                     if cell.box[0] <= x < cell.box[2] and cell.box[1] <= y < cell.box[3]), None)

    def toggle(self, cell: Cell) -> None:
        folders = self.closed_shelves if cell.kind == "shelf" else self.open_regions
        (folders.discard if cell.name in folders else folders.add)(cell.name)
        self.lay_out()

    def click(self, event: tk.Event) -> str:
        self.focus_set()
        cell = self.cell_at(event)
        if cell is None or self.locked():
            return "break"
        if cell.path is None:
            self.toggle(cell)
        else:
            self.choose(cell.path, reveal=False)
            self.on_select()
        return "break"

    def double_click(self, event: tk.Event) -> str:
        cell = self.cell_at(event)
        if cell is None or self.locked():
            return "break"
        if cell.path is None:  # the second click of a quick pair toggles it again
            self.toggle(cell)
        else:
            self.on_play()
        return "break"

    def hover(self, event: tk.Event) -> None:
        cell = self.cell_at(event)
        if cell is None:
            self.itemconfigure(self.hover_box, state="hidden")
        else:
            self.ground(self.hover_box, HOVER_TINT, cell.box)

    def step(self, by: int) -> str:
        songs = self.visible_songs()
        if songs and not self.locked():
            place = songs.index(self.chosen) if self.chosen in songs else (-1 if by > 0 else len(songs))
            self.choose(songs[min(max(place + by, 0), len(songs) - 1)])
            self.on_select()
        return "break"

    def wheel(self, event: tk.Event) -> None:
        if self.total > self.winfo_height():
            self.yview_scroll(-round(event.delta / 120) * 2, "units")


class App:
    def __init__(self, root: tk.Tk, scores_dir: Path = SCORES_DIR, midi_dir: Path = MIDI_DIR,
                 tool_dirs: list[Path] | None = None, jianpu_dir: Path = JIANPU_DIR, own_frame: bool = False,
                 place: tuple[int, int, int, int] | None = None, settings: Path | None = None):
        """``place`` is where the window was left (see ``read_place``); ``settings`` the file the instrument
        songs are heard on is kept in (see ``read_timbre``), if it is to be kept."""
        self.root = root
        self.place = place
        self.settings = settings
        self.scores_dir = scores_dir
        self.midi_dir = midi_dir
        self.jianpu_dir = jianpu_dir
        self.tool_dirs = TOOL_DIRS if tool_dirs is None else tool_dirs
        self.failed_midi: dict[Path, float] = {}  # MIDI files that could not be arranged, by change time
        self.failed_audio: dict[Path, float] = {}  # audio files that could not be transcribed, likewise
        self.songs: list[Path] = []
        self.library: Library | None = None
        self.bad: frozenset[Path] = frozenset()
        self.held_mode: str | None = None  # the play mode to go back to after an unfiled song
        self.last_search = ""  # searching for it again goes on to the next song that matches
        self.resume: tuple[Path, int] | None = None  # a song stopped part way, and the bar it stopped in
        self.shuffle = Shuffle()  # what 随机演奏 has played this round
        self.dragging: tuple[Path, Score] | None = None  # the song whose progress knob is held
        self.drag_bar = 0  # the bar (from 0) the held knob is at
        self.bar_starts: list[float] = []  # of the song playing, in seconds
        self.start_held = False  # F7, at the last look
        self.menu_folder = scores_dir
        self.told_empty = False
        # A note such as "just arranged" kept in front of a song's details while it stays selected:
        # selecting a row also fires <<TreeviewSelect>> later, which redraws the details.
        self.notice: tuple[Path, str] | None = None
        self.cache: dict[Path, tuple[float, Score | ScoreError]] = {}
        self.worker: threading.Thread | None = None  # playing or recording
        self.recording = False
        self.listening = False
        self.auditioning = False  # hearing a song on the computer (``audition``)
        self.timbre = read_timbre(settings) if settings else PIANO  # the General MIDI instrument it is heard on
        self.on_top = bool(settings) and read_on_top(settings)  # kept over all other windows (the title bar's pin)
        self.synth: Audition | None = None  # the synthesizer open while a song is heard
        self.leaping = threading.Event()  # set when a note is clicked while hearing: go on from ``leap_to``
        self.leap_to = 0.0  # seconds into the song
        self.hearing = 0  # counts the stretches heard, so that progress from one cut short is not shown
        self.leaf = False  # the 简谱 page is shown, not the songs
        self.where = "songs"  # the page shown: "songs", "leaf" (the 简谱) or "text" (the score's text)
        self.text_path: Path | None = None  # on the text page: the file, as it was saved, whether it began with
        self.text_saved = ""  # a byte order mark, the mistake found in it (None: none), the score it reads as
        self.text_bom = False
        self.text_error: str | None = None
        self.text_score: Score | None = None
        self.text_job: str | None = None
        self.text_kept = False  # saved since it was last changed
        self.guide: tk.Toplevel | None = None  # the format's card, while it is open (``toggle_guide``)
        self.quickstart: tk.Toplevel | None = None  # the 快速开始 card, while it is open (``toggle_quickstart``)
        self.leaf_song: tuple[Path, float, Score, object, float] | None = None  # on it: file, its time, score,
        self.leaf_from: float | None = None  # timeline, speed; and where ▶ goes on from (seconds), if stopped
        self.leaf_bars: list[float] = []  # when its bars begin, in seconds at that speed
        self.leaves: dict[tuple[Path, float | None], tuple] = {}  # songs read for it lately: (score, 简谱 sheet)
        self.ribbon_inside = False
        self.grown = False  # the window made larger from the 简谱 page (``toggle_size``)
        self.tugging: int | None = None  # the frame of the ribbon's tug being shown, while it is tugged
        self.tug_job: str | None = None
        self.cursors: dict[str, str] = {}  # what a piece's pointer was before the ribbon made it a hand
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
        self.frame: chrome.Frame | None = None  # LyreBot's own frame, once Windows lends it (``take_frame``)
        self.frame_presses = 0  # and of presses on its title bar and edges, as last seen
        self.title_bar: TitleBar | None = None
        root.title(self.base_title)
        root.configure(bg=PAPER)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F8>", lambda _: self.stop())
        # A press anywhere in the window but on the search line takes the keyboard from it (the window's
        # own binding sees its pieces' presses; one on the title bar or an edge is counted by the frame).
        root.bind("<ButtonPress>", self.click_away, add="+")
        self.speed = tk.StringVar(master=root, value="100%")
        self.mode = tk.StringVar(master=root, value=SINGLE)
        self.timbre_name = tk.StringVar(master=root, value=dict(INSTRUMENTS)[self.timbre])

        self.build()
        self.refresh()
        self.show_selected()  # nothing is chosen yet: it asks for a song
        if not (own_frame and self.take_frame(self.panel)):
            dress(root)
        root.after(30, self.poll)
        root.after(300, self.arrange_next)
        root.after(600, self.transcribe_next)
        root.after(2000, self.watch)
        root.after(HOTKEY_POLL, self.watch_start_key)

    def build(self, size: tuple[int, int] | None = None) -> None:
        """Make the window's pieces at the present scaling (``tk scaling``), for a window of ``size``."""
        root = self.root
        self.theme = theme = Theme(root)
        self.scale = theme.scale
        self.dpi = round(theme.scale * 96)
        px, fonts = theme.px, theme.fonts
        width, height = size or (px(WINDOW_WIDTH), px(OPENING_HEIGHT))
        if size is None:
            root.geometry(f"{width}x{height}")  # opens as narrow as it may be
        root.minsize(px(WINDOW_WIDTH), px(360))
        # The page under it all (``paper``): lamplight at the head, under the title bar, and evening at the
        # foot, over the player.
        theme.page = self.page = Page(root, flat=px(110))
        self.page.lay(width, height)
        self.backdrop = Sheet(root, theme)  # the paper between the pieces
        self.backdrop.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lower(self.backdrop)  # under every other piece (a canvas's own lower() moves its items)
        self.bar_hover = False
        self.breath = False
        self.breath_job: str | None = None
        self.build_images()

        # ---- search ----
        # Over the player's ornamental rule, in two parts: the lyre at the left (it moves up into the title
        # bar when LyreBot draws one), and the search field filling the right third, written on the page
        # (no hint: the magnifier says what it is for).  Enter searches; there is no button for it.
        finder = Sheet(root, theme)
        for column, share in ((0, 2), (1, 1)):
            finder.columnconfigure(column, weight=share, uniform="thirds")
        self.crest = Picture(finder, theme, theme.icon("lyre", px(20), GOLD, icons.lyre))
        self.crest.grid(row=0, column=0, sticky="w")
        self.search_field = PageLine(finder, theme, icon=icons.magnifier)
        self.search_field.grid(row=0, column=1, sticky="ew")
        self.search_entry = self.search_field.entry
        self.search_entry.bind("<Return>", lambda _: self.search())

        # ---- song list ----
        top = Sheet(root, theme)
        self.browser = SongBrowser(top, theme, self.locked, self.show_selected, self.start, self.delete)
        self.browser.pack(side="left", fill="both", expand=True)  # (scrolled by the wheel and the keys: no bar)
        self.browser.bind("<Button-3>", self.context_menu)
        root.bind("<MouseWheel>", self.wheel)
        self.song_menu = PopupMenu(root, theme)  # for a song
        self.move_menu = PopupMenu(root, theme)
        self.song_menu.add_cascade(label="移到…", menu=self.move_menu, icon=icons.move_to)
        self.song_menu.add_separator()
        self.song_menu.add_command(label="从 MIDI 重新编配", command=self.rearrange, icon=icons.quill)
        self.song_menu.add_command(label="导出简谱", command=self.export_jianpu, icon=icons.score_page)
        self.song_menu.add_separator()
        self.song_menu.add_command(label="删除", accelerator="Delete", command=self.delete, icon=icons.trash,
                                   danger=True)
        self.folder_menu = PopupMenu(root, theme)  # for OST, 歌曲 or a region
        self.folder_menu.add_command(label="", command=lambda: self.open_folder(self.menu_folder), icon=icons.folder)

        rule = Ornament(root, theme)

        # ---- now playing ----
        panel = Sheet(root, theme)
        heading = Sheet(panel, theme)
        heading.pack(fill="x")
        self.time_label = Words(heading, theme, fg=MUTED, font=fonts.figures)
        self.time_label.pack(side="right", anchor="s", pady=(0, px(2)))
        self.bar_label = Words(heading, theme, fg=MUTED, font=fonts.figures)  # 2|20: bar 2 of 20
        self.bar_label.pack(side="right", anchor="s", padx=(0, px(12)), pady=(0, px(2)))
        self.title_label = Words(heading, theme, fg=INK, font=fonts.title, anchor="w")
        self.title_label.pack(side="left", fill="x", expand=True)

        self.bar_height = px(14)  # the clickable band; the drawn track is thinner
        self.bar = Sheet(panel, theme, height=self.bar_height, cursor="hand2")
        self.bar.pack(fill="x", pady=(px(4), 0))
        self.bar.bind("<Configure>", lambda _: self.draw_bar(), add="+")
        self.bar.bind("<ButtonPress-1>", self.pick_up)
        self.bar.bind("<B1-Motion>", self.slide)
        self.bar.bind("<ButtonRelease-1>", self.let_go)
        self.bar.bind("<Enter>", lambda _: self.hover_bar(True))
        self.bar.bind("<Leave>", lambda _: self.hover_bar(False))

        self.status_label = Words(panel, theme, fg=MUTED, font=fonts.small, anchor="w", justify="left")
        self.status_label.pack(fill="x", pady=(px(2), 0))
        panel.bind("<Configure>", lambda event: self.status_label.configure(wraplength=event.width), add="+")

        controls = Sheet(panel, theme)
        controls.pack(fill="x", pady=(px(8), 0))
        self.speed_choice = Choice(controls, theme, self.speed, SPEEDS, self.show_selected,
                                   ((CUSTOM_SPEED, self.ask_speed, icons.pen_nib),))
        self.speed_choice.pack(side="left")
        self.mode_choice = Choice(controls, theme, self.mode, MODES, self.mode_picked)
        self.mode_choice.pack(side="left", padx=(px(6), 0))
        # While a song plays these stay disabled: clicking LyreBot already stops the song
        # (the game loses focus), and a live button would start it again on release.
        self.next_button = IconButton(controls, self.next_images, self.play_next, "下一首", theme)
        self.next_button.pack(side="right")
        self.button = IconButton(controls, self.play_images, self.start, "播放（F7 立刻开始）", theme)
        self.button.pack(side="right", padx=(0, px(6)))
        self.restart_button = IconButton(controls, self.restart_images, self.restart, "从头开始", theme)
        self.restart_button.pack(side="right", padx=(0, px(6)))  # as far from ▶ as ⏭, on the other side
        self.record_button = IconButton(controls, self.record_images, self.record, "录音", theme)
        self.record_button.pack(side="right", padx=(0, px(4)))
        self.keys_button = IconButton(controls, self.keys_images, self.listen, "记录演奏", theme)
        self.keys_button.pack(side="right", padx=(0, px(2)))

        # From the foot up: the player, its rule and the search row go in first, so that a short window
        # takes its room from the list, which has the top.
        self.songs_page = [(panel, dict(side="bottom", fill="x", padx=px(12), pady=(px(6), px(12)))),
                           (rule, dict(side="bottom", fill="x", padx=px(14), pady=(px(6), 0))),
                           (finder, dict(side="bottom", fill="x", padx=px(16), pady=(px(6), 0))),
                           (top, dict(fill="both", expand=True, padx=px(8), pady=(px(8), 0)))]
        for piece, how in self.songs_page:
            piece.pack(**how)
        self.panel = panel
        self.build_leaf()
        self.build_text()
        self.pages = {"songs": self.songs_page, "leaf": self.leaf_page, "text": self.text_page}
        # The ribbons are bookmarks: a click on one turns the page (the pieces they lie over hear of it first).
        self.ribbon_image = theme.ribbon()
        self.green_image = theme.ribbon(green=True)
        self.ribbon_tag = f"Ribbon{id(self)}"
        for event, handler in (("<ButtonPress-1>", self.ribbon_press), ("<Double-Button-1>", self.ribbon_press),
                               ("<Motion>", self.ribbon_motion), ("<Leave>", self.ribbon_leave)):
            root.bind_class(self.ribbon_tag, event, handler)
        for piece in (self.backdrop, top, self.browser, *self.leaf_pieces(), self.text_head, self.text_title,
                      self.text_about, self.manuscript):
            piece.bindtags((self.ribbon_tag, *piece.bindtags()))
        self.callout = Callout(root, theme)
        self.set_away([piece for piece, _ in self.leaf_page + self.text_page], True)  # (the songs come first)
        self.dress_page()
        if self.frame is not None:  # drawn again: the title bar with the rest
            chrome.stand_aside(self.frame, (self.backdrop, self.make_title_bar(panel)))

    def dress_page(self) -> None:
        """What the page carries besides the writing: a double rule of gold pressed in all round it, as on an
        old book's page, its corners rounded; the lyre pressed faintly into the paper in the middle of the
        window; and the program icon's orange ribbon hanging from under the leather over the list, as a
        bookmark hangs from a book's head."""
        theme, page, px = self.theme, self.page, self.theme.px
        side, depth, frame = theme.frame()

        def frame_at(name: str):
            def place(width: int, height: int) -> tuple[int, ...]:
                head = self.head()
                tall = height - head - 2 * side  # the sides' straight run, between their corners
                return {"nw": (0, head), "ne": (width - side, head), "sw": (0, height - side),
                        "se": (width - side, height - side), "n": (side, head, width - 2 * side, depth),
                        "s": (side, height - depth, width - 2 * side, depth), "w": (0, head + side, depth, tall),
                        "e": (width - depth, head + side, depth, tall)}[name]
            return place
        for name, piece in frame.items():
            page.press(piece, frame_at(name))
        mark = theme.watermark(px(WATERMARK_SIZE))
        page.press(mark, lambda width, height: ((width - mark.width()) // 2, (height - mark.height()) // 2))
        ribbon = theme.ribbon()
        self.ribbon_place = {"orange": lambda width, height: (width - px(RIBBON_FROM_RIGHT), self.head()),
                             "green": lambda width, height: (width - px(GREEN_FROM_RIGHT), self.head())}
        self.hung: dict[str, tk.PhotoImage | None] = {"orange": ribbon, "green": None}  # (green on pages 2, 3)
        page.hang(ribbon, self.ribbon_place["orange"])

    def head(self) -> int:
        """Where the page begins: under LyreBot's own title bar, where it draws one."""
        bar = self.title_bar
        if bar is None:
            return 0
        return bar.winfo_y() + bar.winfo_height() if bar.winfo_ismapped() else bar.winfo_reqheight()

    def make_title_bar(self, first: tk.Widget) -> TitleBar:
        """The leather title bar, over ``first``, laid out for ``self.frame``; the lyre moves up into it."""
        assert self.frame is not None
        self.frame.edge = self.theme.px(5)
        bar = TitleBar(self.root, self.theme, self.frame, "LyreBot", "" if is_admin() else "未以管理员身份运行",
                       self.frame.minimise, self.close, start=self.toggle_quickstart,
                       pin=lambda: self.set_on_top(not self.on_top), pinned=self.on_top)
        bar.pack(side="top", fill="x", before=first)
        self.crest.grid_remove()
        self.title_bar = bar
        return bar

    def take_frame(self, first: tk.Widget) -> bool:
        """Draw the title bar in LyreBot's own leather and gilt instead of Windows' (see ``chrome``).  The
        window opens where it was left, or else in the middle of the screen the pointer is on."""
        if not chrome.available():
            return False
        root, px = self.root, self.theme.px
        self.frame = chrome.Frame(edge=px(5), caption=0)
        bar = self.make_title_bar(first)
        if self.place is not None:
            root.geometry(geometry(*self.place))
        else:
            width, height = px(WINDOW_WIDTH), px(OPENING_HEIGHT)
            left, top, right, bottom = screen_area(root, *root.winfo_pointerxy())
            root.geometry(geometry(left + max(0, right - left - width) // 2,
                                   top + max(0, bottom - top - height) // 2, width, height))
        if chrome.adopt(root, self.frame, see_through=(root, self.backdrop, bar)):
            round_corners(root, border=None)  # rounded, with the system's shadow, and no outline
            root.lyrebot_frame = self.frame  # type: ignore[attr-defined]  # (its dialogs dress to match)
            if self.on_top:  # as it was left
                self.set_on_top(True, keep=False)
            return True
        bar.destroy()
        self.frame = self.title_bar = None
        self.crest.grid()
        return False

    def set_on_top(self, on: bool, keep: bool = True) -> None:
        """The title bar's pin: keep the window over all the others, the game's too, or let it go back among
        them; and, with ``keep``, open it so next time.  Its dialogs, cards, menus and tips stay over it."""
        self.on_top = on
        try:
            self.root.attributes("-topmost", on)
        except tk.TclError:
            pass
        for card in (self.quickstart, self.guide):  # open beside it, and over it
            if card is not None and card.winfo_exists():
                dialogs.keep_over(self.root, card)
        if self.title_bar is not None:
            self.title_bar.show_pinned(on)
        if keep and self.settings:
            write_setting("on_top", on, self.settings)

    def keep_place(self) -> None:
        """Write down where the window is, for it to open there next time (see ``read_place``)."""
        if self.frame is None or not self.frame.hwnd:
            return
        box = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(self.frame.hwnd, ctypes.byref(box)):
            write_place((box.left, box.top), self.dpi)

    def watch_start_key(self) -> None:
        """F7 plays the chosen song at once, in whatever window is in front, as ▶ would."""
        if self.closing:
            return
        held = start_pressed()
        if held and not self.start_held and not self.worker and self.selected() is not None:
            self.start(countdown=0.0)
        self.start_held = held
        self.root.after(HOTKEY_POLL, self.watch_start_key)

    def build_images(self) -> None:
        """The buttons' faces: ▶ an orange disc set in a gold ring, ● an orange dot in a gold one, the rest
        drawn in fine ink lines with their gilt in gold."""
        theme, px = self.theme, self.theme.px
        big, small = px(34), px(26)
        c, s = big / 2, small / 2
        line = max(1, round(self.scale))  # whole pixels, so rings stay sharp

        def picture(name: str, size: int, layers: Callable[[], list]) -> tk.PhotoImage:
            return theme.image(name, size, size, layers)

        def face(disc: str, edge: str, glyph: Callable[[int], icons.Shape]) -> Callable[[], list]:
            return lambda: [(edge, icons.ring(c, c, c - 0.5 - line / 2, line)),
                            (disc, icons.circle(c, c, c - 3.2 * line)), (WHITE, glyph(big))]
        self.play_images = {"normal": picture("play-normal", big, face(ORANGE, GOLD, icons.play)),
                            "hover": picture("play-hover", big, face(ORANGE_DEEP, GOLD, icons.play)),
                            "disabled": picture("play-disabled", big, face(PEACH, HAIR, icons.play))}
        stop = picture("play-stop", big, face(PEACH, HAIR, icons.stop))
        self.stop_images = {"normal": stop, "hover": stop, "disabled": stop}
        # While a song is heard on the 简谱 page LyreBot stays in front, so its ■ is live, in ▶'s colours.
        self.halt_images = {"normal": picture("halt-normal", big, face(ORANGE, GOLD, icons.stop)),
                            "hover": picture("halt-hover", big, face(ORANGE_DEEP, GOLD, icons.stop)),
                            "disabled": stop}

        def halo(color: str = HOVER_TINT) -> tuple[str, icons.Shape]:  # a shade over the page
            return color, icons.circle(s, s, s - 0.5)

        def plain(name: str, draw: Callable[[int], list]) -> dict[str, tk.PhotoImage]:
            return {"normal": picture(f"{name}-normal", small, lambda: icons.paint(draw(small), INK, GOLD)),
                    "hover": picture(f"{name}-hover", small, lambda: [halo(), *icons.paint(draw(small), INK, GOLD)]),
                    "disabled": picture(f"{name}-disabled", small, lambda: icons.paint(draw(small), FAINT))}
        self.next_images = plain("next", icons.next_track)
        self.grow_images = plain("grow", icons.corners_out)  # the 简谱 page's window, larger
        self.undo_images = plain("undo", icons.undo)  # the text page's
        self.save_images = plain("save", icons.seal)
        self.guide_images = plain("guide", icons.question)
        self.shrink_images = plain("shrink", icons.corners_in)  # and back to the narrow one
        self.restart_images = plain("restart", icons.restart)
        self.keys_images = plain("keys", icons.keyboard)

        def dot(edge: str, color: str, ground: tuple | None = None) -> Callable[[], list]:
            def layers() -> list:
                ring, middle = icons.record(small)
                return [*([ground] if ground else []), (edge, ring), (color, middle)]
            return layers
        self.record_images = {"normal": picture("record-normal", small, dot(GOLD_SOFT, ORANGE)),
                              "hover": picture("record-hover", small, dot(GOLD, ORANGE_DEEP, halo())),
                              "disabled": picture("record-disabled", small, dot(HAIR, PEACH))}

        def square(ground: str, edge: str, color: str) -> Callable[[], list]:
            def layers() -> list:
                ring, _ = icons.record(small)
                return [halo(ground), (edge, ring), (color, icons.stop(small))]
            return layers
        self.recording_images = {"normal": picture("recording-in", small, square(CHOSEN_TINT, ORANGE, ORANGE)),
                                 "hover": picture("recording-hover", small, square(PEACH, ORANGE_DEEP, ORANGE_DEEP)),
                                 "disabled": picture("recording-disabled", small,
                                                     lambda: [(FAINT, icons.stop(small))])}
        # The other breath of a recording: the ground and ring a shade lighter.
        self.recording_breath = {**self.recording_images,
                                 "normal": picture("recording-out", small, square(HOVER_TINT, PEACH, ORANGE))}
        knob = lambda size: lambda: [(ORANGE, icons.circle(size / 2, size / 2, size / 2 - 0.5)),  # noqa: E731
                                     (PAPER, icons.circle(size / 2, size / 2, size / 2 - 0.5 - 2 * line))]
        self.knob_images = {False: picture("knob", px(10), knob(px(10))),
                            True: picture("knob-hover", px(13), knob(px(13)))}

    def breathe(self) -> None:
        """While recording or listening, the ■ slowly breathes."""
        if not (self.recording or self.listening):
            self.breath_job = None
            return
        button = self.record_button if self.recording else self.keys_button
        self.breath = not self.breath
        button.set(images=self.recording_breath if self.breath else self.recording_images)
        self.breath_job = self.root.after(700, self.breathe)

    def watch(self) -> None:
        """Pick up scores, MIDI and audio files added, removed or edited while the window is open."""
        self.refresh()
        self.arrange_next()
        self.transcribe_next()
        if self.leaf and not self.auditioning:
            self.read_leaf()
        self.root.after(2000, self.watch)

    # ---- small view helpers ----
    def locked(self, _: tk.Event | None = None) -> str | None:
        return "break" if self.worker else None

    def say(self, text: str, warn: bool = False) -> None:
        self.status_label.configure(text=text, fg=WARN if warn else MUTED)

    def track(self) -> tuple[float, float]:
        """Where the progress track starts and ends on its canvas: inside half a knob at each end."""
        reach = self.theme.px(13) / 2
        width = self.bar.winfo_width()
        return reach, max(width - reach, reach + 1)

    def draw_bar(self) -> None:
        bar, middle = self.bar, self.bar_height / 2
        bar.delete("!paper")
        left, right = self.track()
        thick = self.theme.px(2)
        bar.create_line(left, middle, right, middle, width=thick, fill=HAIR, capstyle="round")
        fraction = min(max(self.fraction, 0.0), 1.0)
        at = left + (right - left) * fraction
        if fraction > 0:
            bar.create_line(left, middle, at, middle, width=thick, fill=ORANGE, capstyle="round")
        browser = getattr(self, "browser", None)
        if self.worker is not None or (browser is not None and self.selected() is not None):
            bar.create_image(at, middle, image=self.knob_images[self.bar_hover])

    def hover_bar(self, inside: bool) -> None:
        self.bar_hover = (inside or self.dragging is not None) and self.worker is None
        self.draw_bar()

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
        library = read_library(self.scores_dir)
        songs = library.everything
        bad = frozenset(path for path in songs if isinstance(self.read(path), ScoreError))
        if library != self.library or bad != self.bad:
            self.library, self.songs, self.bad = library, songs, bad
            self.browser.show(library, bad)
            self.browser.choose(chosen if chosen in songs else None, reveal=False)
            self.show_selected()
            self.sync_mode(False)
            self.told_empty = False
        if not songs and not self.told_empty:  # once, so it does not cover later messages
            self.told_empty = True
            self.title_label.configure(text="还没有曲谱")
            self.time_label.configure(text="")
            self.bar_label.configure(text="")
            self.say(EMPTY)

    def selected(self) -> Path | None:
        chosen = self.browser.chosen
        return chosen if chosen in self.songs else None

    def select(self, path: Path, reveal: bool = True) -> None:
        if path not in self.songs:
            return
        self.browser.choose(path, reveal)
        self.show_selected()

    def search(self) -> None:
        """Go to the first song whose name holds what was typed; searching again for it goes to the next."""
        if self.worker:
            return
        query = self.search_entry.get().strip()
        if not query:
            self.search_entry.focus_set()
            return
        again = query.casefold() == self.last_search.casefold()
        self.last_search = query
        found = next_match(self.songs, query, self.selected() if again else None)
        if found is None:
            self.say(f"没有名字里有「{query}」的曲子")
            return
        self.select(found)
        matches = [path for path in self.songs if query.casefold() in path.stem.casefold()]
        self.say(f"「{query}」第 {matches.index(found) + 1} 首，共 {len(matches)} 首，再按回车看下一首"
                 if len(matches) > 1 else f"「{query}」只有这一首")

    def playlist(self, path: Path | None) -> list[Path]:
        return self.library.playlist(path) if self.library else []

    def sync_mode(self, busy: bool) -> None:
        """An unfiled song is in no playlist: it plays once or over again, with no next song
        to go to.  With no song chosen there is nothing to play."""
        chosen = self.selected()
        if not busy:
            self.button.set(enabled=chosen is not None)
            self.restart_button.set(enabled=chosen is not None)
        alone = chosen is not None and not self.playlist(chosen)
        if alone and self.held_mode is None and self.mode.get() not in ON_ITS_OWN:
            self.held_mode = self.mode.get()
            self.mode.set(SINGLE)
        elif not alone and self.held_mode is not None:
            self.mode.set(self.held_mode)
            self.held_mode = None
        self.mode_choice.allow(ON_ITS_OWN if alone else None)
        self.mode_choice.set_enabled(not busy)
        self.next_button.set(enabled=not busy and chosen is not None and not alone)

    def mode_picked(self) -> None:
        self.held_mode = None  # chosen for this song: kept when a song of a playlist comes next

    def speed_factor(self) -> float:
        return float(self.speed.get().rstrip("%")) / 100

    def ask_speed(self) -> None:
        limits = f"{SLOWEST_SPEED:g} 到 {FASTEST_SPEED:g}"
        prompt = f"演奏速度是原速的百分之几（{limits}）："
        answer = self.speed.get().rstrip("%")
        while True:
            answer = dialogs.ask_text("自定义速度", prompt, initialvalue=answer, parent=self.root)
            if answer is None:
                return
            percent = parse_speed(answer)
            if percent is not None:
                self.speed.set(f"{percent:g}%")
                self.show_selected()
                return
            prompt = f"速度请填 {limits} 之间的数字："

    def show_selected(self) -> None:
        if self.worker:
            return
        path = self.selected()
        self.sync_mode(False)
        if path is None:
            if self.songs:
                self.title_label.configure(text="选择乐曲")
                self.time_label.configure(text="")
                self.bar_label.configure(text="")
                self.set_fraction(0.0)
            return
        score = self.read(path)
        self.title_label.configure(text=path.stem)
        self.set_fraction(0.0)
        if self.notice and self.notice[0] != path:
            self.notice = None
        if self.resume and self.resume[0] != path:
            self.resume = None  # another song chosen: the next one starts from its top
        if isinstance(score, ScoreError):
            self.time_label.configure(text="")
            self.bar_label.configure(text="")
            self.say(f"曲谱有误：{score}", warn=True)
            return
        self.time_label.configure(text=clock(score.duration_seconds / self.speed_factor()))
        self.show_bar(self.resume[1] if self.resume else 0, score)  # where it would start
        if self.resume:
            bar = self.resume[1]
            if score.duration_seconds:
                self.set_fraction(bar_seconds(score, bar, 1.0) / score.duration_seconds)
            self.say(GO_ON)
            return
        self.say(self.notice[1] if self.notice else READY)

    def open_folder(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # type: ignore[attr-defined]

    def context_menu(self, event: tk.Event) -> None:
        """A song's menu on a song; on OST, 歌曲 or a region, one to open its folder; nothing elsewhere."""
        cell = self.browser.cell_at(event)
        if cell is None:
            return
        if cell.path is None:
            self.menu_folder = self.scores_dir / (cell.name if cell.kind == "shelf" else Path(OST, cell.name))
            self.folder_menu.entryconfigure(0, label=f"打开「{cell.name}」文件夹")
            self.folder_menu.tk_popup(event.x_root, event.y_root)
            return
        if not self.worker:
            self.select(cell.path, reveal=False)
        chosen = self.selected()
        if chosen is None:
            return
        self.fill_move_menu(chosen)
        idle = "normal" if not self.worker else "disabled"
        can = not self.worker and midi_for(chosen, self.midi_dir) is not None
        self.song_menu.entryconfigure(0, state=idle)
        self.song_menu.entryconfigure(2, state="normal" if can else "disabled")
        self.song_menu.entryconfigure(5, state=idle)
        self.song_menu.tk_popup(event.x_root, event.y_root)

    def places(self) -> list[tuple[str, Path] | None]:
        """Where a score can be moved to, by name, with None between the groups."""
        regions = [(name, self.scores_dir / OST / name) for name, _ in (self.library.regions if self.library else ())]
        return [(UNFILED, self.scores_dir), None, *regions, None, (SONGS, self.scores_dir / SONGS)]

    def fill_move_menu(self, score: Path) -> None:
        self.move_menu.delete(0, "end")
        here = str(score.parent).casefold()
        for place in self.places():
            if place is None:
                self.move_menu.add_separator()
                continue
            name, folder = place
            self.move_menu.add_command(label=name, command=lambda name=name, folder=folder: self.move(score, folder, name),
                                       state="disabled" if str(folder).casefold() == here else "normal")

    def move(self, score: Path, folder: Path, name: str) -> Path | None:
        """Move a score into another region, 歌曲 or unfiled, and keep it selected there."""
        if self.worker:
            return None
        target = folder / score.name
        if target.exists():
            dialogs.error("无法移动", f"「{name}」里已经有 {score.name}", parent=self.root)
            return None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            score.rename(target)
        except OSError as error:
            dialogs.error("无法移动", f"{score.name}：{error}", parent=self.root)
            return None
        self.cache.pop(score, None)
        self.refresh()
        self.notice = (target, f"已移到「{name}」")
        self.select(target)
        return target

    def shown(self, path: Path) -> str:
        """A file's name as the user sees it, from the LyreBot folder: txt\\OST\\曲名.txt."""
        try:
            return str(path.relative_to(self.scores_dir.parent))
        except ValueError:
            return str(path)

    def delete(self) -> None:
        """Move the selected score to the Recycle Bin, with the MIDI file and recording it was made
        from and the 简谱 exported from it."""
        score = self.selected()
        if score is None or self.worker:
            return
        page = self.jianpu_dir / f"{score.stem}.html"
        files = [path for path in (*song_files(score, self.midi_dir), page) if path.exists()]
        if score not in files:  # already gone
            self.refresh()
            return
        listed = "\n".join(self.shown(path) for path in files)
        question = f"删除「{score.stem}」？\n\n这些文件会移到回收站，需要时可以从回收站还原：\n\n{listed}"
        if not dialogs.confirm("删除", question, parent=self.root, yes="删除", danger=True):
            return
        index = self.songs.index(score) if score in self.songs else 0
        rest = [path for path in self.songs if path != score]
        # The score goes last: left without its MIDI file and recording it stays as it is,
        # but they, left without it, would make it again.
        for path in reversed(files):
            try:
                to_recycle_bin(path)
            except OSError as error:
                dialogs.error("无法删除", f"{self.shown(path)}：{error}", parent=self.root)
                self.refresh()
                return
            self.cache.pop(path, None)
            self.failed_midi.pop(path, None)
            self.failed_audio.pop(path, None)
        self.refresh()
        if rest:
            after = rest[min(index, len(rest) - 1)]
            self.notice = (after, f"已把「{score.stem}」移到回收站")
            self.select(after)

    def export_jianpu(self) -> Path | None:
        """Write the selected score as 简谱 into the jianpu_output folder and open it."""
        score = self.selected()
        if score is None:
            return None
        target = self.jianpu_dir / f"{score.stem}.html"
        try:
            export_jianpu(score, target)
        except (OSError, ScoreError) as error:
            dialogs.error("无法导出简谱", f"{score.name}：{error}", parent=self.root)
            return None
        self.say(f"简谱已存进 jianpu_output 文件夹：{target.name}，在浏览器里可以打印成 PDF")
        self.open_file(target)
        return target

    def open_file(self, path: Path) -> None:
        os.startfile(path)  # type: ignore[attr-defined]

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
        target = self.scores_dir / f"{score_name(source)}.txt"
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
        if not dialogs.confirm("从 MIDI 重新编配", question, parent=self.root, yes="重新编配"):
            return
        # The whole arrangement runs again, here: a few seconds for a long song.  Say so before it starts.
        self.say(f"正在重新编配 {source.name}…")
        self.root.update_idletasks()
        try:
            arrange_file(source, score)
        except (OSError, MidiError, ArrangeError) as error:
            self.say(f"{source.name} 无法编配：{error}", warn=True)
            dialogs.error("无法编配", f"{source.name}：{error}", parent=self.root)
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
            dialogs.info("还不能录音", NO_TOOLS, parent=self.root)
            return
        name = self.ask_name()
        if name is None:
            return
        self.midi_dir.mkdir(exist_ok=True)
        self.cancel = threading.Event()
        self.recording = True
        self.set_playing(False, recording=True)
        self.breathe()
        self.title_label.configure(text=name)
        self.time_label.configure(text="")
        self.bar_label.configure(text="")
        self.set_fraction(0.0)
        self.root.title(f"{name} · 录音 · LyreBot")
        self.say("等待声音：播放想编配的音乐，录完点 ■ 结束")
        self.worker = threading.Thread(target=self.run_recording, args=(self.midi_dir / f"{name}.wav",), daemon=True)
        self.worker.start()

    def ask_name(self, title: str = "录音", suffix: str = RECORDING_SUFFIX, verb: str = "录") -> str | None:
        prompt = f"要{verb}的是哪首曲子？曲谱会叫「曲名{suffix}」（不填就按时间起名）："
        answer = ""
        while True:
            answer = dialogs.ask_text(title, prompt, initialvalue=answer, parent=self.root)
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
        self.breathe()
        self.title_label.configure(text=name)
        self.time_label.configure(text="")
        self.bar_label.configure(text="")
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
            self.say(f"{source.name} 无法转录：没有找到转录工具，见 docs 文件夹里的「从录音生成曲谱-安装说明」", warn=True)
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
    def start(self, path: Path | None = None, bar: int | None = None, countdown: float | None = None,
              from_top: bool = False) -> None:
        """Play ``path`` (the chosen song): from the start of bar ``bar`` (from 0); else, if it was stopped
        part way, from the start of the bar it stopped in; else from the top."""
        # The click that pulls focus back to LyreBot stops the song; it must not start one too.
        if self.worker or time.monotonic() < self.quiet_until:
            return
        path = path or self.selected()
        if path is None:
            return
        if self.where == "text":  # (F7 pressed while writing): nothing plays
            return
        if self.leaf:  # (F7 pressed in the game): the songs' page shows what plays
            self.show_songs()
        self.cache.pop(path, None)  # always play the file as it is now
        score = self.read(path)
        if isinstance(score, ScoreError):
            dialogs.error("曲谱有误", f"{path.stem}：{score}", parent=self.root)
            self.refresh()
            return
        speed = self.speed_factor()
        timeline = compile_timeline(score, speed)
        begin = self.begin_at(path, score, speed, bar, from_top)
        self.duration = timeline.duration
        self.set_fraction(begin / timeline.duration if timeline.duration else 0.0)
        self.set_playing(True)
        args = (path, timeline, begin, speed, self.mode.get(), self.playlist(path), countdown)
        self.worker = threading.Thread(target=self.run, args=args, daemon=True)
        self.worker.start()

    def begin_at(self, path: Path, score: Score, speed: float, bar: int | None, from_top: bool = False) -> float:
        """Where ``path`` starts, in seconds at ``speed``: at bar ``bar`` (from 0); else, if it was stopped part
        way, at the start of the bar it stopped in; else at the top.  The song is chosen, and its place used."""
        if bar is not None:
            begin = bar_seconds(score, bar, speed)
        elif not from_top and self.resume and self.resume[0] == path:
            begin = bar_seconds(score, self.resume[1], speed)
        else:
            begin = 0.0
        self.resume = None
        self.select(path)
        self.cancel = threading.Event()
        return begin

    def press_hear(self) -> None:
        """The 简谱 page's ▶: hear the song from where it was stopped, else from the top; its ■ stops it."""
        if self.auditioning:
            self.stop()
        else:
            self.audition(self.leaf_from or 0.0)

    def hear_note(self, index: int) -> None:
        """A note clicked on the 简谱 page: hear the song from it, or, while it is heard, go on from it."""
        note = self.score_view.notes[index]
        if self.auditioning:
            self.leap_to = note.start
            self.hearing += 1  # what is still to come from before is not shown
            self.leaping.set()
            self.score_view.heard(note.at)
            self.show_heard(note.at)
            return
        self.audition(note.start)

    def audition(self, begin: float = 0.0) -> None:
        """Hear the song on the 简谱 page through the computer's speakers, once, from ``begin`` seconds in,
        at the speed chosen on the songs' page.  LyreBot stays in front; ■ or F8 stops it."""
        if self.worker or time.monotonic() < self.quiet_until or self.leaf_song is None:
            return
        path, _, score, timeline, speed = self.leaf_song
        self.leaping.clear()
        self.leaf_from = None
        self.auditioning = True
        self.set_playing(False, auditioning=True)
        self.leaf_status.configure(text="正在打开系统的 MIDI 合成器…", fg=MUTED)
        self.root.title(f"{path.stem} · 试听 · LyreBot")
        self.cancel = threading.Event()
        self.worker = threading.Thread(target=self.run_audition, args=(path, timeline, begin, speed), daemon=True)
        self.worker.start()

    def pick_timbre(self) -> None:
        """The instrument chosen on the 简谱 page: kept for next time, and heard at once if a song is playing."""
        self.timbre = dict((name, program) for program, name in INSTRUMENTS)[self.timbre_name.get()]
        if self.settings:
            write_timbre(self.timbre, self.settings)
        synth = self.synth
        if synth is not None:
            try:
                synth.instrument(self.timbre)
            except AuditionError:
                pass  # closing just then: the next one opens on it

    def run_audition(self, path: Path, timeline, begin: float, speed: float) -> None:
        post = self.updates.put
        try:
            while True:
                hearing = self.hearing
                reached = [begin]

                def progress(at: float) -> None:
                    reached[0] = at
                    post(("heard", (hearing, at)))
                program = self.timbre
                synth = self.synth = Audition(program, interrupt=self.leaping.is_set)
                if self.timbre != program:  # changed while it opened
                    synth.instrument(self.timbre)
                try:
                    reason = play(timeline, synth, synth.foreground(), self.cancel, progress, start=begin)
                finally:
                    self.synth = None
                if self.leaping.is_set() and not self.cancel.is_set():  # another note was clicked
                    self.leaping.clear()
                    begin = self.leap_to
                    continue
                post(("heard end", (reason, reached[0])))
                return
        except AuditionError as error:
            post(("audition failed", str(error)))
        except Exception as error:  # report anything to the user rather than dying silently
            post(("error", str(error)))
        finally:
            post(("done", None))

    def show_bar(self, bar: int, score: Score) -> None:
        """Show bar ``bar`` (from 0) of all the score's bars, as 2|20."""
        self.bar_label.configure(text=f"{bar + 1}|{len(score.bars)}" if score.bars else "")

    def restart(self) -> None:
        """↺: the chosen song from the top, whatever bar it stopped in."""
        self.start(from_top=True)

    def play_next(self) -> None:
        if self.worker:
            return
        songs = self.playlist(self.selected())
        if songs:
            self.start(next_song(songs, self.selected(), self.mode.get(), self.shuffle), from_top=True)

    def interrupted(self, path: Path, reached: float, speed: float, why: str) -> None:
        """Remember the bar a song was stopped in, to go on from there."""
        score = self.read(path)
        if isinstance(score, ScoreError) or path not in self.songs:
            self.say(why)
            return
        bar = bar_at(score, reached, speed)
        self.resume = (path, bar)
        self.set_fraction(bar_seconds(score, bar, 1.0) / score.duration_seconds if score.duration_seconds else 0.0)
        self.time_label.configure(text=f"{clock(bar_seconds(score, bar, speed))} / {clock(score.duration_seconds / speed)}")
        self.show_bar(bar, score)
        self.say(f"{why} · {GO_ON}")

    def bar_under(self, x: float, score: Score) -> int:
        """The bar (from 0) of ``score`` at ``x`` along the progress track."""
        left, right = self.track()
        return bar_at(score, min(max((x - left) / (right - left), 0.0), 1.0) * score.duration_seconds, 1.0)

    def pick_up(self, event: tk.Event) -> None:
        """Pressing the progress bar takes hold of its knob, which then follows the pointer a bar at a time."""
        path = self.selected()
        if self.worker or path is None:
            return
        score = self.read(path)
        if isinstance(score, ScoreError) or not score.bars or not score.duration_seconds:
            self.start(path)  # says what is wrong, as ▶ would
            return
        self.dragging = (path, score)
        self.slide(event)

    def slide(self, event: tk.Event) -> None:
        """The held knob to the start of the bar under the pointer, with that bar and its time shown."""
        if self.dragging is None:
            return
        _, score = self.dragging
        bar = self.drag_bar = self.bar_under(event.x, score)
        speed = self.speed_factor()
        self.bar_hover = True
        self.set_fraction(bar_seconds(score, bar, 1.0) / score.duration_seconds)
        self.time_label.configure(text=f"{clock(bar_seconds(score, bar, speed))} / {clock(score.duration_seconds / speed)}")
        self.show_bar(bar, score)
        self.say(LET_GO)

    def let_go(self, _: tk.Event) -> None:
        """Letting go of the knob plays from the start of its bar, after the countdown, as ▶ does."""
        if self.dragging is None:
            return
        path, _ = self.dragging
        self.dragging = None
        self.start(path, bar=self.drag_bar)
        self.bar_hover = self.worker is None  # the pointer is still on the bar, but not while it plays
        if self.worker is None:  # nothing started (let go just after a stop): the song as it was
            self.show_selected()
        self.draw_bar()

    def set_playing(self, playing: bool, recording: bool = False, listening: bool = False,
                    auditioning: bool = False) -> None:
        busy = playing or recording or listening or auditioning
        self.button.set(self.stop_images if playing else self.play_images, enabled=not busy)
        self.restart_button.set(enabled=not busy)
        self.sync_mode(busy)
        self.speed_choice.set_enabled(not busy)
        self.bar.configure(cursor="arrow" if busy else "hand2")
        # On the 简谱 page, ▶ becomes a live ■ while the song is heard.
        self.hear_button.set(self.halt_images if auditioning else self.play_images,
                             enabled=auditioning or (not busy and self.leaf_song is not None))
        if self.hear_button.tooltip is not None:
            self.hear_button.tooltip.text = HALT_TIP if auditioning else HEAR_TIP
        # Unlike ■ while playing, ■ while recording is live: LyreBot keeps recording when clicked.
        self.record_button.set(self.recording_images if recording else self.record_images,
                               enabled=recording or (not busy and self.transcriber is None))
        self.keys_button.set(self.recording_images if listening else self.keys_images, enabled=listening or not busy)
        if not busy:  # (while busy, the title says what plays)
            if self.where == "leaf" and self.leaf_song is not None:
                self.root.title(f"{self.leaf_song[0].stem} · 简谱 · LyreBot")
            elif self.where == "text" and self.text_path is not None:
                self.root.title(f"{self.text_path.stem} · 原文 · LyreBot")
            else:
                self.root.title(self.base_title)

    def run(self, path: Path, timeline, begin: float, speed: float, mode: str, songs: list[Path],
            countdown: float | None = None) -> None:
        post = self.updates.put
        countdown = COUNTDOWN if countdown is None else countdown
        try:
            keyboard = Keyboard()
            end = time.perf_counter() + countdown
            while (left := end - time.perf_counter()) > 0:
                if self.cancel.is_set() or keyboard.stop_pressed():
                    post(("status", "已取消"))
                    return
                post(("status", f"{math.ceil(left)} 秒后开始，请切换到游戏窗口"))
                self.cancel.wait(min(0.05, left))
            target = keyboard.foreground()
            title, pid = foreground_title_and_pid(target)
            if not target or pid == os.getpid():
                post(("status", "倒计时结束时前台还是 LyreBot，没有演奏" if countdown
                      else "按 F7 时前台是 LyreBot，没有演奏"))
                return
            while True:
                post(("song", (path, timeline.duration)))
                if mode == SHUFFLE:
                    self.shuffle.playing(path)
                reached = [begin]

                def progress(at: float) -> None:
                    reached[0] = at
                    post(("progress", at))
                reason = play(timeline, keyboard, target, self.cancel, progress, start=begin,
                              on_strike=lambda keys: post(("struck", keys)))
                if reason in ("stopped", "focus"):
                    post(("interrupted", (path, reached[0], speed, "已停止" if reason == "stopped" else "切换了窗口，已停止")))
                    return
                if mode == SINGLE:
                    post(("status", "演奏完成"))
                    return
                if mode == REPEAT:  # the same song again, as the file is now
                    try:
                        timeline, begin = compile_timeline(load_score(path), speed), 0.0
                    except (ScoreError, OSError) as error:
                        post(("status", f"曲谱有误，没有再弹：{error}"))
                        return
                    if self.cancel.wait(GAP):
                        post(("status", "已停止"))
                        return
                    continue
                for _ in songs:  # skip scores with mistakes, but give up after one lap
                    path = next_song(songs, path, mode, self.shuffle)
                    try:
                        timeline, begin = compile_timeline(load_score(path), speed), 0.0
                        break
                    except (ScoreError, OSError):
                        if mode == SHUFFLE:  # not drawn again this round
                            self.shuffle.playing(path)
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

    # ---- the 简谱 page ----
    def build_leaf(self) -> None:
        """The page behind the ribbon, as big as the songs' page, with the same frame and ribbon: the chosen
        song's title, its 简谱 to read and click, and at its foot, under the ·◆· rule, the instrument it is
        heard on, the bar and time it has come to, and ▶."""
        root, theme = self.root, self.theme
        px, fonts = theme.px, theme.fonts
        self.leaf_head = Sheet(root, theme)
        self.leaf_title = Words(self.leaf_head, theme, fg=INK, font=fonts.title, anchor="center", justify="center")
        self.leaf_title.pack(fill="x")
        self.leaf_about = Words(self.leaf_head, theme, fg=MUTED, font=fonts.small, anchor="center", justify="center")
        self.leaf_about.pack(fill="x")
        self.leaf_head.bind("<Configure>", lambda event: [words.configure(wraplength=event.width)
                                                          for words in (self.leaf_title, self.leaf_about)], add="+")
        self.leaf_body = Sheet(root, theme)
        self.score_view = ScoreView(self.leaf_body, theme, self.hear_note)
        self.score_view.pack(fill="both", expand=True)  # (scrolled by the wheel, and following the song)
        self.leaf_rule = Ornament(root, theme)
        self.leaf_panel = Sheet(root, theme)
        row = Sheet(self.leaf_panel, theme)
        row.pack(fill="x")
        self.timbre_choice = Choice(row, theme, self.timbre_name, tuple(name for _, name in INSTRUMENTS),
                                    self.pick_timbre)
        self.timbre_choice.pack(side="left")
        self.size_button = IconButton(row, self.grow_images, self.toggle_size, GROW_TIP, theme)
        self.size_button.pack(side="left", padx=(px(6), 0))
        self.hear_button = IconButton(row, self.play_images, self.press_hear, HEAR_TIP, theme)
        self.hear_button.pack(side="right")
        self.leaf_time = Words(row, theme, fg=MUTED, font=fonts.figures)
        self.leaf_time.pack(side="right", padx=(0, px(10)))
        self.leaf_bar = Words(row, theme, fg=MUTED, font=fonts.figures)
        self.leaf_bar.pack(side="right", padx=(0, px(10)))
        self.leaf_status = OneLine(self.leaf_panel, theme, fg=MUTED, font=fonts.small, anchor="w", justify="left")
        self.leaf_status.pack(fill="x", pady=(px(4), 0))
        # From the foot up, as on the songs' page; the head keeps clear of the ribbon on either side.
        self.leaf_page = [(self.leaf_panel, dict(side="bottom", fill="x", padx=px(12), pady=(px(6), px(12)))),
                          (self.leaf_rule, dict(side="bottom", fill="x", padx=px(14), pady=(px(6), 0))),
                          (self.leaf_head, dict(side="top", fill="x", padx=px(GREEN_FROM_RIGHT + 6),
                                                pady=(px(12), px(4)))),
                          (self.leaf_body, dict(fill="both", expand=True, padx=(px(10), px(8)), pady=0))]

    def leaf_pieces(self) -> list[tk.Misc]:
        """The pieces of the 简谱 page the ribbon may lie over."""
        return [self.leaf_head, self.leaf_title, self.leaf_about, self.leaf_body, self.score_view]

    def wheel(self, event: tk.Event) -> None:
        if self.where != "text":  # (the text scrolls itself)
            (self.score_view if self.leaf else self.browser).wheel(event)

    def toggle_size(self) -> None:
        """The 简谱 page's button: the window between two sizes.  The larger has the song four bars a line, the
        widest line just fitting, and the tallest four lines next to one another; the smaller is the narrowest
        window, as tall as the songs' page opens.  Whatever the window was dragged to meanwhile, the button
        goes from the one it last gave to the other; the window keeps its top left corner, and the screen."""
        view = self.score_view
        if self.grown:
            self.size_window(self.theme.px(WINDOW_WIDTH), self.theme.px(OPENING_HEIGHT))
            view.most = None
            self.grown = False
        else:
            wanted = view.four_bars()
            if wanted is None:
                return
            root = self.root
            spare_width = root.winfo_width() - view.winfo_width()  # what is round the score: frame, margins
            spare_height = root.winfo_height() - view.winfo_height()
            view.most = 4
            self.size_window(wanted[0] + spare_width, wanted[1] + spare_height)
            self.grown = True
        self.size_button.set(self.shrink_images if self.grown else self.grow_images)
        if self.size_button.tooltip is not None:
            self.size_button.tooltip.text = SHRINK_TIP if self.grown else GROW_TIP
        self.root.update_idletasks()
        view.lay_out()  # for the size it has come to, now

    def size_window(self, width: int, height: int) -> None:
        """Make the window ``width`` by ``height``, keeping its top left corner, and the whole of it on its
        screen (no larger than the screen)."""
        root = self.root
        x, y = root.winfo_x(), root.winfo_y()
        left, top, right, bottom = screen_area(root, x + 1, y + 1)
        width = max(min(width, right - left), self.theme.px(WINDOW_WIDTH))
        height = max(min(height, bottom - top), self.theme.px(360))
        x, y = min(max(x, left), right - width), min(max(y, top), bottom - height)
        root.geometry(geometry(x, y, width, height))

    def set_away(self, pieces: list[tk.Misc], away: bool) -> None:
        """Mark ``pieces`` and all in them as on the page not shown (the paper passes them by), or shown."""
        stack = list(pieces)
        while stack:
            piece = stack.pop()
            if isinstance(piece, OnPage):
                piece.away = away
            stack.extend(piece.winfo_children())

    def turn_page(self) -> None:
        """The ribbon clicked: to the chosen song's 简谱, or back to the songs.  The page is turned first and
        the score drawn after, for the size it shows at."""
        if self.leaf:
            self.show_songs()
            return
        path = self.selected()
        if self.worker:
            self.ribbon_says("正在演奏或录音，结束后再看简谱")
            return
        if path is None:
            self.ribbon_says("先选一首曲子")
            return
        read = self.load_leaf(path)
        if read is None:
            return
        self.show_leaf()
        self.root.update_idletasks()  # (the score is laid out for the width it has on the page)
        self.put_leaf(read)

    def load_leaf(self, path: Path, quietly: bool = False) -> tuple | None:
        """``path`` read for the 简谱 page: (path, its time, score, sheet), the sheet None when it is the song
        there already, unchanged.  None, said under the ribbon unless ``quietly``, when it cannot be read."""
        try:
            changed = path.stat().st_mtime
        except OSError:
            changed = None
        if self.leaf_song is not None and self.leaf_song[0] == path and changed == self.leaf_song[1]:
            return path, changed, self.leaf_song[2], None
        kept = self.leaves.pop((path, changed), None)  # read lately, unchanged since: not read again
        if kept is None:
            try:
                kept = load_score(path), jianpu.sheet_of(path.read_text(encoding="utf-8-sig"), path.stem)
            except (OSError, ScoreError, UnicodeDecodeError, ValueError) as error:
                if not quietly:
                    self.ribbon_says("曲谱有误，没有简谱可看" if isinstance(error, ScoreError) else "读不了这份曲谱")
                return None
        self.leaves[(path, changed)] = kept
        while len(self.leaves) > LEAVES:
            self.leaves.pop(next(iter(self.leaves)))
        return path, changed, *kept

    def read_leaf(self) -> bool:
        """Draw the song on the 简谱 page again if its file has changed; back to the songs if it has gone.
        False when it cannot be shown."""
        if self.leaf_song is None:
            return False
        path = self.leaf_song[0]
        if not path.exists():  # moved or deleted while it was open
            self.show_songs()
            return False
        read = self.load_leaf(path, quietly=True)
        if read is None:
            return False
        if read[3] is not None:
            self.put_leaf(read)
        return True

    def put_leaf(self, read: tuple) -> None:
        """Show what ``load_leaf`` read on the 简谱 page, timed at the speed chosen now."""
        path, changed, score, sheet = read
        speed = self.speed_factor()
        if sheet is None and self.leaf_song is not None and self.leaf_song[4] == speed:
            timeline = self.leaf_song[3]  # the same song at the same speed: as it was
        else:
            timeline = compile_ringing(score, speed)
            self.leaf_from = None  # a new song, or at another speed: where it stopped is elsewhere in time
            if sheet is not None:
                again = self.leaf_song is not None and self.leaf_song[0] == path  # (its file changed): still there
                self.score_view.show(sheet, top=self.score_view.canvasy(0) if again else 0.0)
                # 1=C, the meter and the tempo; under them, briefly, where it came from and how it reads.
                notes = [note.removeprefix("LyreBot ").split("，")[0] for note in sheet.notes
                         if note != "速度随演奏起伏"]  # (the ≈ says as much)
                about = "　".join(("1=C", f"{sheet.meter[0]}/{sheet.meter[1]}", sheet.tempo))
                self.leaf_title.configure(text=sheet.title)
                self.leaf_about.configure(text=about + ("\n" + " · ".join(notes) if notes else ""))
            self.score_view.time(score, speed, sorted(event.at for event in timeline.events if event.down))
            tempo = score.tempo_map(speed)
            self.leaf_bars = [float(tempo.seconds(Fraction(start))) for start in score.bars]
        self.leaf_song = (path, changed, score, timeline, speed)
        self.show_heard(self.leaf_from or 0.0)
        self.leaf_status.configure(text=TO_HEAR if self.leaf_from is None else "已停止 · 点 ▶ 从亮着的音接着听",
                                   fg=MUTED)
        if not self.worker:
            self.set_playing(False)

    def turn_to(self, where: str) -> None:
        """Show page ``where`` ("songs", "leaf" or "text") in place of the others, which are put away; the green
        ribbon hangs on the 简谱 page and the text page only, and the format's card goes with the text page."""
        if where != "text" and self.guide is not None:
            if self.guide.winfo_exists():
                self.guide.destroy()
            self.guide = None
        for name, pieces in self.pages.items():
            if name != where:
                for piece, _ in pieces:
                    piece.pack_forget()
                self.set_away([piece for piece, _ in pieces], True)
        self.set_away([piece for piece, _ in self.pages[where]], False)
        for piece, how in self.pages[where]:
            piece.pack(**how)
        self.page.schedule()  # (laid where they stand now, whether or not they moved)
        self.where, self.leaf = where, where == "leaf"
        green = self.hung["green"]
        if where == "songs" and green is not None:
            self.page.swap(green, None)
            self.hung["green"] = None
        elif where != "songs" and green is None:
            self.hung["green"] = self.green_image
            self.page.hang(self.green_image, self.ribbon_place["green"])
        self.ribbon_leave()

    def show_leaf(self) -> None:
        """Turn to the 简谱 page."""
        self.turn_to("leaf")
        self.score_view.later()  # a width not laid out for yet: laid out once it holds still
        self.set_playing(False)

    def show_songs(self) -> None:
        """Turn back to the songs; a song being heard stops, and a window made larger for the 简谱 page goes
        back to the narrow one first."""
        if self.auditioning:
            self.stop()
        if self.grown:
            self.toggle_size()
        self.turn_to("songs")
        if not self.worker:
            self.root.title(self.base_title)
        self.show_selected()

    def show_heard(self, at: float) -> None:
        """The 简谱 page's bar and time at ``at`` seconds into its song."""
        if self.leaf_song is None:
            return
        duration = self.leaf_song[3].duration
        self.leaf_time.configure(text=f"{clock(min(at, duration))} / {clock(duration)}")
        if self.leaf_bars:
            bar = max(bisect_right(self.leaf_bars, at + 1e-6), 1)
            self.leaf_bar.configure(text=f"{bar}|{len(self.leaf_bars)}")

    def heard_end(self, reason: str, reached: float) -> None:
        """The song stopped being heard: at its end, or stopped part way, where ▶ will go on from."""
        view = self.score_view
        if reason == "done":
            self.leaf_from = None
            view.heard(None, follow=False)
            self.show_heard(0.0)
            self.leaf_status.configure(text="试听完毕 · 点 ▶ 从头再听，或点简谱上的音", fg=MUTED)
            return
        lit = view.sounding(reached) if view.notes else []
        self.leaf_from = min((view.notes[index].start for index in lit), default=reached)
        view.heard(reached, follow=False)
        self.leaf_status.configure(text="已停止 · 点 ▶ 从亮着的音接着听", fg=MUTED)

    # ---- the ribbons ----
    def on_ribbon(self, event: tk.Event) -> str | None:
        """Which ribbon the pointer of ``event`` is on ("orange" or "green"), if any: its band, a little wider
        to be easy to hit."""
        root, px = self.root, self.theme.px
        x, y = event.x_root - root.winfo_rootx(), event.y_root - root.winfo_rooty()
        band, top = px(10.5), self.head()
        for which, from_right in (("orange", RIBBON_FROM_RIGHT), ("green", GREEN_FROM_RIGHT)):
            left = root.winfo_width() - px(from_right)
            if self.hung[which] is not None and left - px(2) <= x < left + band + px(2) \
                    and top <= y < top + self.ribbon_image.height():
                return which
        return None

    def ribbon_press(self, event: tk.Event) -> str | None:
        """A click on a ribbon tugs it, and the page turns as it is pulled furthest; a click while one is still
        moving counts for nothing (nor does it reach what lies under the ribbon).  From the text page, while
        the text has a mistake, the ribbon is tugged but the page stays, and the mistake is said."""
        which = self.on_ribbon(event)
        if which is None:
            return None
        self.callout.hide()
        if self.tugging is None:
            problem = self.text_problem()
            self.tug(0, which, turn=problem is None)
            if problem is not None:
                self.ribbon_says(problem, which, last=4000)
        return "break"

    def tug(self, frame: int, which: str | None = None, turn: bool = True) -> None:
        """Show frame ``frame`` of a ribbon's tug (see ``TUG``), turning the page at ``TUG_TURN``; frame 0
        names the ribbon, and whether the page is to turn."""
        if which is not None:
            self.tug_which, self.tug_turns = which, turn
        which = self.tug_which
        self.tug_job = None
        if frame >= len(TUG):
            self.tugging = None
            self.hang_ribbon(which, self.green_image if which == "green" else self.ribbon_image)
            return
        self.tugging = frame
        self.hang_ribbon(which, self.theme.ribbon(round(TUG[frame] * self.theme.scale), green=which == "green"))
        if frame == TUG_TURN and self.tug_turns:
            self.ribbon_turns(which)
        self.tug_job = self.root.after(TUG_STEP, self.tug, frame + 1)

    tug_which, tug_turns = "orange", True

    def finish_tug(self) -> None:
        """Play what is left of the ribbon's tug at once."""
        while self.tugging is not None:
            if self.tug_job is not None:
                self.root.after_cancel(self.tug_job)
            self.tug(self.tugging + 1)

    def hang_ribbon(self, which: str, image: tk.PhotoImage) -> None:
        hung = self.hung[which]
        if hung is not None and image is not hung:
            self.page.swap(hung, image)
            self.hung[which] = image

    @property
    def hung_ribbon(self) -> tk.PhotoImage | None:  # (the orange one)
        return self.hung["orange"]

    def ribbon_turns(self, which: str) -> None:
        """The page a ribbon turns to.  Orange: from the songs to the 简谱, from the others back to the songs.
        Green: between the 简谱 and the text.  Leaving the text saves it first, if it reads."""
        if which == "orange":
            if self.where == "text":
                if self.leave_text(which):
                    self.show_songs()
            else:
                self.turn_page()
        elif self.where == "leaf":
            self.show_text()
        elif self.where == "text" and self.leave_text(which):
            self.back_to_leaf()

    def ribbon_motion(self, event: tk.Event) -> str | None:
        which = self.on_ribbon(event)
        widget = event.widget
        if which is not None:
            name = str(widget)
            if name not in self.cursors:
                self.cursors[name] = str(widget.cget("cursor"))
            widget.configure(cursor="hand2")
            if self.ribbon_inside != which:
                self.callout.hide()
                self.ribbon_inside = which
                if which == "green":
                    words = TO_TEXT if self.where == "leaf" else TO_LEAF
                else:
                    words = TO_SCORE if self.where == "songs" else TO_SONGS
                self.ribbon_says(words, which, wait=550)
            return "break"  # (nor does the list light the song under it)
        self.ribbon_leave(event)
        return None

    def ribbon_leave(self, event: tk.Event | None = None) -> None:
        """The pointer is off the ribbon: the pieces get their own pointers back, and the words go."""
        if self.ribbon_inside:
            self.ribbon_inside = False
            self.callout.hide()
        for name, cursor in list(self.cursors.items()):
            try:
                self.root.nametowidget(name).configure(cursor=cursor)
            except (KeyError, tk.TclError):
                pass
        self.cursors.clear()

    def ribbon_says(self, text: str, which: str = "orange", wait: int = 0, last: int = 2200) -> None:
        """A few words under a ribbon's tail."""
        root, px = self.root, self.theme.px
        from_right = GREEN_FROM_RIGHT if which == "green" else RIBBON_FROM_RIGHT
        x = root.winfo_rootx() + root.winfo_width() - px(from_right) + px(5)
        y = root.winfo_rooty() + self.head() + self.ribbon_image.height()
        self.callout.say(text, x, y, wait=wait, last=last)

    # ---- the text page ----
    def build_text(self) -> None:
        """The page behind the green ribbon: the song's title and its file, the slip of paper its text is
        written on, and at its foot, under the ·◆· rule, undo and save, and a line saying how the text stands."""
        root, theme = self.root, self.theme
        px, fonts = theme.px, theme.fonts
        self.text_head = Sheet(root, theme)
        self.text_title = Words(self.text_head, theme, fg=INK, font=fonts.title, anchor="center", justify="center")
        self.text_title.pack(fill="x")
        self.text_about = Words(self.text_head, theme, fg=MUTED, font=fonts.small, anchor="center", justify="center")
        self.text_about.pack(fill="x")
        self.text_head.bind("<Configure>", lambda event: [words.configure(wraplength=event.width)
                                                          for words in (self.text_title, self.text_about)], add="+")
        self.manuscript = Manuscript(root, theme, fonts.mono, fonts.figures, measure_lines)
        self.text_box = text = self.manuscript.text
        text.bind("<<Modified>>", self.text_changed, add="+")
        text.bind("<Control-s>", lambda _: self.save_text() and "break" or "break")
        text.bind("<Control-S>", lambda _: self.save_text() and "break" or "break")
        text.bind("<<Undo>>", lambda _: self.undo_text() or "break")
        self.text_rule = Ornament(root, theme)
        self.text_panel = Sheet(root, theme)
        row = Sheet(self.text_panel, theme)
        row.pack(fill="x")
        # The row is as tall as the 简谱 page's, which holds ▶, so that the ·◆· rule over it stays put when the
        # page is turned: the smaller buttons sit in its middle.
        spare = (self.play_images["normal"].height() + 2) - (self.undo_images["normal"].height() + 2)
        middle = (max(spare, 0) // 2, max(spare, 0) - max(spare, 0) // 2)
        self.undo_button = IconButton(row, self.undo_images, self.undo_text, UNDO_TIP, theme)
        self.undo_button.pack(side="left", pady=middle)
        self.save_button = IconButton(row, self.save_images, self.save_text, SAVE_TIP, theme)
        self.save_button.pack(side="left", padx=(px(6), 0), pady=middle)
        self.guide_button = IconButton(row, self.guide_images, self.toggle_guide, GUIDE_TIP, theme)
        self.guide_button.pack(side="right", pady=middle)
        self.text_status = OneLine(self.text_panel, theme, fg=MUTED, font=fonts.small, anchor="w", justify="left")
        self.text_status.pack(fill="x", pady=(px(4), 0))
        self.text_page = [(self.text_panel, dict(side="bottom", fill="x", padx=px(12), pady=(px(6), px(12)))),
                          (self.text_rule, dict(side="bottom", fill="x", padx=px(14), pady=(px(6), 0))),
                          (self.text_head, dict(side="top", fill="x", padx=px(GREEN_FROM_RIGHT + 6),
                                                pady=(px(12), px(4)))),
                          (self.manuscript, dict(fill="both", expand=True, padx=px(22), pady=(px(2), px(8))))]

    def show_text(self) -> None:
        """Turn from the 简谱 page to its song's text, read from its file as it is now: the version undo goes
        back to.  The cursor and the view are where this page was left, for the same song; else at the top."""
        if self.auditioning:
            self.stop()
        path = self.leaf_song[0] if self.leaf_song else None
        try:
            raw = path.read_text(encoding="utf-8") if path else None
        except (OSError, UnicodeDecodeError):
            raw = None
        if raw is None:
            self.ribbon_says("读不了这份曲谱", "green")
            return
        self.text_bom = raw.startswith("\ufeff")
        content = raw.removeprefix("\ufeff")
        text = self.text_box
        place = (text.index("insert"), text.yview()[0]) if path == self.text_path else ("1.0", 0.0)
        text.delete("1.0", "end")
        text.insert("1.0", content)
        text.edit_reset()  # undo goes back no further than this
        text.edit_modified(False)
        self.text_path, self.text_saved, self.text_kept = path, content, False
        self.text_title.configure(text=path.stem)
        try:
            where = path.parent.relative_to(self.scores_dir.parent)
        except ValueError:
            where = path.parent
        self.text_about.configure(text=f"{where.as_posix()}/{path.name}")
        self.turn_to("text")
        self.check_text()
        text.mark_set("insert", place[0])
        text.yview_moveto(place[1])
        text.focus_set()
        self.set_playing(False)

    def back_to_leaf(self) -> None:
        """Turn from the text back to the 简谱, where it was left: drawn again if the text was changed, and still
        scrolled where it was."""
        self.turn_to("leaf")
        self.root.update_idletasks()
        self.read_leaf()
        self.set_playing(False)

    def text_problem(self) -> str | None:
        """On the text page, the mistake in the text that keeps it from being left; else None."""
        if self.where != "text":
            return None
        self.check_text()
        return self.text_error

    def leave_text(self, which: str) -> bool:
        """Before the text page is left: read it once more and save it.  False, with the mistake said under
        the ribbon, if it does not read."""
        problem = self.text_problem()
        if problem is not None:
            self.ribbon_says(problem, which, last=4000)
            return False
        return self.save_text()

    def text_changed(self, _: tk.Event | None = None) -> None:
        """The text was changed: look for mistakes once it is left alone a moment."""
        text = self.text_box
        if not text.edit_modified():
            return
        text.edit_modified(False)  # (so that the next change is told too)
        self.text_kept = False
        top = self.root
        if self.text_job is not None:
            top.after_cancel(self.text_job)
        self.text_job = top.after(CHECK_AFTER, self.check_text)
        self.show_text_state()

    def check_text(self) -> None:
        """Read the text as a score: its mistake, if any, said and its line marked; the score kept if it reads.
        The slip numbers bars, not lines, so a mistake in a bar is said by the bar's number."""
        if self.text_job is not None:
            self.root.after_cancel(self.text_job)
            self.text_job = None
        written = self.text_box.get("1.0", "end-1c")
        try:
            self.text_score, self.text_error = parse_score(written), None
        except ScoreError as error:
            self.text_score, self.text_error = None, str(error)
        found = re.search(r"第 (\d+) 行", self.text_error or "")
        line = int(found.group(1)) if found else None
        self.manuscript.mark(line)
        bars = measure_lines(written)
        if found and line in bars:  # (a directive's line has no number on the slip: it is still said by line)
            self.text_error = self.text_error.replace(found.group(0), f"第 {bars.index(line) + 1} 小节", 1)
        self.show_text_state()

    def show_text_state(self) -> None:
        """Undo and the words under the slip, as the text stands."""
        text = self.text_box
        self.undo_button.set(enabled=bool(int(text.edit("canundo"))))
        changed = text.get("1.0", "end-1c") != self.text_saved
        if self.text_error is not None:  # (where in the line: the line is marked; all of it on save or a ribbon)
            self.text_status.configure(text=re.sub(r"（位置：[^）]*）$", "", self.text_error), fg=WARN)
        elif changed:
            self.text_status.configure(text=f"{CHANGED} · Ctrl+S 保存", fg=MUTED)
        else:
            self.text_status.configure(text=SAVED if self.text_kept else TEXT_HINT, fg=MUTED)

    def toggle_guide(self) -> None:
        """The text page's ? button: the format's card beside the window, or, while it is open, closed.  The
        keyboard stays with the text."""
        if self.guide is not None and self.guide.winfo_exists():
            self.guide.destroy()
            self.guide = None
        else:
            self.guide = dialogs.card(self.root, guide.TITLE, guide.fill, on_close=self.guide_closed)
            self.root.after(60, self.text_box.focus_force)

    def toggle_quickstart(self) -> None:
        """The title bar's open book: 快速开始 beside the window, or, while it is open, closed.  The keyboard
        stays where it was."""
        if self.quickstart is not None and self.quickstart.winfo_exists():
            self.quickstart.destroy()
            self.quickstart = None
            return
        pictures = {"play": self.play_images["normal"], "ribbon": self.ribbon_image, "green": self.green_image,
                    "record": self.record_images["normal"], "keys": self.keys_images["normal"]}
        self.quickstart = dialogs.card(self.root, quickstart.TITLE,
                                       lambda frame, theme: quickstart.fill(frame, theme, pictures),
                                       on_close=self.quickstart_closed, note=quickstart.README)
        self.root.after(60, self.take_keyboard)

    def quickstart_closed(self) -> None:
        self.quickstart = None
        self.take_keyboard()

    def take_keyboard(self) -> None:
        """The keyboard back to the window: to the text, on the text page."""
        try:
            (self.text_box if self.where == "text" else self.root).focus_force()
        except tk.TclError:
            pass

    def guide_closed(self) -> None:
        self.guide = None
        if self.where == "text":
            self.text_box.focus_force()

    def undo_text(self) -> None:
        """Undo the last change, no further back than the text as it was when the page was turned to."""
        try:
            self.text_box.edit_undo()
        except tk.TclError:
            pass  # (nothing left to undo)
        self.text_changed()
        self.show_text_state()

    def save_text(self) -> bool:
        """Read the text once more and write it to its file; with a mistake in it, say so over the save button
        and keep it unwritten.  True once it is saved."""
        if self.text_path is None:
            return False
        self.check_text()
        if self.text_error is not None:
            button = self.save_button
            self.callout.say(self.text_error, button.winfo_rootx() + button.winfo_width() // 2, button.winfo_rooty(),
                             last=4000, above=True)
            return False
        content = self.text_box.get("1.0", "end-1c")
        if content != self.text_saved:
            try:
                self.text_path.write_text(("\ufeff" if self.text_bom else "") + content, encoding="utf-8", newline="\n")
            except OSError as error:
                self.text_status.configure(text=f"保存失败：{error}", fg=WARN)
                return False
            self.text_saved = content
            self.cache.pop(self.text_path, None)
        self.text_kept = True
        self.show_text_state()
        return True

    def show_progress(self, at: float) -> None:
        """The knob, the time and the bar counter at ``at`` seconds into the song playing."""
        self.set_fraction(at / self.duration if self.duration else 0)
        self.time_label.configure(text=f"{clock(at)} / {clock(self.duration)}")
        if self.bar_starts:
            bar = max(bisect_right(self.bar_starts, at + 1e-6), 1)
            self.bar_label.configure(text=f"{bar}|{len(self.bar_starts)}")

    def notated(self, path: Path, faithful: float) -> None:
        self.say(f"已写成曲谱 {path.name}")
        self.written = (path, f"刚从演奏记录好，{faithful:.0%} 的按键照原样弹回")

    def stop(self) -> None:
        self.cancel.set()

    def click_away(self, event: tk.Event) -> None:
        """A press anywhere but on the search line (where the pointer is an I-beam) leaves it."""
        line = str(self.search_field)
        where = str(event.widget)
        if where != line and not where.startswith(line + "."):
            self.leave_search()

    def leave_search(self) -> None:
        try:
            typing = self.root.focus_get() is self.search_entry
        except (KeyError, tk.TclError):  # (a focus Tk cannot name, in a menu)
            return
        if typing:
            self.root.focus_set()

    def poll(self) -> None:
        frame = self.frame
        if frame is not None and frame.presses != self.frame_presses:
            self.frame_presses = frame.presses  # pressed on the title bar or an edge
            self.leave_search()
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
                self.browser.set_playing(path)
                self.title_label.configure(text=path.stem)
                self.time_label.configure(text=f"0:00 / {clock(self.duration)}")
                score = self.read(path)
                tempo = None if isinstance(score, ScoreError) else score.tempo_map(self.speed_factor())
                self.bar_starts = [float(tempo.seconds(Fraction(start))) for start in score.bars] if tempo else []
                self.root.title(f"{path.stem} · LyreBot")
            elif kind == "interrupted":
                self.interrupted(*value)
            elif kind == "progress":
                self.show_progress(value)
                self.say("正在演奏 · 按 F8 或切换窗口停止")
            elif kind == "struck":
                self.browser.strike({LYRE_ROWS[key] for key in value if key in LYRE_ROWS})
            elif kind == "heard":
                hearing, at = value
                if hearing == self.hearing:
                    self.score_view.heard(at)
                    self.show_heard(at)
                    self.leaf_status.configure(text=HEARING, fg=MUTED)
            elif kind == "heard end":
                self.heard_end(*value)
            elif kind == "error":
                self.say("出错，已停止", warn=True)
                if not self.closing:
                    dialogs.error("演奏出错", value, parent=self.root)
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
            elif kind == "audition failed":
                self.leaf_status.configure(text=f"不能试听：{value}", fg=WARN)
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
                self.auditioning = False
                self.quiet_until = time.monotonic() + COOLDOWN
                self.browser.set_playing(None)
                self.set_playing(False)
                self.draw_bar()
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
        if self.where == "text" and self.text_box.get("1.0", "end-1c") != self.text_saved and not self.save_text():
            question = f"原文有误，改动没有保存：\n{self.text_error}\n\n仍然关闭吗？"
            if not dialogs.confirm("关闭 LyreBot", question, parent=self.root, yes="关闭"):
                return
        self.keep_place()
        self.closing = True
        self.stop()
        self.stop_transcribing.set()
        if not self.worker and not self.transcriber:
            self.root.destroy()


PLACE = CACHE_ROOT.parent / "window.json"  # where the window was left (its top left corner)
SETTINGS = CACHE_ROOT.parent / "settings.json"  # what was chosen, to be chosen again next time


def read_timbre(path: Path) -> int:
    """The instrument songs were last heard on, if it is one offered; else the piano."""
    try:
        program = json.loads(path.read_text(encoding="utf-8"))["timbre"]
    except (OSError, ValueError, KeyError, TypeError):
        return PIANO
    return program if type(program) is int and program in dict(INSTRUMENTS) else PIANO


def write_timbre(program: int, path: Path) -> None:
    """Keep the instrument songs are heard on, beside whatever else ``path`` keeps."""
    write_setting("timbre", program, path)


def read_on_top(path: Path) -> bool:
    """Whether the window was last kept over all the others (``App.set_on_top``); not unless it was."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))["on_top"] is True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def write_setting(key: str, value: object, path: Path) -> None:
    """Keep ``value`` as ``key`` in the settings file ``path``, beside whatever else it keeps."""
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
        kept = kept if isinstance(kept, dict) else {}
    except (OSError, ValueError):
        kept = {}
    kept[key] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def write_place(corner: tuple[int, int], dpi: int, path: Path | None = None) -> None:
    """Keep where the window is, ``corner`` (its top left, in LyreBot's pixels), drawn at ``dpi``.  Its size is
    not kept: it opens at the size it opens at the first time."""
    path = path or PLACE
    left, top = corner
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"left": left, "top": top, "dpi": dpi}), encoding="utf-8")
    except OSError:
        pass


def read_place(dpi: int, path: Path | None = None) -> tuple[int, int, int, int] | None:
    """Where the window opens, as (left, top, width, height) for LyreBot drawn at ``dpi`` now: at the corner
    it was left at, the size it opens at the first time (the narrowest, 640 high: what the 简谱 page's button
    makes it smaller to), and the whole of it on the screen it is on.  None when nothing was kept, or that
    screen is gone.  (A size kept by LyreBot before 3.18.1 is passed over.)"""
    path = path or PLACE
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
        left, top, was = (int(kept[key]) for key in ("left", "top", "dpi"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    width, height = round(WINDOW_WIDTH * dpi / 96), round(OPENING_HEIGHT * dpi / 96)
    area = screen_holding(left, top, left + width, top + height)
    if area is None or was <= 0:
        return None
    area_left, area_top, area_right, area_bottom = area
    width, height = min(width, area_right - area_left), min(height, area_bottom - area_top)
    left = min(max(left, area_left), area_right - width)
    top = min(max(top, area_top), area_bottom - height)
    return left, top, width, height


def main() -> None:
    if sys.platform == "win32" and not is_admin() and relaunch_as_admin():
        return  # the elevated copy takes over
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # drawn at the main screen's scaling
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
        App(root, own_frame=True, place=read_place(round(root.winfo_fpixels("1i"))), settings=SETTINGS)
    except Exception:
        if sys.stderr is not None:
            raise
        import traceback  # windowless start: show the error instead of vanishing
        root.withdraw()
        system_dialogs.showerror("LyreBot 启动失败", traceback.format_exc(limit=3))
        return
    root.mainloop()
