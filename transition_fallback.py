"""
transition.py — animated wallpaper transitions, ported from YASB.

Windows only ever cross-fades the wallpaper; there is no API to customize how
it changes (IDesktopWallpaper has no transition options at all). So this fakes
a fancier one: a short animation plays over the desktop that reveals the NEW
wallpaper, the real wallpaper is committed underneath while the final frame is
still on screen, and then the overlay is removed. Windows' built-in cross-fade
happens behind the overlay, where nobody can see it.

The overlay is a native window (pure ctypes — no PyQt6) parented to WorkerW,
the hidden window behind the desktop icons, so it draws above the wallpaper
and below the icons — the same trick YASB's Qt-based WallpaperEngine uses.

Layout rules are ported from YASB's engine (verified there against Windows 11
26100), because the overlay's last frame must land on exactly the pixels the
real wallpaper settles on, or the desktop visibly jumps when the overlay
closes. The rules that are not obvious:

  * Fill and span anchor vertically at a third of the overflow, not a half.
  * Landscape images wider than 2.22:1 are spanned across every monitor
    ("autospan") even in fill and fit mode.
  * Integer division truncates toward zero, not floor.

Not ported: Windows' transcode size cap for gigantic images, so overlays for
very large sources can land a pixel or two off (YASB decodes that from the
TranscodedImageCache registry blob).
"""

from __future__ import annotations

import ctypes
import math
import os
import threading
import time
import winreg
from ctypes import wintypes

try:
    from PIL import Image, ImageDraw
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

from wallpaper_api import WallpaperManager

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------------------
# Win32 constants and ctypes plumbing
# ---------------------------------------------------------------------------

WM_SPAWN_WORKER = 0x052C        # secretly tells Progman to spawn WorkerW
WM_ERASEBKGND = 0x0014
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_EX_TRANSPARENT = 0x00000020  # clicks fall through the overlay
WS_EX_NOACTIVATE = 0x08000000
SW_SHOW = 5
BI_RGB = 0
DIB_RGB_COLORS = 0
ERROR_CLASS_ALREADY_EXISTS = 1410

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
EnumChildProc = EnumWindowsProc
MonitorEnumProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE,
    ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                 wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SetParent.restype = wintypes.HWND
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.EnumWindows.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL
user32.EnumChildWindows.argtypes = [wintypes.HWND, EnumChildProc, wintypes.LPARAM]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.EnumDisplayMonitors.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.RECT), MonitorEnumProc, wintypes.LPARAM]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ulonglong)]
gdi32.SetDIBitsToDevice.restype = ctypes.c_int
gdi32.SetDIBitsToDevice.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, wintypes.DWORD, wintypes.DWORD,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON), ("hCursor", ctypes.c_void_p),
        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC), ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT), ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_ubyte * 32),
    ]


def _static_wndproc(hwnd, msg, wp, lp):
    # We repaint the whole window every frame, so there is nothing to do in
    # WM_PAINT beyond validating the region, and nothing to erase at all.
    if msg == WM_ERASEBKGND:
        return 1
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


_WNDPROC_REF = WNDPROC(_static_wndproc)  # keep alive for the process lifetime

TRANSITION_NAMES = {
    "circle": "Circle Reveal",
    "diamond": "Diamond Reveal",
    "split": "Split Open",
    "slide_top": "Slide from Top",
    "fade": "Cross Fade",
    "off": "Off (instant)",
}

ANIMATION_MS = 1200    # same duration YASB uses
FRAME_MS = 16          # ~60 fps
HOLD_MS = 1000         # keep the final frame up while Windows commits



# ---------------------------------------------------------------------------
# Desktop layout — what Windows actually draws (ported from YASB)
# ---------------------------------------------------------------------------

def _trunc_div(a: int, b: int) -> int:
    """Divide the way C does (toward zero), not the way Python floors.

    Windows truncates, and the numerator is negative whenever the image
    overflows the monitor — the two differ by a pixel there.
    """
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _transcoded_wallpaper_path() -> str:
    """Path to Windows' cached transcoded copy of the CURRENT wallpaper."""
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, r"Microsoft\Windows\Themes\TranscodedWallpaper")


