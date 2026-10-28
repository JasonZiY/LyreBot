"""Record what the speakers are playing (WASAPI loopback) into a WAV file.

Standard library only: the audio device is reached through ctypes and COM.
The recording starts when sound arrives and ends after a stretch of quiet,
after a time limit, or when the caller cancels it.
"""

from __future__ import annotations

from array import array
import ctypes
from ctypes import POINTER, byref, c_int64, c_uint16, c_uint32, c_uint64, c_void_p
from pathlib import Path
import struct
import threading
import time
from typing import BinaryIO, Callable

WAIT = 300.0  # seconds to wait for sound before giving up
SILENCE = 5.0  # seconds of quiet that end a recording
LONGEST = 600.0  # seconds
FLOAT_QUIET = 1e-4  # about -80 dB
PCM_QUIET = 3

PCM, IEEE_FLOAT = 1, 3


class RecordError(Exception):
    pass


class WavWriter:
    """A WAV file written as it grows; the sizes in the header are filled in on close."""

    def __init__(self, path: Path, rate: int, channels: int, float_samples: bool):
        self.file: BinaryIO = open(path, "wb")
        self.channels = channels
        self.width = 4 if float_samples else 2
        self.frames = 0
        tag = IEEE_FLOAT if float_samples else PCM
        block = channels * self.width
        fmt = struct.pack("<HHIIHHH", tag, channels, rate, rate * block, block, self.width * 8, 0)
        self.file.write(b"RIFF\0\0\0\0WAVE")
        self.file.write(b"fmt " + struct.pack("<I", len(fmt)) + fmt)
        self.fact_at = self.file.tell() + 8
        self.file.write(b"fact" + struct.pack("<II", 4, 0))
        self.data_at = self.file.tell() + 4
        self.file.write(b"data\0\0\0\0")

    def write(self, data: bytes) -> None:
        self.file.write(data)
        self.frames += len(data) // (self.channels * self.width)

    def close(self) -> None:
        if self.file.closed:
            return
        size = self.frames * self.channels * self.width
        if size % 2:
            self.file.write(b"\0")
        end = self.file.tell()
        self.file.seek(4)
        self.file.write(struct.pack("<I", end - 8))
        self.file.seek(self.fact_at)
        self.file.write(struct.pack("<I", self.frames))
        self.file.seek(self.data_at)
        self.file.write(struct.pack("<I", size))
        self.file.close()


class Take:
    """Decides when a recording starts and stops, apart from any audio device.

    Packets of interleaved samples go in through ``feed``; ``idle`` is called
    when the device has nothing new. Loopback capture delivers nothing at all
    while nothing plays, so after the start a gap in the packets is written
    out as silence to keep the timeline true.
    """

    def __init__(self, writer: WavWriter, rate: int, channels: int, float_samples: bool, now: float,
                 wait: float = WAIT, silence: float = SILENCE, longest: float = LONGEST):
        self.writer, self.rate, self.channels = writer, rate, channels
        self.typecode = "f" if float_samples else "h"
        self.quiet_level = FLOAT_QUIET if float_samples else PCM_QUIET
        self.frame_bytes = channels * (4 if float_samples else 2)
        self.opened, self.wait, self.silence, self.longest = now, wait, silence, longest
        self.began: float | None = None
        self.last_loud = now
        self.reason = ""  # why it ended: "quiet", "long", "no sound"

    @property
    def done(self) -> bool:
        return bool(self.reason)

    @property
    def seconds(self) -> float:
        return self.writer.frames / self.rate

    def loud(self, data: bytes) -> bool:
        samples = array(self.typecode)
        samples.frombytes(data[:len(data) - len(data) % samples.itemsize])
        return bool(samples) and max(max(samples), -min(samples)) > self.quiet_level

    def feed(self, data: bytes, now: float) -> None:
        if self.done:
            return
        loud = self.loud(data)
        if self.began is None:
            if not loud:
                self.check(now)
                return
            self.began = now
        if loud:
            self.last_loud = now
        self.writer.write(data)
        self.check(now)

    def idle(self, now: float) -> None:
        if self.done:
            return
        if self.began is not None:
            behind = int((now - self.began) * self.rate) - self.writer.frames
            if behind > self.rate // 5:  # 200 ms without a packet: the device went quiet
                self.writer.write(bytes((behind - self.rate // 20) * self.frame_bytes))
        self.check(now)

    def check(self, now: float) -> None:
        if self.began is None:
            if now - self.opened > self.wait:
                self.reason = "no sound"
        elif now - self.last_loud > self.silence:
            self.reason = "quiet"
        elif self.seconds >= self.longest:
            self.reason = "long"


# ---- WASAPI through ctypes ----------------------------------------------------

class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16), ("Data4", ctypes.c_ubyte * 8)]


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", c_uint16), ("nChannels", c_uint16), ("nSamplesPerSec", c_uint32),
                ("nAvgBytesPerSec", c_uint32), ("nBlockAlign", c_uint16), ("wBitsPerSample", c_uint16),
                ("cbSize", c_uint16)]


class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("Format", WAVEFORMATEX), ("wValidBitsPerSample", c_uint16),
                ("dwChannelMask", c_uint32), ("SubFormat", GUID)]


CLSCTX_ALL = 0x17
STREAMFLAGS_LOOPBACK = 0x00020000
BUFFERFLAGS_SILENT = 0x2
EXTENSIBLE = 0xFFFE


