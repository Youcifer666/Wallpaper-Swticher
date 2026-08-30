"""
wallpaper_api.py — Native Windows wallpaper changer.

Changes the REAL Windows wallpaper (the one you see in
Settings > Personalization > Background) -- no overlay windows.

Primary method:  IDesktopWallpaper COM interface (Windows 10/11)
                 - supports per-monitor wallpapers and fit styles
Fallback method: SystemParametersInfoW(SPI_SETDESKWALLPAPER) + registry
                 (works on older Windows, all-monitors only)

Pure ctypes -- no third-party dependencies.
"""

from __future__ import annotations

import ctypes
import os
import uuid
import winreg
from ctypes import POINTER, Structure, byref, c_uint, c_ulong, c_ushort, c_void_p
from ctypes.wintypes import BOOL, LPCWSTR, UINT

ole32 = ctypes.OleDLL("ole32")
user32 = ctypes.windll.user32

# ---------------------------------------------------------------------------
# COM plumbing
# ---------------------------------------------------------------------------


class GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(s: str) -> GUID:
    g = GUID()
    ctypes.memmove(byref(g), uuid.UUID(s).bytes_le, 16)
    return g


CLSID_DesktopWallpaper = _guid("{C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD}")
IID_IDesktopWallpaper = _guid("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")
CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2

# DESKTOP_WALLPAPER_POSITION enum
POSITIONS = {
    "center": 0,
    "tile": 1,
    "stretch": 2,
    "fit": 3,
    "fill": 4,
    "span": 5,
}
POSITION_NAMES = {
    "fill": "Fill (crop to screen)",
    "fit": "Fit (letterbox)",
    "stretch": "Stretch (distort)",
    "tile": "Tile",
    "center": "Center (1:1)",
    "span": "Span across monitors",
}


class RECT(Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _check(hr: int, what: str) -> None:
    if hr < 0:  # FAILED(hresult)
        raise OSError(f"{what} failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})")


class _DesktopWallpaper:
    """Raw ctypes wrapper around the IDesktopWallpaper COM interface.

    Vtable layout (after the 3 IUnknown entries):
      3  SetWallpaper(monitorID, wallpaper)
      4  GetWallpaper(monitorID, *wallpaper)
      5  GetMonitorDevicePathAt(index, *monitorID)
      6  GetMonitorDevicePathCount(*count)
      7  GetMonitorRECT(monitorID, *rect)
      8  SetBackgroundColor(color)
     10  SetPosition(position)
     11  GetPosition(*position)
     17  Enable(enable)
    """

    def __init__(self) -> None:
        try:
            ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        except OSError:
            pass  # already initialized on this thread
        ptr = c_void_p()
        ole32.CoCreateInstance(
            byref(CLSID_DesktopWallpaper),
            None,
            CLSCTX_ALL,
            byref(IID_IDesktopWallpaper),
            byref(ptr),
        )
        self._ptr = ptr

    def _method(self, index: int, restype, *argtypes):
        # self._ptr[0] is the vtable pointer; pick entry `index` from it.
        obj = ctypes.cast(self._ptr, POINTER(c_void_p))
        vtbl = ctypes.cast(obj[0], POINTER(c_void_p))
        fn = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])
        return lambda *args: fn(self._ptr, *args)

    # -- monitors -----------------------------------------------------------

    def monitor_count(self) -> int:
        fn = self._method(6, ctypes.HRESULT, POINTER(UINT))
        count = UINT(0)
        _check(fn(byref(count)), "GetMonitorDevicePathCount")
        return count.value

    def monitor_id(self, index: int) -> str:
        fn = self._method(5, ctypes.HRESULT, UINT, POINTER(c_void_p))
        out = c_void_p()
        _check(fn(index, byref(out)), "GetMonitorDevicePathAt")
        try:
            return ctypes.wstring_at(out)
        finally:
            ole32.CoTaskMemFree(out)

    def monitor_rect(self, monitor_id: str) -> tuple[int, int, int, int]:
        fn = self._method(7, ctypes.HRESULT, LPCWSTR, POINTER(RECT))
        rect = RECT()
        _check(fn(monitor_id, byref(rect)), "GetMonitorRECT")
        return rect.left, rect.top, rect.right, rect.bottom

    # -- wallpaper ----------------------------------------------------------

    def set_wallpaper(self, path: str, monitor_id: str | None = None) -> None:
        fn = self._method(3, ctypes.HRESULT, LPCWSTR, LPCWSTR)
        _check(fn(monitor_id, str(path)), "SetWallpaper")

    def get_wallpaper(self, monitor_id: str | None = None) -> str:
        fn = self._method(4, ctypes.HRESULT, LPCWSTR, POINTER(c_void_p))
        out = c_void_p()
        _check(fn(monitor_id, byref(out)), "GetWallpaper")
        try:
            return ctypes.wstring_at(out)
        finally:
            ole32.CoTaskMemFree(out)

    def set_position(self, position: int) -> None:
        fn = self._method(10, ctypes.HRESULT, UINT)
        _check(fn(position), "SetPosition")

    def get_position(self) -> int:
        fn = self._method(11, ctypes.HRESULT, POINTER(UINT))
        pos = UINT(0)
        _check(fn(byref(pos)), "GetPosition")
        return pos.value

    def set_background_color(self, color_ref: int) -> None:
        """color_ref is a COLORREF: 0x00BBGGRR."""
        fn = self._method(8, ctypes.HRESULT, UINT)
        _check(fn(color_ref), "SetBackgroundColor")

    def enable(self, enabled: bool) -> None:
        fn = self._method(17, ctypes.HRESULT, BOOL)
        _check(fn(bool(enabled)), "Enable")