def _read_background_color() -> tuple[int, int, int]:
    """The Windows desktop background color (shows around centered images)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Colors") as k:
            r, g, b = map(int, winreg.QueryValueEx(k, "Background")[0].split())
            return (r, g, b)
    except (OSError, ValueError):
        return (0, 0, 0)


def _panorama_threshold(landscape: bool) -> float:
    """Aspect ratio past which Windows spans a wallpaper across the monitors.

    Stored in thousandths in the registry; 2.22 for landscape images and 1.0
    for portrait ones by default.
    """
    default = 2.22 if landscape else 1.0
    name = "PanoramaThreshold" if landscape else "PanoramaPortraitThreshold"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
            value = int(winreg.QueryValueEx(k, name)[0])
    except (FileNotFoundError, OSError, ValueError):
        return default
    return value / 1000.0 if value else default


def _monitors_tile_a_rectangle(areas) -> bool:
    """Whether the monitors cover their bounding box exactly.

    Windows refuses to span unless the monitor region is a single rectangle
    with every monitor the same size — a step or gap turns spanning off.
    """
    if not areas:
        return False
    if len({(dw, dh) for _, _, dw, dh in areas}) != 1:
        return False
    left = min(dx for dx, _, _, _ in areas)
    top = min(dy for _, dy, _, _ in areas)
    right = max(dx + dw for dx, _, dw, _ in areas)
    bottom = max(dy + dh for _, dy, _, dh in areas)
    covered = sum(dw * dh for _, _, dw, dh in areas)
    return covered == (right - left) * (bottom - top)


def _wants_autospan(img, areas) -> bool:
    landscape = img.width >= img.height
    aspect = img.width / max(1, img.height)
    return aspect >= _panorama_threshold(landscape) and _monitors_tile_a_rectangle(areas)


def _cover_or_contain(img, dw: int, dh: int, cover: bool):
    """Scale to cover or contain dw x dh, rounding the way Windows rounds.

    Windows works the ratios out in single precision, takes max for cover and
    min for contain, then adds a half to each axis before truncating.
    """
    ratios = (dw / img.width, dh / img.height)
    s = max(ratios) if cover else min(ratios)
    return img.resize((int(img.width * s + 0.5), int(img.height * s + 0.5)),
                      Image.BILINEAR)


def _layout_image(img, areas, vw: int, vh: int, style: str, bg):
    """Lay an image out over the virtual desktop the way Windows would."""
    canvas = Image.new("RGB", (vw, vh), bg)

    # Only span and tile are laid out against the whole virtual desktop;
    # Windows computes every other mode independently per monitor. Fill and
    # fit get rewritten to span when the image is panorama-wide (autospan),
    # but keep their cover-vs-contain and anchoring behaviour.
    if style == "span" or (style in ("fill", "fit") and _wants_autospan(img, areas)):
        cover = style != "fit"
        sc = _cover_or_contain(img, vw, vh, cover)
        ox = _trunc_div(vw - sc.width, 2)
        oy = _trunc_div(vh - sc.height, 3 if cover else 2)
        for dx, dy, _, _ in areas:
            canvas.paste(sc, (ox - dx, oy - dy))
        return canvas

    if style == "tile":
        iw, ih = img.size
        if iw > 0 and ih > 0:
            for ty in range(0, vh, ih):
                for tx in range(0, vw, iw):
                    canvas.paste(img, (tx, ty))
        return canvas

    for dx, dy, dw, dh in areas:
        if style == "stretch":
            sc = img.resize((max(1, dw), max(1, dh)), Image.BILINEAR)
        elif style in ("fill", "fit"):
            sc = _cover_or_contain(img, dw, dh, cover=style == "fill")
        else:  # center: never rescaled, cropped by the monitor rect
            sc = img
        ox = _trunc_div(dw - sc.width, 2)
        oy = _trunc_div(dh - sc.height, 3 if style == "fill" else 2)
        canvas.paste(sc, (dx + ox, dy + oy))
    return canvas



# ---------------------------------------------------------------------------
# Reveal shapes — the animation itself (ported from YASB)
# ---------------------------------------------------------------------------

def _reveal_mask(areas, vw: int, vh: int, t: float, animation: str):
    """White where the NEW wallpaper should show at progress `t` (0..1)."""
    mask = Image.new("L", (vw, vh), 0)
    draw = ImageDraw.Draw(mask)
    if animation == "fade":
        draw.rectangle([0, 0, vw, vh], fill=int(255 * t))
        return mask
    for dx, dy, dw, dh in areas:
        if animation == "diamond":
            cx, cy = dx + dw // 2, dy + dh // 2
            max_r = max(abs(cx - dx) + abs(cy - dy),
                        abs(cx - (dx + dw)) + abs(cy - dy),
                        abs(cx - dx) + abs(cy - (dy + dh)),
                        abs(cx - (dx + dw)) + abs(cy - (dy + dh)))
            rr = max(1.0, float(max_r) * t)
            draw.polygon([(cx, cy - rr), (cx + rr, cy),
                          (cx, cy + rr), (cx - rr, cy)], fill=255)
        elif animation == "split":
            half = int(dh / 2 * t)
            draw.rectangle([dx, dy, dx + dw, dy + max(1, half)], fill=255)
            draw.rectangle([dx, dy + dh - max(1, half), dx + dw, dy + dh],
                           fill=255)
        elif animation == "slide_top":
            cx = dx + dw // 2
            max_r = math.hypot(dw / 2, dh)
            r = max(1.0, max_r * t)
            # ImageDraw clips to the monitor rect for us, which is exactly
            # the circle-intersected-with-rect shape YASB builds.
            draw.ellipse([cx - r, dy - r, cx + r, dy + r], fill=255)
        else:  # circle
            cx, cy = dx + dw // 2, dy + dh // 2
            max_r = max(math.hypot(cx - dx, cy - dy),
                        math.hypot(cx - (dx + dw), cy - dy),
                        math.hypot(cx - dx, cy - (dy + dh)),
                        math.hypot(cx - (dx + dw), cy - (dy + dh)))
            r = max(1.0, max_r * t)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    return mask


# ---------------------------------------------------------------------------
# WorkerW and the overlay window
# ---------------------------------------------------------------------------

def _enum_physical_monitors():
    """(left, top, width, height) for every monitor, virtual-screen coords."""
    rects = []

    def _cb(_hmon, _hdc, lprect, _lp):
        r = lprect.contents
        rects.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)
    return rects


def _locate_workerw() -> int:
    """Find (or spawn) the WorkerW window that sits behind the desktop icons."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return 0
    user32.SendMessageTimeoutW(progman, WM_SPAWN_WORKER, 0, 0, 0, 1000,
                               ctypes.byref(ctypes.c_ulonglong()))
    worker = wintypes.HWND()

    def _child_proc(hwnd, _lp):
        if worker.value:
            return False
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, len(buf))
        if buf.value == "WorkerW":
            worker.value = hwnd
            return False
        return True

    user32.EnumChildWindows(wintypes.HWND(progman), EnumChildProc(_child_proc), 0)

    if not worker.value:
        def _enum_proc(hwnd, _lp):
            if worker.value:
                return False
            if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
                candidate = user32.FindWindowExW(None, hwnd, "WorkerW", None)
                if candidate:
                    worker.value = candidate
                    return False
            return True

        user32.EnumWindows(EnumWindowsProc(_enum_proc), 0)

    if not worker.value:
        return 0
    user32.ShowWindow(worker, SW_SHOW)
    return worker.value


