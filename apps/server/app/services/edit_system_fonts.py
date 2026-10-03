"""Enumerate font family names on the local host, without exposing file paths."""

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def system_font_families() -> list[str]:
    names: set[str] = set()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class LogFont(ctypes.Structure):
            _fields_ = (
                [
                    (name, wintypes.LONG)
                    for name in ("height", "width", "escape", "orientation", "weight")
                ]
                + [
                    (name, wintypes.BYTE)
                    for name in (
                        "italic",
                        "underline",
                        "strike",
                        "charset",
                        "out",
                        "clip",
                        "quality",
                        "pitch",
                    )
                ]
                + [("family", wintypes.WCHAR * 32)]
            )

        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.POINTER(LogFont), ctypes.c_void_p, wintypes.DWORD, ctypes.c_ssize_t
        )

        @callback_type
        def collect(font, _metric, _kind, _data):
            family = font.contents.family
            if family and not family.startswith("@"):
                names.add(family)
            return 1

        gdi = ctypes.windll.gdi32
        gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi.CreateCompatibleDC.restype = wintypes.HDC
        gdi.DeleteDC.argtypes = [wintypes.HDC]
        gdi.EnumFontFamiliesExW.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(LogFont),
            callback_type,
            ctypes.c_ssize_t,
            wintypes.DWORD,
        ]
        dc = gdi.CreateCompatibleDC(None)
        if dc:
            try:
                query = LogFont()
                query.charset = 1
                gdi.EnumFontFamiliesExW(dc, ctypes.byref(query), collect, 0, 0)
            finally:
                gdi.DeleteDC(dc)
    return sorted(names, key=str.casefold)