# ---------------------------------------------------------------------------
# Legacy fallback (no COM): SystemParametersInfoW + registry style keys
# ---------------------------------------------------------------------------

SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

# style -> (WallpaperStyle, TileWallpaper) registry values
_REG_STYLES = {
    "center": ("0", "0"),
    "tile": ("0", "1"),
    "stretch": ("2", "0"),
    "fit": ("6", "0"),
    "fill": ("10", "0"),
    "span": ("22", "0"),
}


def _legacy_set_wallpaper(path: str, style: str) -> None:
    style_val, tile_val = _REG_STYLES.get(style, _REG_STYLES["fill"])
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, style_val)
        winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, tile_val)
    if not user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, str(path), SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _legacy_get_wallpaper() -> str:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
        value, _ = winreg.QueryValueEx(key, "Wallpaper")
        return value


# ---------------------------------------------------------------------------
# High-level manager used by the GUI
# ---------------------------------------------------------------------------


class WallpaperManager:
    """Friendly API. Uses IDesktopWallpaper when available, else legacy."""

    def __init__(self) -> None:
        self._dw = None
        try:
            dw = _DesktopWallpaper()
            dw.monitor_count()  # probe that the interface really works
            self._dw = dw
        except Exception:
            self._dw = None

    @property
    def per_monitor(self) -> bool:
        """True if this system supports per-monitor wallpapers."""
        return self._dw is not None

    def monitors(self) -> list:
        """List of monitors: {'index': n, 'id': device-path, 'size': (w, h)}."""
        result = []
        if self._dw is None:
            return result
        for i in range(self._dw.monitor_count()):
            mid = self._dw.monitor_id(i)
            l, t, r, b = self._dw.monitor_rect(mid)
            result.append({"index": i, "id": mid, "size": (r - l, b - t)})
        return result

    def set_wallpaper(
        self,
        path: str,
        monitor_index: int | None = None,
        style: str = "fill",
    ) -> None:
        """Apply `path` as the wallpaper.

        monitor_index: None = all monitors, otherwise 0-based monitor index.
        style: fill / fit / stretch / tile / center / span.
        """
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        if self._dw is not None:
            monitor_id = None
            if monitor_index is not None:
                monitors = self.monitors()
                if monitor_index >= len(monitors):
                    raise IndexError(f"no monitor {monitor_index}")
                monitor_id = monitors[monitor_index]["id"]
            self._dw.set_wallpaper(path, monitor_id)
            try:
                self._dw.set_position(POSITIONS.get(style, POSITIONS["fill"]))
            except OSError:
                pass  # position is cosmetic; ignore failures
        else:
            _legacy_set_wallpaper(path, style)

    def get_wallpaper(self, monitor_index: int | None = None) -> str:
        if self._dw is not None:
            monitor_id = None
            if monitor_index is not None:
                monitors = self.monitors()
                if monitor_index < len(monitors):
                    monitor_id = monitors[monitor_index]["id"]
            try:
                return self._dw.get_wallpaper(monitor_id)
            except OSError:
                pass
        try:
            return _legacy_get_wallpaper()
        except OSError:
            return ""


if __name__ == "__main__":
    # Quick manual test: prints detected monitors and the current wallpaper.
    mgr = WallpaperManager()
    print("Per-monitor support:", mgr.per_monitor)
    for m in mgr.monitors():
        print(f"  Monitor {m['index']}: {m['size'][0]}x{m['size'][1]}  id={m['id']}")
    print("Current wallpaper:", mgr.get_wallpaper())
