"""
tray.py — system tray icon with a popup menu (pure ctypes, no dependencies).

Runs its own hidden message window + message loop on a background thread.
Right-click shows the menu provided by `menu_provider()`; left-click posts
"toggle". Menu commands are delivered through the `notify(cmd)` callback
(which should marshal them onto the UI thread, e.g. via a queue).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import threading

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

WM_APP_TRAY = 0x8001          # WM_APP + 1, our tray callback message
WM_LBUTTONUP = 0x0207
WM_RBUTTONUP = 0x0205
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_NULL = 0x0000

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04

MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
MF_POPUP, MF_CHECKED = 0x0010, 0x0008
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
IDI_APPLICATION = 32512
ERROR_CLASS_ALREADY_EXISTS = 1410

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateWindowExW.restype = wt.HWND

class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON), ("hCursor", ctypes.c_void_p),
        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_ubyte * 16), ("hBalloonIcon", wt.HICON),
    ]


def _default_icon_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "tray.ico")


def make_icon_file(path: str) -> bool:
    """Generate a small icon with Pillow; returns True on success."""
    try:
        from PIL import Image, ImageDraw
        size = 64
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([3, 3, 61, 61], radius=14,
                            fill=(79, 140, 255, 255))
        d.rounded_rectangle([12, 16, 52, 42], radius=4,
                            fill=(250, 250, 252, 255))
        d.polygon([(12, 42), (24, 27), (34, 36), (44, 25), (52, 33),
                   (52, 42)], fill=(52, 199, 123, 255))
        d.rectangle([12, 46, 52, 50], fill=(28, 37, 48, 255))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        im.save(path, sizes=[(32, 32), (16, 16)])
        return True
    except Exception:
        return False
class TrayIcon:
    """Resident system tray icon. The menu provider returns nested tuples:
    (label, cmd, checked) leaf, (label, [children]) submenu, ("-",) separator."""

    def __init__(self, notify, menu_provider,
                 tooltip: str = "Youcifer Wallpaper",
                 icon_path: str | None = None) -> None:
        self._notify = notify
        self._menu_provider = menu_provider
        self._tooltip = tooltip
        self._icon_path = icon_path or _default_icon_path()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hwnd = None
        self._nid: NOTIFYICONDATAW | None = None
        self._proc_ref = None
        self._cmd_map: dict[int, str] = {}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._hwnd is not None:
            if self._nid is not None:
                shell32.Shell_NotifyIconW(NIM_DELETE,
                                          ctypes.byref(self._nid))
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        hinst = kernel32.GetModuleHandleW(None)

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_APP_TRAY:
                lp = lparam & 0xFFFF
                if lp == WM_LBUTTONUP:
                    try:
                        self._notify("toggle")
                    except Exception:
                        pass
                elif lp == WM_RBUTTONUP:
                    self._show_menu(hwnd)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc_ref = WNDPROC(wndproc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc_ref
        wc.hInstance = hinst
        wc.lpszClassName = "YouciferTrayWnd"
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            if kernel32.GetLastError() != ERROR_CLASS_ALREADY_EXISTS:
                return

        hwnd = user32.CreateWindowExW(
            0, "YouciferTrayWnd", "Youcifer Wallpaper", 0,
            0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            return
        self._hwnd = hwnd

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self._load_icon()
        nid.szTip = self._tooltip[:127]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            return
        self._nid = nid

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # -- icon & tooltip ---------------------------------------------------------

    def _load_icon(self):
        if not os.path.isfile(self._icon_path):
            make_icon_file(self._icon_path)
        hicon = user32.LoadImageW(
            None, self._icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if not hicon:
            hicon = user32.LoadIconW(None, IDI_APPLICATION)
        return hicon

    def set_tooltip(self, text: str) -> None:
        nid = self._nid
        if nid is None:
            return
        nid.uFlags = NIF_TIP
        nid.szTip = text[:127]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # -- popup menu ----------------------------------------------------------------

    def _show_menu(self, hwnd) -> None:
        items = self._menu_provider()
        if not items:
            return
        self._cmd_map = {}
        hmenu = user32.CreatePopupMenu()
        self._add_items(hmenu, items, iter(range(0x1000, 0x8000)))
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # the classic TrackPopupMenu focus quirk:
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenuEx(
            hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, hwnd, None)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(hmenu)
        if cmd:
            action = self._cmd_map.get(cmd)
            if action:
                try:
                    self._notify(action)
                except Exception:
                    pass

    def _add_items(self, hmenu, items, ids) -> None:
        for it in items:
            if it[0] == "-":
                user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            elif len(it) == 2:  # (label, [children])
                sub = user32.CreatePopupMenu()
                self._add_items(sub, it[1], ids)
                user32.AppendMenuW(hmenu, MF_STRING | MF_POPUP, sub, it[0])
            else:               # (label, cmd, checked)
                label, action, checked = it
                cmd_id = next(ids)
                self._cmd_map[cmd_id] = action
                flags = MF_STRING | (MF_CHECKED if checked else 0)
                user32.AppendMenuW(hmenu, flags, cmd_id, label)