def _register_overlay_class() -> bool:
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = _WNDPROC_REF
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = "YouciferWallTransition"
    if not user32.RegisterClassExW(ctypes.byref(wc)):
        return kernel32.GetLastError() == ERROR_CLASS_ALREADY_EXISTS
    return True


def _pump_messages() -> None:
    msg = wintypes.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))



# ---------------------------------------------------------------------------
# The player
# ---------------------------------------------------------------------------

class WallpaperTransition:
    """Plays the reveal animation over the desktop, then commits the wallpaper.

    `play()` returns False (without starting anything) when the animation
    cannot be shown — no WorkerW, no Pillow, a per-monitor target, another
    transition already running — and the caller should set the wallpaper
    directly instead.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._abort = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def abort(self) -> None:
        """Stop without committing (used when the app is shutting down)."""
        self._abort.set()

    def play(self, new_path: str, style: str = "fill",
             animation: str = "circle", monitor_index: int | None = None,
             on_commit=None, on_done=None) -> bool:
        if (not HAVE_PIL or not animation or animation == "off"
                or monitor_index is not None):
            return False
        if not os.path.isfile(new_path) or self.running:
            return False
        if not _register_overlay_class():
            return False
        self._abort.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            args=(new_path, style, animation, on_commit, on_done))
        self._thread.start()
        return True

    def _run(self, new_path, style, animation, on_commit, on_done) -> None:
        ok, error = False, None
        try:
            ok, error = self._animate_and_commit(new_path, style, animation,
                                                 on_commit)
        except Exception as exc:  # never leave the desktop covered
            error = str(exc)
        finally:
            if on_done is not None:
                try:
                    on_done(ok, error)
                except Exception:
                    pass

    def _animate_and_commit(self, new_path, style, animation, on_commit):
        bg = _read_background_color()

        try:
            new_img = Image.open(new_path).convert("RGB")
        except OSError as exc:
            return False, f"cannot open {os.path.basename(new_path)}: {exc}"
        try:
            # Windows lays out its own transcoded copy of the old wallpaper,
            # so that — not the source file — is what the desktop shows now.
            old_img = Image.open(_transcoded_wallpaper_path()).convert("RGB")
        except OSError:
            old_img = Image.new("RGB", new_img.size, bg)  # fade in from the bg

        worker = _locate_workerw()
        if not worker:
            return False, "no WorkerW"
        wr = wintypes.RECT()
        user32.GetWindowRect(worker, ctypes.byref(wr))

        # Overlay-local monitor areas: WorkerW covers the virtual desktop, so
        # shifting by its origin gives coordinates inside the overlay window.
        areas = [(ml - wr.left, mt - wr.top, mw, mh)
                 for ml, mt, mw, mh in _enum_physical_monitors()]
        if not areas:
            return False, "no monitors"
        vw = max(dx + dw for dx, _, dw, _ in areas)
        vh = max(dy + dh for _, dy, _, dh in areas)

        old_canvas = _layout_image(old_img, areas, vw, vh, style, bg)
        new_canvas = _layout_image(new_img, areas, vw, vh, style, bg)

        hwnd = user32.CreateWindowExW(
            WS_EX_TRANSPARENT | WS_EX_NOACTIVATE, "YouciferWallTransition",
            None, WS_CHILD, 0, 0, vw, vh, wintypes.HWND(worker), None,
            kernel32.GetModuleHandleW(None), None)
        if not hwnd:
            return False, "could not create overlay window"
        hdc = user32.GetDC(hwnd)
        try:
            self._draw_frame(hdc, areas, vw, vh, old_canvas, new_canvas,
                             0.0, animation)
            user32.ShowWindow(hwnd, SW_SHOW)

            # The animation: OutQuad easing, like YASB's QTimeLine setup.
            start = time.perf_counter()
            while True:
                if self._abort.is_set():
                    return False, "aborted"
                t = (time.perf_counter() - start) * 1000.0 / ANIMATION_MS
                if t >= 1.0:
                    break
                eased = 1.0 - (1.0 - t) ** 2
                self._draw_frame(hdc, areas, vw, vh, old_canvas, new_canvas,
                                 eased, animation)
                _pump_messages()
                time.sleep(FRAME_MS / 1000.0)
            self._draw_frame(hdc, areas, vw, vh, old_canvas, new_canvas,
                             1.0, animation)

            # Final frame is frozen on screen: commit the real wallpaper
            # underneath so Windows' cross-fade happens where nobody sees it.
            if on_commit is not None:
                try:
                    on_commit()
                except Exception as exc:
                    return False, str(exc)

            end = time.perf_counter() + HOLD_MS / 1000.0
            while time.perf_counter() < end and not self._abort.is_set():
                _pump_messages()
                time.sleep(FRAME_MS / 1000.0)
            return True, None
        finally:
            user32.ReleaseDC(hwnd, hdc)
            user32.DestroyWindow(hwnd)
            _pump_messages()

    @staticmethod
    def _draw_frame(hdc, areas, vw, vh, old_canvas, new_canvas,
                    t: float, animation: str) -> None:
        if t >= 1.0:
            frame = new_canvas
        else:
            mask = _reveal_mask(areas, vw, vh, t, animation)
            frame = old_canvas.copy()
            frame.paste(new_canvas, (0, 0), mask)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = vw
        bmi.bmiHeader.biHeight = -vh  # negative: top-down rows
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        data = frame.tobytes("raw", "BGRX")
        gdi32.SetDIBitsToDevice(hdc, 0, 0, vw, vh, 0, 0, 0, vh,
                                data, ctypes.byref(bmi), DIB_RGB_COLORS)

