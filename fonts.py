"""
fonts.py — bundle & register the Alexandria font with Tk.

The TTF files live in ./assets/fonts and are registered as *private*
process fonts (AddFontResourceExW + FR_PRIVATE), so the app is
self-contained and nothing is installed system-wide.
"""

from __future__ import annotations

import ctypes
import glob
import os

gdi32 = ctypes.windll.gdi32

FR_PRIVATE = 0x10
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "fonts")
FAMILY = "Alexandria"
FALLBACK = "Segoe UI"

_registered = False
_verified_family = FAMILY


def register() -> bool:
    """Load the bundled TTFs into this process. Safe to call repeatedly."""
    global _registered
    if _registered:
        return True
    count = 0
    if os.path.isdir(FONT_DIR):
        for path in glob.glob(os.path.join(FONT_DIR, "*.ttf")):
            if gdi32.AddFontResourceExW(path, FR_PRIVATE, 0):
                count += 1
    _registered = count > 0
    return _registered


def family(root=None) -> str:
    """Return the font family to use ('Alexandria' if available)."""
    global _verified_family
    if not _registered:
        register()
    if _registered and root is not None:
        try:
            from tkinter import font as tkfont
            names = set(tkfont.families(root))
            if FAMILY in names:
                _verified_family = FAMILY
            else:
                _verified_family = FALLBACK
        except Exception:
            _verified_family = FALLBACK if not _registered else _verified_family
    return _verified_family


def f(size: int, weight: str = "normal") -> tuple:
    """Convenience font tuple, e.g. fonts.f(10) or fonts.f(10, 'bold')."""
    if weight == "bold":
        return (family(), size, "bold")
    return (family(), size)
