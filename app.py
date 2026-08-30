"""
Youcifer Wallpaper — a tray-resident Windows wallpaper switcher.

No main window: press Alt+W for the quick switcher bar, and use the
system tray icon (right-click menu) to choose the wallpaper folder,
fit style, monitor and slideshow. Changes the REAL Windows wallpaper
(visible in Settings > Personalization).

Run:  pythonw app.py   (or python app.py)
"""

from __future__ import annotations

import json
import os
import queue
import random
import sys
import tkinter as tk
from tkinter import filedialog
import winreg

from hotkey import GlobalHotkey
from switcher import SwitcherBar
from tray import TrayIcon
from wallpaper_api import POSITION_NAMES, WallpaperManager
import fonts

fonts.register()  # load bundled Alexandria TTFs into this process

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
MAX_THUMBS = 400

STYLES = list(POSITION_NAMES.keys())
INTERVALS = (5, 10, 15, 30, 60)


def default_folder() -> str:
    pics = os.path.join(os.path.expanduser("~"), "Pictures")
    return pics if os.path.isdir(pics) else os.path.expanduser("~")


class WallpaperApp:
    """Headless controller: global hotkey + tray icon + switcher bar."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.withdraw()  # no main window — tray only
        self.manager = WallpaperManager()

        self.settings = self._load_settings()
        self.folder = self.settings.get("folder", default_folder())
        self.history: list[str] = list(self.settings.get("history", []))
        self.style = self.settings.get("style", "fill")
        if self.style not in STYLES:
            self.style = "fill"
        self.monitor = self.settings.get("monitor")   # None = all monitors
        self.interval = int(self.settings.get("interval", 15))
        self.shuffle = bool(self.settings.get("shuffle", True))

        self.images: list[str] = []
        self._slideshow_running = False
        self._slide_job = None
        self._slide_index = 0

        self.monitors: list[dict] = []
        try:
            self.monitors = self.manager.monitors()
        except OSError:
            self.monitors = []

        # quick switcher bar (child of the hidden root)
        self.switcher = SwitcherBar(self)

        # global hotkey + tray icon, both marshal commands via one queue
        self._events: queue.Queue = queue.Queue()
        self._poll_job = None
        self.hotkey = GlobalHotkey(
            self.settings.get("hotkey", "alt+w"),
            lambda: self._events.put("toggle"))
        self.tray = TrayIcon(
            notify=self._events.put, menu_provider=self._tray_menu,
            tooltip="Youcifer Wallpaper — Alt+W to switch")
        self.hotkey.start()
        self.tray.start()
        self._poll_events()

        self._load_folder(self.folder)

    # -- tray menu ---------------------------------------------------------------

    def _tray_menu(self) -> list:
        fit = [(name, f"style:{key}", key == self.style)
               for key, name in POSITION_NAMES.items()]
        mons = [("All monitors", "monitor:all", self.monitor is None)]
        for m in self.monitors:
            w, h = m["size"]
            mons.append((f"Monitor {m['index']}  ({w}x{h})",
                         f"monitor:{m['index']}",
                         self.monitor == m["index"]))
        slide = [("Turn Off Slideshow" if self._slideshow_running
                  else "Start Slideshow", "slide", False), ("-",)]
        slide += [(f"Every {n} min", f"interval:{n}", self.interval == n)
                  for n in INTERVALS]
        return [
            ("Open Switcher  (Alt+W)", "toggle", False),
            ("Random Wallpaper", "random", False),
            ("Choose Folder…", "folder", False),
            ("-",),
            ("Fit Style", fit),
            ("Monitor", mons),
            ("Slideshow", slide),
            ("Start with Windows", "startup", self._startup_enabled()),
            ("-",),
            ("Exit", "exit", False),
        ]

    # -- event pump (hotkey + tray commands) --------------------------------------

    def _poll_events(self) -> None:
        try:
            if self.hotkey.failed and not getattr(
                    self, "_hotkey_warned", False):
                self._hotkey_warned = True
                self._status(f"Hotkey {self.hotkey.combo} could not be "
                             f"registered (already in use by another app)")
            while True:
                event = self._events.get_nowait()
                if event == "toggle":
                    self.switcher.toggle()
                elif event == "folder":
                    self._choose_folder()
                elif event == "random":
                    self._random()
                elif event == "slide":
                    self._toggle_slideshow()
                elif event == "startup":
                    self._toggle_startup()
                elif event == "exit":
                    self._on_close()
                    return
                elif event.startswith("style:"):
                    self.style = event[6:]
                    self._save_settings()
                    self._status(f"Fit style: {POSITION_NAMES[self.style]}")
                elif event.startswith("monitor:"):
                    value = event[8:]
                    self.monitor = None if value == "all" else int(value)
                    self._save_settings()
                    self._status("Monitor: "
                                 + ("all" if self.monitor is None else value))
                elif event.startswith("interval:"):
                    self.interval = int(event[9:])
                    self._save_settings()
                    self._status(f"Slideshow interval: {self.interval} min")
        except queue.Empty:
            pass
        except tk.TclError:
            return  # window already destroyed
        try:
            self._poll_job = self.root.after(120, self._poll_events)
        except tk.TclError:
            pass

    def _toggle_switcher(self) -> None:
        self.switcher.toggle()

    # -- status (shown as the tray tooltip) -----------------------------------------

    def _status(self, text: str) -> None:
        try:
            self.tray.set_tooltip(text[:127])
        except Exception:
            pass

    # -- folder & images ---------------------------------------------------------

    def _choose_folder(self) -> None:
        folder = filedialog.askdirectory(
            parent=self.root, initialdir=self.folder,
            title="Choose a wallpaper folder")
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder: str) -> None:
        folder = os.path.normpath(folder)
        if not os.path.isdir(folder):
            self._status(f"Folder not found: {folder}")
            return
        self.folder = folder
        files = []
        try:
            for name in sorted(os.listdir(folder)):
                ext = os.path.splitext(name)[1].lower()
                if ext in IMAGE_EXTS:
                    files.append(os.path.join(folder, name))
                if len(files) >= MAX_THUMBS:
                    break
        except OSError as exc:
            self._status(f"Cannot read folder: {exc}")
            return
        self.images = files
        self.switcher.on_images_changed()
        self._save_settings()
        self._status(f"{len(files)} wallpapers — press Alt+W to switch")

    # -- applying -----------------------------------------------------------------

    def _random(self) -> None:
        if self.images:
            self._apply_path(random.choice(self.images))

    def _apply_path(self, path: str) -> None:
        if not os.path.isfile(path):
            self._status(f"File not found: {path}")
            return

        def do_set() -> None:
            try:
                self.manager.set_wallpaper(path, monitor_index=self.monitor,
                                           style=self.style)
            except Exception as exc:
                self._status(f"Could not set wallpaper: {exc}")
                return
            self._add_history(path)
            self._save_settings()
            target = "all monitors" if self.monitor is None \
                else f"monitor {self.monitor}"
            self._status(f"Wallpaper set to {os.path.basename(path)} "
                         f"({self.style}, {target})")

        do_set()

    def _add_history(self, path: str) -> None:
        if path in self.history:
            self.history.remove(path)
        self.history.insert(0, path)
        del self.history[30:]

    # -- start with Windows (HKCU Run key) ----------------------------------------

    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    RUN_NAME = "YouciferWallpaper"

    def _startup_command(self) -> str:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pythonw if os.path.isfile(pythonw) else sys.executable
        return f'"{exe}" "{os.path.join(APP_DIR, "app.py")}"'

    def _startup_enabled(self) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY) as k:
                value, _ = winreg.QueryValueEx(k, self.RUN_NAME)
                return bool(value)  # an empty leftover value counts as OFF
        except OSError:
            return False

    def _toggle_startup(self) -> None:
        try:
            if self._startup_enabled():
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, self.RUN_NAME)
                self._status("Start with Windows: OFF")
            else:
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, self.RUN_NAME, 0, winreg.REG_SZ,
                                      self._startup_command())
                self._status("Start with Windows: ON")
        except OSError as exc:
            self._status(f"Could not change startup setting: {exc}")

    # -- slideshow ------------------------------------------------------------------

    def _toggle_slideshow(self) -> None:
        if self._slideshow_running:
            self._stop_slideshow()
        else:
            self._slideshow_running = True
            self._status(f"Slideshow started — every {self.interval} min")
            self._slideshow_tick()

    def _stop_slideshow(self) -> None:
        self._slideshow_running = False
        if self._slide_job is not None:
            self.root.after_cancel(self._slide_job)
            self._slide_job = None
        self._status("Slideshow stopped")

    def _slideshow_tick(self) -> None:
        if not self._slideshow_running:
            return
        if not self.images:
            self._stop_slideshow()
            self._status("Slideshow stopped — no images in folder")
            return
        if self.shuffle:
            path = random.choice(self.images)
        else:
            path = self.images[self._slide_index % len(self.images)]
            self._slide_index += 1
        self._apply_path(path)
        delay = max(1, self.interval) * 60_000
        self._slide_job = self.root.after(delay, self._slideshow_tick)

    # -- settings ----------------------------------------------------------------------

    def _load_settings(self) -> dict:
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        data = {
            "folder": self.folder,
            "style": self.style,
            "monitor": self.monitor,
            "interval": self.interval,
            "shuffle": self.shuffle,
            "hotkey": self.settings.get("hotkey", "alt+w"),
            "history": self.history,
        }
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    # -- shutdown -------------------------------------------------------------------------

    def _on_close(self) -> None:
        self._stop_slideshow()
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self.hotkey.stop()
        self.tray.stop()
        self.switcher.destroy()
        self._save_settings()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    WallpaperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()