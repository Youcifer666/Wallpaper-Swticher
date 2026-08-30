"""One-shot test: set a test wallpaper, verify Windows accepted it, restore."""

import os

from PIL import Image

from wallpaper_api import WallpaperManager

mgr = WallpaperManager()
original = mgr.get_wallpaper()
print("Original wallpaper:", original)

# Make a test image (teal/green gradient)
img = Image.new("RGB", (1920, 1080))
px = img.load()
for y in range(1080):
    g = int(255 * y / 1080)
    for x in range(1920):
        px[x, y] = (40, g, int(255 * x / 1920))
img.save("test_wallpaper.png")

test_path = os.path.abspath("test_wallpaper.png")
mgr.set_wallpaper(test_path, monitor_index=None, style="fill")

current = mgr.get_wallpaper()
print("After set, Windows reports:", current)
assert "test_wallpaper" in current.lower(), "FAILED: wallpaper was not applied!"
print("SET OK - Windows accepted the new wallpaper")

# Restore original
if original and os.path.isfile(original):
    mgr.set_wallpaper(original, style="fill")
    restored = mgr.get_wallpaper()
    print("Restored original:", restored)
else:
    print("(Could not restore original - path missing)")

os.remove(test_path)
print("DONE")
