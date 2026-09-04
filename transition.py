"""
transition.py — animated wallpaper transitions, powered by YASB's engine.

Windows only ever cross-fades the wallpaper; there is no API to customize how
it changes. So this fakes a fancier one: YASB's Qt-based WallpaperEngine
(transition_engine.py, ported from amnweb/yasb) plays a short animation over
the desktop that reveals the NEW wallpaper, the real wallpaper is committed
underneath while the final frame is still on screen, and then the overlay is
removed. Windows' built-in cross-fade happens behind the overlay, where nobody
can see it.

Threading note: a QApplication must live (and die) on the thread that created
it, and that cannot safely be a background thread here — so the engine runs on
the Tk main thread and its Qt event loop is pumped from the Tk mainloop
(`root.after` -> QApplication.processEvents, ~80 times a second). Tk stays
responsive throughout; only the actual wallpaper commit blocks briefly.

If PyQt6 is not installed, this module falls back to the pure-ctypes GDI
overlay in transition_fallback.py, and finally to simply setting the wallpaper
directly (play() returns False).
"""

from __future__ import annotations

import os
import time

try:
    from PyQt6.QtWidgets import QApplication
    from transition_engine import WallpaperEngine
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

TRANSITION_NAMES = {
    "circle": "Circle Reveal",
    "diamond": "Diamond Reveal",
    "split": "Split Open",
    "slide_top": "Slide from Top",
    "fade": "Cross Fade",
    "off": "Off (instant)",
}

HOLD_MIN_MS = 600       # minimum time the final frame stays up after commit
SETTLE_MS = 700         # after Windows rewrites its transcode cache, let its own fade finish
FALLBACK_HOLD_MS = 1500 # blind wait when the cache file can't be observed
PUMP_MS = 12            # Qt event-loop pump interval (~80 Hz)
DEADLINE_S = 10         # hard stop in case the engine never reports finished


def _transcode_mtime() -> float | None:
    """LastWriteTime of Windows' transcoded-wallpaper cache, if readable.

    Windows rewrites this file when it accepts a new wallpaper, so a change
    here is the best available signal that the desktop switch is underway.
    """
    try:
        path = os.path.join(os.environ.get("APPDATA", ""),
                            r"Microsoft\Windows\Themes\TranscodedWallpaper")
        return os.path.getmtime(path)
    except OSError:
        return None

if not HAVE_QT:  # pragma: no cover — only on machines without PyQt6
    from transition_fallback import WallpaperTransition  # noqa: F401
else:

    class WallpaperTransition:
        """Drives YASB's WallpaperEngine from the Tk mainloop.

        `play()` returns False (without starting anything) when the animation
        cannot be shown — no PyQt6, a per-monitor target, another transition
        already running — and the caller should set the wallpaper directly
        instead.
        """

        def __init__(self) -> None:
            self._engine = None
            self._qapp = None
            self._state: dict | None = None
            self._job = None
            self._deadline = 0.0
            self._commit_at = 0.0
            self._settle_at = None
            self._give_up_at = 0.0
            self._mtime_before = None
            self._on_commit = None
            self._on_done = None
            self._tk_root = None

        @property
        def running(self) -> bool:
            return self._engine is not None

        def abort(self) -> None:
            """Stop without further ado (used when the app is shutting down)."""
            if self.running:
                self._finish("aborted")

        def play(self, new_path: str, style: str = "fill",
                 animation: str = "circle", monitor_index: int | None = None,
                 on_commit=None, on_done=None, tk_root=None) -> bool:
            if (not HAVE_QT or not animation or animation == "off"
                    or monitor_index is not None):
                return False
            if self.running:
                return False
            if tk_root is None:
                import tkinter
                tk_root = tkinter._get_default_root()
            if tk_root is None:
                return False
            try:
                self._qapp = QApplication.instance() or QApplication([])
                self._engine = WallpaperEngine(new_path, animation, style=style)
            except Exception:
                self._engine = None
                return False
            self._tk_root = tk_root
            self._on_commit = on_commit
            self._on_done = on_done
            self._state = {"finished": False, "error": None}
            self._engine.finished.connect(self._on_engine_finished)
            self._engine.start()
            self._deadline = time.monotonic() + DEADLINE_S
            self._pump()
            return True

        # -- driven from the Tk mainloop ------------------------------------

        def _on_engine_finished(self) -> None:
            # Animation done and the final frame is frozen on screen: commit
            # the real wallpaper underneath so Windows' cross-fade happens
            # where nobody sees it (this is what YASB's manager does in
            # response to the same signal).
            self._mtime_before = _transcode_mtime()
            self._commit_at = time.monotonic()
            self._settle_at = None
            try:
                if self._on_commit is not None:
                    self._on_commit()
            except Exception as exc:
                self._state["error"] = str(exc)
            self._state["finished"] = True
            self._give_up_at = time.monotonic() + FALLBACK_HOLD_MS / 1000.0

        def _pump(self) -> None:
            if self._engine is None:
                return
            try:
                self._qapp.processEvents()
            except Exception as exc:
                self._finish(f"qt error: {exc}")
                return
            now = time.monotonic()
            if self._state["finished"]:
                # Wait until Windows has actually swapped the desktop before
                # pulling the overlay away — closing too early shows the old
                # wallpaper for a fraction of a second. Windows rewrites its
                # transcode cache when it accepts the new image; once we see
                # that, give its own fade SETTLE_MS to run out.
                if self._settle_at is None:
                    mtime = _transcode_mtime()
                    if mtime is not None and mtime != self._mtime_before:
                        self._settle_at = now + SETTLE_MS / 1000.0
                done = now >= self._give_up_at
                if self._settle_at is not None and now >= self._settle_at:
                    done = True
                if now < self._commit_at + HOLD_MIN_MS / 1000.0:
                    done = False
                if done:
                    self._finish(None)
                    return
            elif now > self._deadline:
                self._finish("timeout")
                return
            self._job = self._tk_root.after(PUMP_MS, self._pump)

        def _finish(self, error: str | None) -> None:
            engine, self._engine = self._engine, None
            if self._job is not None:
                try:
                    self._tk_root.after_cancel(self._job)
                except Exception:
                    pass
                self._job = None
            if engine is not None:
                try:
                    engine.close()  # frees resources via WM_DESTROY
                except Exception:
                    pass
            try:
                self._qapp.processEvents()
            except Exception:
                pass
            state_error = self._state["error"] if self._state else None
            done = self._on_done
            self._on_done = None
            if done is not None:
                try:
                    done(error is None and state_error is None,
                         error or state_error)
                except Exception:
                    pass

