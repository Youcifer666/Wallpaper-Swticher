"""
hotkey.py — System-wide hotkeys via RegisterHotKey (pure ctypes).

Runs a background thread with its own Windows message loop; the callback is
invoked from that thread, so the UI side must marshal it (e.g. queue + poll).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_MAP = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
           "shift": MOD_SHIFT, "win": MOD_WIN}

# Virtual-key codes for special keys
VK_MAP = {f"f{i}": 0x70 + i - 1 for i in range(1, 13)}
VK_MAP.update({"escape": 0x1B, "space": 0x20, "tab": 0x09,
               "left": 0x25, "right": 0x27, "up": 0x26, "down": 0x28})


def parse_combo(combo: str) -> tuple[int, int]:
    """'ctrl+alt+w' -> (MOD_CONTROL | MOD_ALT, ord('W'))."""
    mods = 0
    key = "w"
    for part in combo.lower().split("+"):
        part = part.strip()
        if not part:
            continue
        if part in MOD_MAP:
            mods |= MOD_MAP[part]
        else:
            key = part
    if key in VK_MAP:
        vk = VK_MAP[key]
    elif len(key) == 1:
        vk = ord(key.upper())
    else:
        raise ValueError(f"unsupported hotkey key: {key!r}")
    return mods, vk


class GlobalHotkey:
    """Register a system-wide hotkey and invoke `callback` when pressed."""

    def __init__(self, combo: str, callback) -> None:
        self.combo = combo
        self.callback = callback
        self.mods, self.vk = parse_combo(combo)
        self.failed = False
        self.registered = threading.Event()  # set once RegisterHotKey succeeded
        self._id = 1
        self._thread: threading.Thread | None = None
        self._thread_id = 0

    def start(self) -> None:
        self.registered.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, self._id, self.mods, self.vk):
            self.failed = True  # hotkey is already taken by another app
            return
        self.registered.set()
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == self._id:
                try:
                    self.callback()
                except Exception:
                    pass
        user32.UnregisterHotKey(None, self._id)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread_id = 0
