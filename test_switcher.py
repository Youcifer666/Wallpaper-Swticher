"""End-to-end test of the Alt+W quick switcher bar."""

import ctypes
import os
import shutil
import time
import tkinter as tk
from PIL import Image

from wallpaper_api import WallpaperManager

user32 = ctypes.windll.user32

mgr = WallpaperManager()
original = mgr.get_wallpaper()
print("Original wallpaper:", original)

HERE = os.path.dirname(os.path.abspath(__file__))
test_dir = os.path.join(HERE, "_test_imgs")
os.makedirs(test_dir, exist_ok=True)
for i, color in enumerate([(200, 60, 60), (60, 200, 60), (60, 60, 200)]):
    Image.new("RGB", (800, 500), color).save(
        os.path.join(test_dir, f"wall_{i}.png"))

from app import WallpaperApp  # noqa: E402

root = tk.Tk()
root.withdraw()
app = WallpaperApp(root)
app._load_folder(test_dir)
print("Images loaded:", len(app.images))

# -- open the switcher programmatically --------------------------------------
app.switcher.show()
root.update()
assert app.switcher.visible, "switcher did not become visible"
cards = len(app.switcher._card_widgets)
print("Switcher shown, cards:", cards)
assert cards == 4, "expected 1 random + 3 image cards"

# -- typing filters (filtering is debounced ~180ms, so pump events) -----------
app.switcher._entry_focus_in()
app.switcher.entry.insert(0, "wall_1")
app.switcher._on_type()
deadline = time.time() + 1
while time.time() < deadline and len(app.switcher._card_widgets) != 2:
    root.update()
    time.sleep(0.02)
print("Filtered cards:", len(app.switcher._card_widgets))
assert len(app.switcher._card_widgets) == 2, "filter failed (1 random + 1 match)"

# -- apply filtered card (animation plays first, real apply on final frame) ----
app.switcher.sel = 1
app.switcher._apply_selected()
deadline = time.time() + 6
while time.time() < deadline:
    root.update()
    current = mgr.get_wallpaper() or ""
    if "wall_1" in current.lower():
        break
    time.sleep(0.02)
current = mgr.get_wallpaper() or ""
print("Windows reports:", current)
assert "wall_1" in current.lower(), "switcher apply failed"
print("SWITCHER APPLY OK (animation included)")

# -- random card --------------------------------------------------------------
app.switcher.query = ""
app.switcher.entry.delete(0, "end")
app.switcher._on_type()
deadline = time.time() + 1
while time.time() < deadline and len(app.switcher._card_widgets) != 4:
    root.update()
    time.sleep(0.02)
app.switcher._apply_random()
deadline = time.time() + 6
while time.time() < deadline:
    root.update()
    cur = mgr.get_wallpaper() or ""
    if "wall_" in cur.lower():
        break
    time.sleep(0.02)
assert "wall_" in (mgr.get_wallpaper() or "").lower()
print("RANDOM APPLY OK")

# -- close the switcher --------------------------------------------------------
app.switcher.hide()
root.update()
assert not app.switcher.visible, "hide failed"
print("HIDE OK")
# clean shutdown: stops hotkey, cancels poll job, releases Alt+W
app._on_close()
time.sleep(0.4)

# -- global hotkey: simulate a real Alt+W keypress -----------------------------
root = tk.Tk()
root.withdraw()
app2 = WallpaperApp(root)
app2._load_folder(test_dir)
root.update()
assert app2.hotkey.registered.wait(3), "Alt+W was not re-registered"

user32.keybd_event(0x12, 0, 0, 0)      # Alt down
user32.keybd_event(0x57, 0, 0, 0)      # W down
user32.keybd_event(0x57, 0, 2, 0)      # W up
user32.keybd_event(0x12, 0, 2, 0)      # Alt up

deadline = time.time() + 3
while time.time() < deadline:
    root.update()
    if app2.switcher.visible:
        break
    time.sleep(0.03)
assert app2.switcher.visible, "Alt+W global hotkey did not open the switcher"
print("GLOBAL HOTKEY Alt+W OK — switcher opened")

app2.switcher.hide()
root.update()
app2._on_close()

# -- cleanup -------------------------------------------------------------------
if original and os.path.isfile(original):
    mgr.set_wallpaper(original, style="fill")
    print("Restored:", mgr.get_wallpaper())
shutil.rmtree(test_dir)
print("DONE - ALL SWITCHER TESTS PASSED")
