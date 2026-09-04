# Youcifer Wallpaper
<img width="1722" height="664" alt="image" src="https://github.com/user-attachments/assets/f0b53b25-e6eb-47e0-8917-7cb9b9b6e216" />
<img width="1918" height="944" alt="image" src="https://github.com/user-attachments/assets/ff9ed719-f012-413a-b7c4-ff4d1adf5106" />


A lightweight Windows wallpaper **switcher** built with **Python** that
**actually changes the Windows wallpaper** — the same one you see in
*Settings → Personalization → Background*. No overlay windows, no
"covering up" the real wallpaper. It lives entirely in the **system tray**:
no main window.

## Version 2.0 — What's New

- **Uniform Cover Flow cards** — Every card in the Cover Flow gallery now
  leans identically. The special "straight + rounded corner + white border"
  center card is **removed**. All wallpapers share the same shape.
- **Buttery-smooth scrolling** — The Cover Flow now uses a faster Qt event
  pump (6 ms), a refreshed wheel timer (4 ms), and a softer exponential
  ease-out curve. Wheel scrolling glides instead of jumping, and cards
  preload further ahead so nothing pops in while you scroll.

## How it's different from WinWallpaper / Wallpaper Engine

Those apps render a hidden window **behind your desktop icons** (the
`Progman`/`WorkerW` trick) that covers the real wallpaper — it's the only way
to show *animated/video* wallpapers, but it "overrides" rather than changes.

This app goes the native route instead:

- **Primary:** the `IDesktopWallpaper` COM interface (Windows 10/11) —
  the official API. Supports **per-monitor** wallpapers and fit styles.
- **Fallback:** `SystemParametersInfoW(SPI_SETDESKWALLPAPER)` + registry
  style keys (older systems, all monitors).

Because it changes the real wallpaper, everything is permanent: it survives
reboots, shows up in Windows Settings, and works even if this app is closed.

## Features

- 🖥️ **System tray icon** — left-click toggles the switcher; right-click
  opens the menu: `Open Switcher / Random Wallpaper / Choose Folder… /
  Fit Style / Monitor / Slideshow / Exit`. Status messages show as the
  tray tooltip.
- ⌨️ **Global hotkey `Alt+W`** — opens a quick switcher anywhere. Two layouts,
  chosen from the tray menu (`"layout"` in `settings.json`):
  - **Grid Cards** — floating rounded bar with a search field and a horizontal
    row of wallpaper cards (plus a "⇄ Random Wallpaper" card).
  - **Cover Flow** — a full-screen Qt gallery (YASB "Strip" style, run in the
    same process and pumped from the Tk mainloop): tall cards with **uniform
    leaning parallelogram edges** (no special center card, no rounded corners,
    no white outline), no text. Smooth `QVariantAnimation` + exponential
    ease-out scrolling at ~60 fps; wheel/arrow keys browse; a single click on
    any card applies it (same animated transition as Grid Cards — Enter also
    applies); Esc closes.
    Falls back to the grid if PyQt6 is missing.
  Both: `Esc` closes. The hotkey can be changed in `settings.json` (`"hotkey": "alt+w"` —
  supports `ctrl`, `shift`, `win` modifiers and `f1`–`f12` keys).
- ✒️ **Alexandria font** across the switcher UI (bundled in `assets/fonts`,
  registered privately per-process — nothing is installed system-wide)
- 🖼️ Crop-to-fill **thumbnails**, cached and loaded lazily in chunks —
  the bar opens instantly and scrolling stays smooth
- 🖥️ **Per-monitor** wallpaper selection (or all monitors at once)
- 📐 Fit styles: Fill, Fit, Stretch, Tile, Center, Span
- ⏱️ **Slideshow** — auto-rotate the folder with shuffle, 5–60 min intervals
- ✨ **Animated transitions** — YASB's wallpaper transition engine (ported from
  [amnweb/yasb](https://github.com/amnweb/yasb), PyQt6) with Circle Reveal,
  Diamond, Split Open, Slide from Top or Cross Fade. Windows itself only ever
  cross-fades, so the animation plays on an overlay window parented to
  `WorkerW` (above the wallpaper, below the desktop icons) while the real
  wallpaper is committed underneath — the built-in cross-fade happens hidden.
  Chosen from the tray menu, stored as `"transition"` in `settings.json`.
  Skipped automatically when applying to a single monitor, and it degrades
  gracefully (pure-ctypes fallback, then instant change) if Qt is unavailable.
- 🕘 **History** of applied wallpapers (kept in `settings.json`)
- 🪶 Zero heavy dependencies — just Python, Tkinter, Pillow and ctypes

## Run

```
run.bat            (recommended — starts silently via pythonw)
```

or `pythonw app.py` (use `python app.py` to see errors in a console).

## Tray menu

| Item | Action |
|---|---|
| Open Switcher (Alt+W) | show/hide the quick switcher bar |
| Random Wallpaper | apply a random image from the folder |
| Choose Folder… | native folder picker — the switcher shows its images |
| Fit Style | Fill / Fit / Stretch / Tile / Center / Span |
| Monitor | all monitors or a specific one (with resolutions) |
| Slideshow | start / **turn off** + interval (5–60 min) |
| Transition | Circle / Diamond / Split / Slide from Top / Cross Fade / Off |
| Switcher Layout | Grid Cards / Cover Flow |
| Start with Windows | launch the app automatically at login (HKCU Run key, ✓ shows state) |
| Exit | clean shutdown, releases the hotkey and tray icon |

## Requirements

- Windows 10/11
- Python 3.8+ (standard library only)
- Pillow (for thumbnails): `pip install pillow`
- PyQt6 (for the animated transitions): `pip install PyQt6` —
  optional; without it the app falls back to a plain-ctypes overlay and
  finally to an instant change, everything else still works

## Files

| File | Purpose |
|---|---|
| `app.py` | Headless controller: hotkey + tray + switcher wiring |
| `switcher.py` | The Alt+W quick switcher bar (grid layout) |
| `switcher_flow.py` | Cover-flow Qt gallery (YASB "Strip" style, uniform cards, smooth scrolling) |
| `tray.py` | System tray icon + popup menu (pure ctypes) |
| `hotkey.py` | Global hotkey registration (RegisterHotKey, pure ctypes) |
| `fonts.py` | Bundled Alexandria font loader (AddFontResourceExW, FR_PRIVATE) |
| `wallpaper_api.py` | Native Windows wallpaper API (ctypes COM wrapper + fallback) |
| `transition.py` | Transition controller: pumps the Qt engine from the Tk mainloop |
| `transition_engine.py` | YASB's WallpaperEngine ported verbatim (PyQt6, WorkerW overlay) |
| `transition_fallback.py` | Pure-ctypes GDI overlay fallback for machines without PyQt6 |
| `test_set_wallpaper.py` | API-level test (sets a test wallpaper, verifies, restores) |
| `test_tray.py` | Tray-only app end-to-end test (menu, event pump, settings) |
| `test_switcher.py` | Switcher + Alt+W hotkey end-to-end test |
| `test_transition.py` | Visual test: plays each transition without changing the wallpaper |
| `settings.json` | Persisted folder / style / slideshow / hotkey / history |
| `run.bat` | Launcher (pythonw — no console window) |

## Notes / limitations

- Static images only — Windows has **no API** for video wallpapers; that's
  why Wallpaper Engine uses the overlay technique this app deliberately avoids.
- The slideshow only runs while the app is open (it can stay hidden in the tray).
