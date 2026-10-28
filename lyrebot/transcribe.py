"""Turn a recording into a MIDI file with audio.cpp and the MuScriptor model.

Neither ships with LyreBot. They are looked for in a ``tools`` folder inside
the LyreBot folder, or in a ``LyreBot-tools`` folder next to it (see README).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Iterable

AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".ogg")
CLI = "audiocpp_cli.exe"
MEMORY_LIMIT = 6 * 1024 ** 3  # bytes; MuScriptor needs about 3.5 GB for a song
TIMEOUT = 1800.0  # seconds; on the CPU a minute of audio takes about half a minute
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class TranscribeError(Exception):
    pass


@dataclass(frozen=True)
class Tools:
    attempts: tuple[tuple[tuple[str, ...], str], ...]  # (command, backend), tried in order until one works
    model: Path
    ffmpeg: str | None = None


def tool_folders(project: Path) -> list[Path]:
    return [project / "tools", project.parent / "LyreBot-tools"]


def find_tools(folders: Iterable[Path]) -> Tools | None:
    """audio.cpp builds and the MuScriptor model in the given folders, or None if either is missing.

    A build in a folder named like ``audiocpp-vulkan`` runs on the graphics
    card, one named like ``audiocpp-cpu`` on the processor; the graphics card
    is tried first. (The Vulkan build cannot always fall back to the CPU.)
    """
    clis: list[Path] = []
    models: list[Path] = []
    ffmpeg = shutil.which("ffmpeg")
    for folder in folders:
        if not folder.is_dir():
            continue
        clis += [path for path in [folder / CLI, *sorted(folder.glob(f"*/{CLI}"))] if path.is_file()]
        models += sorted(path for path in folder.rglob("*.gguf") if "muscriptor" in path.name.lower())
        ffmpeg = ffmpeg or next((str(path) for path in folder.glob("**/ffmpeg.exe")), None)
    if not clis or not models:
        return None
    first, last = [], []
    for cli in clis:
        name = cli.parent.name.lower()
        if "vulkan" in name:
            first.append(((str(cli),), "vulkan"))
        elif "cpu" in name:
            last.append(((str(cli),), "cpu"))
        else:
            first.append(((str(cli),), "vulkan"))
            last.append(((str(cli),), "cpu"))
    model = sorted(models, key=lambda path: "f32" not in path.name.lower())[0]
    return Tools(tuple(first + last), model, ffmpeg)


def transcribe(audio: Path, target: Path, tools: Tools, cancel: threading.Event | None = None) -> None:
    """Write the MIDI transcription of ``audio`` to ``target``, which appears only once it is complete."""
    cancel = cancel or threading.Event()
    with tempfile.TemporaryDirectory(prefix="lyrebot-") as folder:
        work = Path(folder)
        wav = audio
        if audio.suffix.lower() != ".wav":
            if not tools.ffmpeg:
                raise TranscribeError("要转录 WAV 以外的格式需要 ffmpeg，也可以先把它转成 WAV")
            wav = work / "audio.wav"
            _run([tools.ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(audio), "-c:a", "pcm_s16le",
                  str(wav)], work, cancel, "格式转换")
        out = work / "out.mid"
        error = "没有可用的转录程序"
        for command, backend in tools.attempts:
            try:
                _run([*command, "--task", "midi", "--family", "muscriptor", "--model", str(tools.model),
                      "--backend", backend, "--audio", str(wav), "--out", str(out)], work, cancel, "转录")
            except TranscribeError as failure:
                if cancel.is_set():
                    raise
                error = str(failure)
                continue
            if out.is_file() and out.stat().st_size > 0:
                partial = target.with_name(target.name + ".part")
                shutil.copyfile(out, partial)
                os.replace(partial, target)
                return
            error = "转录程序没有输出 MIDI"
        raise TranscribeError(error)


def _run(command: list[str], work: Path, cancel: threading.Event, what: str) -> None:
    log = work / "errors.log"
    with open(log, "wb") as errors:
        process = subprocess.Popen(command, cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=errors, creationflags=NO_WINDOW)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancel.wait(0.2):
                    raise TranscribeError("已取消")
                if time.monotonic() - started > TIMEOUT:
                    raise TranscribeError(f"{what}超过 {TIMEOUT / 60:.0f} 分钟还没完成，已停止")
                if private_bytes(process.pid) > MEMORY_LIMIT:
                    raise TranscribeError(f"{what}占用的内存超过 {MEMORY_LIMIT / 1024 ** 3:.0f} GB，已停止")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if process.returncode != 0:
        raise TranscribeError(f"{what}失败：{_last_message(log, process.returncode)}")


def _last_message(log: Path, code: int) -> str:
    try:
        lines = [line.strip() for line in log.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError:
        lines = []
    lines = [line for line in lines if line]
    failed = [line for line in lines if "failed" in line.lower() or "error" in line.lower()]
    if failed or lines:
        return (failed or lines)[-1][:300]
    return f"退出码 {code & 0xFFFFFFFF:#x}"


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            "PrivateUsage")]


_kernel32 = None


def private_bytes(pid: int) -> int:
    """Memory a process has committed for itself, or 0 when it cannot be read."""
    global _kernel32
    if sys.platform != "win32":
        return 0
    if _kernel32 is None:
        _kernel32 = ctypes.WinDLL("kernel32")  # own instance, so these signatures stay private
        _kernel32.OpenProcess.restype = ctypes.c_void_p
        _kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        _kernel32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MemoryCounters),
                                                      ctypes.c_uint32]
        _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = _kernel32.OpenProcess(0x1000 | 0x0010, False, pid)  # query limited information, read memory
    if not handle:
        return 0
    try:
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not _kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return 0
        return counters.PrivateUsage
    finally:
        _kernel32.CloseHandle(handle)
