"""
test_transition.py — manual visual test for the wallpaper transitions.

Plays each animation over the CURRENT desktop wallpaper WITHOUT changing it
(the commit callback is a no-op), so it is safe to run: the real wallpaper is
never touched.

Run:  python test_transition.py          # play every animation once
      python test_transition.py circle   # play just one
"""

from __future__ import annotations

import sys
import time
import tkinter as tk

from transition import HAVE_QT, TRANSITION_NAMES, WallpaperTransition


def main() -> None:
    names = [a for a in sys.argv[1:] if a in TRANSITION_NAMES] \
        or [a for a in TRANSITION_NAMES if a != "off"]
    # use the last applied wallpaper as the "new" one, so the reveal shows
    # something plausible; the layout code still runs exactly as in production
    try:
        with open("settings.json", encoding="utf-8") as f:
            import json
            history = json.load(f).get("history", [])
        new_path = history[0] if history else None
    except (OSError, ValueError):
        new_path = None
    if not new_path:
        print("No wallpaper in settings.json history to animate with.")
        return
    print("PyQt6 engine:", HAVE_QT)

    root = tk.Tk()
    root.withdraw()
    player = WallpaperTransition()
    for animation in names:
        print(f"Playing {animation!r} ...")
        result = []

        def on_done(ok, err, _name=animation):
            result.append((ok, err))
            if not ok:
                print(f"  {_name}: skipped/failed ({err})")

        started = player.play(new_path, style="fill", animation=animation,
                              on_commit=lambda: None, on_done=on_done,
                              tk_root=root)
        if not started:
            print("  could not start (no Qt / busy / no WorkerW)")
            continue
        deadline = time.time() + 15
        while player.running and time.time() < deadline:
            root.update()
            time.sleep(0.004)
        if player.running:
            player.abort()
            print("  timed out")
        elif result and result[0][0]:
            print("  ok")
        time.sleep(0.4)
    root.destroy()
    print("Done — the real wallpaper was never changed.")


if __name__ == "__main__":
    main()
