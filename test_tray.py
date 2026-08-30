"""
test_tray.py — headless smoke test for the tray-only app.

Creates a temp folder with 3 images, boots WallpaperApp (no GUI window,
hidden root), exercises the tray menu provider and event pump, then
cleans up. Restores the original wallpaper at the end.
"""

import os
import shutil
import tempfile
import time

import tkinter as tk

from PIL import Image

from wallpaper_api import WallpaperManager
from app import WallpaperApp, APP_DIR, SETTINGS_PATH

mgr = WallpaperManager()
original = mgr.get_wallpaper()
print("Original wallpaper:", original)

root = tk.Tk()
root.withdraw()

# temp wallpaper folder
test_dir = os.path.join(tempfile.gettempdir(), "youcifer_tray_test")
shutil.rmtree(test_dir, ignore_errors=True)
os.makedirs(test_dir)
for i in (1, 2, 3):
    Image.new("RGB", (800, 500), (i * 60 % 255, 90, 160)).save(
        os.path.join(test_dir, f"wall_{i}.png"))

app = WallpaperApp(root)
app._load_folder(test_dir)
print("Images loaded:", len(app.images))
assert len(app.images) == 3

# -- tray menu provider ---------------------------------------------------------
menu = app._tray_menu()
labels = [it[0] for it in menu]
assert ("Open Switcher  (Alt+W)", "toggle", False) in menu
assert any(it[0] == "Choose Folder…" for it in menu)
fit = [it for it in menu if it[0] == "Fit Style"][0][1]
assert len(fit) == 6 and any(c for _, _, c in fit), "no checked fit style"
mons = [it for it in menu if it[0] == "Monitor"][0][1]
print("Tray menu OK — monitors:", [m[0] for m in mons])

# -- start with Windows toggle ------------------------------------------------------
import winreg
startup_before = app._startup_enabled()
if startup_before:
    app._toggle_startup()  # normalize to OFF for a deterministic run
assert not app._startup_enabled()
app._toggle_startup()  # ON
assert app._startup_enabled(), "startup registry value not created"
with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
    cmd, _ = winreg.QueryValueEx(k, "YouciferWallpaper")
assert "app.py" in cmd and "pythonw" in cmd.lower(), f"bad command: {cmd}"
print("Startup ON, command:", cmd)
menu = app._tray_menu()
assert ("Start with Windows", "startup", True) in menu, "not checked when ON"
# restore explicitly (blind toggle breaks when the original state was ON)
if startup_before:
    if not app._startup_enabled():
        app._toggle_startup()
else:
    if app._startup_enabled():
        app._toggle_startup()
assert app._startup_enabled() == startup_before, "startup state not restored"
print("Startup restored to:", "ON" if startup_before else "OFF")

# -- slideshow on/off ------------------------------------------------------------------
slide = [it for it in menu if it[0] == "Slideshow"][0][1]
assert slide[0][0] == "Start Slideshow"
app._events.put("slide")
deadline = time.time() + 3
while time.time() < deadline and not app._slideshow_running:
    root.update()
    time.sleep(0.05)
assert app._slideshow_running, "slideshow did not start"
slide = [it for it in app._tray_menu() if it[0] == "Slideshow"][0][1]
assert slide[0][0] == "Turn Off Slideshow", "label did not change"
app._events.put("slide")  # turn off
deadline = time.time() + 3
while time.time() < deadline and app._slideshow_running:
    root.update()
    time.sleep(0.05)
assert not app._slideshow_running, "slideshow did not stop"
print("Slideshow start/turn-off OK")

# -- event pump: simulate tray menu commands -------------------------------------
app._events.put("style:fit")
app._events.put("monitor:all")
app._events.put("random")
deadline = time.time() + 3
while time.time() < deadline:
    root.update()
    time.sleep(0.05)
cur = mgr.get_wallpaper()
print("Applied via tray 'random':", cur)
assert "wall_" in (cur or "").lower(), "random via tray failed"
assert app.style == "fit" and app.monitor is None

# -- switcher still works ----------------------------------------------------------
app.switcher.show()
root.update()
assert app.switcher.visible
print("Switcher cards:", len(app.switcher._card_widgets))
assert len(app.switcher._card_widgets) == 4  # random + 3
app.switcher.hide()
root.update()

# -- settings reflect the folder -------------------------------------------------
app._on_close()
time.sleep(0.4)

import json
with open(SETTINGS_PATH, encoding="utf-8") as f:
    saved = json.load(f)
assert saved["folder"].lower() == test_dir.lower()
print("Settings saved with folder:", saved["folder"])

# restore original wallpaper
if original and os.path.isfile(original):
    mgr.set_wallpaper(original)
    print("Restored:", mgr.get_wallpaper())

shutil.rmtree(test_dir, ignore_errors=True)
print("ALL TRAY TESTS PASSED")