def _guid(text: str) -> GUID:
    value = GUID()
    ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(value))
    return value


def _method(obj: c_void_p, index: int, *argtypes) -> Callable:
    """Method ``index`` of a COM object; a failed HRESULT raises OSError."""
    vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    function = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtable[index])
    return lambda *args: function(obj, *args)


def _release(obj: c_void_p) -> None:
    if obj:
        vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        ctypes.WINFUNCTYPE(c_uint32, c_void_p)(vtable[2])(obj)


class Loopback:
    """The default playback device, opened for loopback capture in shared mode."""

    def __init__(self) -> None:
        self.client, self.capture = c_void_p(), c_void_p()
        enumerator, device = c_void_p(), c_void_p()
        ole32 = ctypes.oledll.ole32
        try:
            ole32.CoCreateInstance(byref(_guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")), None, CLSCTX_ALL,
                                   byref(_guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")), byref(enumerator))
            _method(enumerator, 4, c_uint32, c_uint32, POINTER(c_void_p))(0, 0, byref(device))  # render, console
            _method(device, 3, POINTER(GUID), c_uint32, c_void_p, POINTER(c_void_p))(
                byref(_guid("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")), CLSCTX_ALL, None, byref(self.client))
            mix = POINTER(WAVEFORMATEX)()
            _method(self.client, 8, POINTER(POINTER(WAVEFORMATEX)))(byref(mix))
            try:
                fmt = mix.contents
                tag = fmt.wFormatTag
                if tag == EXTENSIBLE:
                    tag = ctypes.cast(mix, POINTER(WAVEFORMATEXTENSIBLE)).contents.SubFormat.Data1
                if (tag, fmt.wBitsPerSample) not in ((IEEE_FLOAT, 32), (PCM, 16)):
                    raise RecordError(f"不支持这个声卡的格式（{fmt.wBitsPerSample} 位，格式 {tag}）")
                self.float_samples = tag == IEEE_FLOAT
                self.rate, self.channels, self.block = fmt.nSamplesPerSec, fmt.nChannels, fmt.nBlockAlign
                _method(self.client, 3, c_uint32, c_uint32, c_int64, c_int64, POINTER(WAVEFORMATEX), c_void_p)(
                    0, STREAMFLAGS_LOOPBACK, 10_000_000, 0, mix, None)  # shared, 1 s buffer
            finally:
                ctypes.windll.ole32.CoTaskMemFree(mix)  # returns nothing, so not through oledll
            _method(self.client, 14, POINTER(GUID), POINTER(c_void_p))(
                byref(_guid("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")), byref(self.capture))
        except Exception:
            self.close()
            raise
        finally:
            _release(device)
            _release(enumerator)
        self._next = _method(self.capture, 5, POINTER(c_uint32))
        self._get = _method(self.capture, 3, POINTER(POINTER(ctypes.c_ubyte)), POINTER(c_uint32),
                            POINTER(c_uint32), POINTER(c_uint64), POINTER(c_uint64))
        self._free = _method(self.capture, 4, c_uint32)

    def start(self) -> None:
        _method(self.client, 10)()

    def packet(self) -> bytes | None:
        """The next packet of interleaved samples, or None when there is nothing new."""
        size = c_uint32()
        self._next(byref(size))
        if not size.value:
            return None
        data, frames, flags = POINTER(ctypes.c_ubyte)(), c_uint32(), c_uint32()
        self._get(byref(data), byref(frames), byref(flags), None, None)
        try:
            count = frames.value * self.block
            return bytes(count) if flags.value & BUFFERFLAGS_SILENT else ctypes.string_at(data, count)
        finally:
            self._free(frames.value)

    def close(self) -> None:
        if self.client:
            try:
                _method(self.client, 11)()  # Stop
            except OSError:
                pass
        _release(self.capture)
        _release(self.client)
        self.capture, self.client = c_void_p(), c_void_p()


def record(path: Path, cancel: threading.Event, report: Callable[[str, float], None] | None = None,
           wait: float = WAIT, silence: float = SILENCE, longest: float = LONGEST) -> float:
    """Record the speakers into ``path``; returns the seconds kept (0 if no sound came).

    ``report(state, seconds)`` is called a few times a second with state
    "waiting" (seconds waited) or "recording" (seconds recorded).
    """
    ctypes.oledll.ole32.CoInitializeEx(None, 0)
    device = None
    try:
        try:
            device = Loopback()
        except OSError as error:
            raise RecordError(f"打不开播放设备：{error}") from error
        writer = WavWriter(path, device.rate, device.channels, device.float_samples)
        try:
            take = Take(writer, device.rate, device.channels, device.float_samples, time.monotonic(),
                        wait, silence, longest)
            device.start()
            told = 0.0
            while not take.done and not cancel.is_set():
                try:
                    data = device.packet()
                except OSError as error:
                    raise RecordError(f"播放设备断开了：{error}") from error
                now = time.monotonic()
                if data is None:
                    take.idle(now)
                    cancel.wait(0.005)
                else:
                    take.feed(data, now)
                if report and now - told >= 0.25:
                    told = now
                    if take.began is None:
                        report("waiting", now - take.opened)
                    else:
                        report("recording", take.seconds)
            return take.seconds
        finally:
            writer.close()
    finally:
        if device:
            device.close()
        ctypes.windll.ole32.CoUninitialize()
