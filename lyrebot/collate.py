"""Sort song names: English ones first, alphabetically, then Chinese ones by pinyin.

Windows' zh-CN locale orders Han characters by pinyin, letters alphabetically
whatever their case, and numbers by value ("2" before "10"); it puts letters
before Han too, but LyreBot says so itself rather than leave it to the locale.
Without Windows (or if the call fails) names fall back to plain string order.
"""

from __future__ import annotations

import ctypes

LCMAP_SORTKEY = 0x00000400
SORT_DIGITSASNUMBERS = 0x00000008


def _windows_key(text: str) -> bytes | None:
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return None
    flags = LCMAP_SORTKEY | SORT_DIGITSASNUMBERS
    size = kernel32.LCMapStringEx("zh-CN", flags, text, len(text), None, 0, None, None, 0)
    if size <= 0:
        return None
    buffer = ctypes.create_string_buffer(size)
    if kernel32.LCMapStringEx("zh-CN", flags, text, len(text), buffer, size, None, None, 0) <= 0:
        return None
    return buffer.raw[:size]


def pinyin_key(text: str) -> tuple[int, bytes, str]:
    """A sort key: names that begin with an English letter or a digit first, then the rest; each in
    Windows' zh-CN order; the raw name to break ties."""
    first = text[:1]
    return (0 if first.isascii() and first.isalnum() else 1, _windows_key(text) or text.encode("utf-16-be"), text)
