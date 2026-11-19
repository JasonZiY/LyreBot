"""Hear a score through the computer's speakers instead of the game: a player backend that sends each key
as a note to the MIDI synthesizer Windows comes with (Microsoft GS Wavetable Synth).

Only the standard library: winmm through ctypes.  Nothing here touches Windows until ``Audition()`` is
created.  Notes should be timed by ``timeline.compile_ringing``: under the tapped timeline every note would
be cut off 35 ms after it was struck.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import threading
import time
from typing import Any, Callable

from .arrange import KEY_OF
from .keyboard import stop_pressed

NOTE_OF = {key: pitch for pitch, key in KEY_OF.items()}  # z = C3 (48), a = C4 (60), q = C5 (72), u = B5
PIANO, HARP = 1, 47  # General MIDI's Acoustic Grand Piano and Orchestral Harp, numbered from 1 as in its list
INSTRUMENTS = ((PIANO, "钢琴"), (HARP, "竖琴"), (9, "钢片琴"), (10, "钟琴"), (11, "八音盒"), (12, "颤音琴"),
               (13, "马林巴"), (16, "扬琴"), (25, "尼龙弦吉他"), (46, "弦乐拨奏"), (108, "筝"), (109, "卡林巴"))
VELOCITY = 96  # every note alike: the lyre has no loud and soft
CHANNEL = 0
SYNTH = "GS Wavetable"  # part of the name of Windows' own synthesizer
MIDI_MAPPER = 0xFFFFFFFF  # winmm's "whichever synthesizer Windows maps MIDI to", (UINT)-1
TAIL = 0.4  # seconds the last notes are given to fade after their release before the synthesizer is closed
NOTE_ON, NOTE_OFF, CONTROL, PROGRAM = 0x90, 0x80, 0xB0, 0xC0
ALL_SOUND_OFF, ALL_NOTES_OFF = 120, 123
HEARD = 0  # what ``Audition.foreground`` always answers: listening goes on whichever window is in front


class AuditionError(RuntimeError):
    pass


class MIDIOUTCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                ("szPname", wintypes.WCHAR * 32), ("wTechnology", wintypes.WORD), ("wVoices", wintypes.WORD),
                ("wNotes", wintypes.WORD), ("wChannelMask", wintypes.WORD), ("dwSupport", wintypes.DWORD)]


class _WinMM:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise AuditionError("试听只支持 Windows")
        dll = self.dll = ctypes.WinDLL("winmm")
        dll.midiOutGetNumDevs.argtypes = []
        dll.midiOutGetNumDevs.restype = wintypes.UINT
        dll.midiOutGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(MIDIOUTCAPSW), wintypes.UINT]
        dll.midiOutGetDevCapsW.restype = wintypes.UINT
        dll.midiOutOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.c_size_t,
                                    ctypes.c_size_t, wintypes.DWORD]
        dll.midiOutOpen.restype = wintypes.UINT
        dll.midiOutGetErrorTextW.argtypes = [wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        dll.midiOutGetErrorTextW.restype = wintypes.UINT
        for name, extra in (("midiOutShortMsg", [wintypes.DWORD]), ("midiOutReset", []), ("midiOutClose", [])):
            function = getattr(dll, name)
            function.argtypes = [wintypes.HANDLE, *extra]
            function.restype = wintypes.UINT

    def devices(self) -> list[str]:
        """The names of the MIDI outputs, by their number."""
        names = []
        for number in range(self.dll.midiOutGetNumDevs()):
            caps = MIDIOUTCAPSW()
            failed = self.dll.midiOutGetDevCapsW(number, ctypes.byref(caps), ctypes.sizeof(caps))
            names.append("" if failed else caps.szPname)
        return names

    def check(self, result: int) -> None:
        if result:
            text = ctypes.create_unicode_buffer(256)
            self.dll.midiOutGetErrorTextW(result, text, len(text))
            raise AuditionError(f"系统的 MIDI 合成器出错：{text.value or result}")

    def open(self, device: int) -> int:
        handle = wintypes.HANDLE()
        self.check(self.dll.midiOutOpen(ctypes.byref(handle), device, 0, 0, 0))  # CALLBACK_NULL
        return int(handle.value or 0)

    def short(self, handle: int, message: int) -> None:
        self.check(self.dll.midiOutShortMsg(handle, message))

    def reset(self, handle: int) -> None:
        self.dll.midiOutReset(handle)

    def close(self, handle: int) -> None:
        self.dll.midiOutClose(handle)


def message(status: int, first: int, second: int = 0) -> int:
    """A MIDI short message packed as winmm wants it: status in the low byte, then the two data bytes."""
    return status | first << 8 | second << 16


class Audition:
    """Sounds the keys instead of pressing them, for ``player.play``.

    The synthesizer is open from creation until ``release_all``, which the player always calls at the end.
    A key's press starts its note (ending the one still ringing on it); its release ends the note, which
    then fades as the instrument does.  Listening is never stopped by another window coming to the front;
    F8 stops it in any window, and so does ``interrupt`` once it answers true.  ``instrument`` may be
    called from another thread while it plays.
    """

    def __init__(self, program: int = PIANO, interrupt: Callable[[], bool] | None = None, *, api: Any = None,
                 stop_key: Callable[[], bool] = stop_pressed, sleep: Callable[[float], None] = time.sleep):
        self._api = api or _WinMM()
        self.interrupt = interrupt or (lambda: False)
        self.stop_key, self.sleep = stop_key, sleep
        self.lock = threading.Lock()  # one message at a time, and none once closed
        names = self._api.devices()
        if not names:
            raise AuditionError("这台电脑没有可以发声的 MIDI 设备")
        found = [number for number, name in enumerate(names) if SYNTH.lower() in name.lower()]
        self.device = found[0] if found else MIDI_MAPPER
        self.handle: int | None = self._api.open(self.device)
        self.sounding: set[int] = set()
        try:
            self.instrument(program)
        except AuditionError:
            self.release_all()
            raise

    def instrument(self, program: int) -> None:
        """Sound the notes from now on as General MIDI instrument ``program`` (numbered from 1); those
        already ringing keep theirs."""
        with self.lock:
            if self.handle is not None:
                self._api.short(self.handle, message(PROGRAM | CHANNEL, program - 1))

    def foreground(self) -> int:
        return HEARD

    def stop_pressed(self) -> bool:
        return self.interrupt() or self.stop_key()

    def send(self, keys: tuple[str, ...], down: bool) -> None:
        with self.lock:
            if self.handle is None:
                return
            for key in keys:
                note = NOTE_OF[key]
                if note in self.sounding:  # released, or struck again while it rang
                    self._api.short(self.handle, message(NOTE_OFF | CHANNEL, note))
                    self.sounding.discard(note)
                if down:
                    self._api.short(self.handle, message(NOTE_ON | CHANNEL, note, VELOCITY))
                    self.sounding.add(note)

    def release_all(self) -> None:
        """Silence and close.  Stopped part way, it falls silent at once; at the end of the song, once every
        note was let go, the last ones are given ``TAIL`` to fade first."""
        with self.lock:  # from here on nothing else goes to the synthesizer
            if self.handle is None:
                return
            handle, self.handle = self.handle, None
            cut = bool(self.sounding)
            self.sounding.clear()
        try:
            if cut:
                self._api.short(handle, message(CONTROL | CHANNEL, ALL_SOUND_OFF))
            else:
                self.sleep(TAIL)
            self._api.short(handle, message(CONTROL | CHANNEL, ALL_NOTES_OFF))
        except AuditionError:
            pass
        finally:
            self._api.reset(handle)
            self._api.close(handle)
