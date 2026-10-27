"""Sort Chinese names by pinyin, the way Windows does for zh-CN.

Windows' zh-CN locale orders Han characters by pinyin, and numbers by value
("2" before "10").  Without Windows (or if the call fails) names fall back to
plain string order.
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


def pinyin_key(text: str) -> tuple[bytes, str]:
    """A sort key: pinyin order first, the raw name to break ties."""
    return (_windows_key(text) or text.encode("utf-16-be"), text)
