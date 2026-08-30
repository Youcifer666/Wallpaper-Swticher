# Youcifer Wallpaper

A lightweight Windows wallpaper **switcher** built with **Python** that
**actually changes the Windows wallpaper** — the same one you see in
*Settings → Personalization → Background*. No overlay windows, no
"covering up" the real wallpaper. It lives entirely in the **system tray**:
no main window.

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
- ⌨️ **Global hotkey `Alt+W`** — opens a Wallpaper-Engine-style **quick switcher
  bar** anywhere: floating, rounded, centered on screen, with a search field
  and a horizontal row of wallpaper cards (plus a "⇄ Random Wallpaper" card).
  Click a card (or arrow-keys + Enter) to apply instantly, `Esc` to close.
  The mouse wheel scrolls the card slider from anywhere over the bar.
  The hotkey can be changed in `settings.json` (`"hotkey": "alt+w"` —
  supports `ctrl`, `shift`, `win` modifiers and `f1`–`f12` keys).
- ✒️ **Alexandria font** across the switcher UI (bundled in `assets/fonts`,
  registered privately per-process — nothing is installed system-wide)
- 🖼️ Crop-to-fill **thumbnails**, cached and loaded lazily in chunks —
  the bar opens instantly and scrolling stays smooth
- 🖥️ **Per-monitor** wallpaper selection (or all monitors at once)
- 📐 Fit styles: Fill, Fit, Stretch, Tile, Center, Span
- ⏱️ **Slideshow** — auto-rotate the folder with shuffle, 5–60 min intervals
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
| Start with Windows | launch the app automatically at login (HKCU Run key, ✓ shows state) |
| Exit | clean shutdown, releases the hotkey and tray icon |

## Requirements

- Windows 10/11
- Python 3.8+ (standard library only)
- Pillow (for thumbnails): `pip install pillow`

## Files

| File | Purpose |
|---|---|
| `app.py` | Headless controller: hotkey + tray + switcher wiring |
| `switcher.py` | The Alt+W quick switcher bar |
| `tray.py` | System tray icon + popup menu (pure ctypes) |
| `hotkey.py` | Global hotkey registration (RegisterHotKey, pure ctypes) |
| `fonts.py` | Bundled Alexandria font loader (AddFontResourceExW, FR_PRIVATE) |
| `wallpaper_api.py` | Native Windows wallpaper API (ctypes COM wrapper + fallback) |
| `test_set_wallpaper.py` | API-level test (sets a test wallpaper, verifies, restores) |
| `test_tray.py` | Tray-only app end-to-end test (menu, event pump, settings) |
| `test_switcher.py` | Switcher + Alt+W hotkey end-to-end test |
| `settings.json` | Persisted folder / style / slideshow / hotkey / history |
| `run.bat` | Launcher (pythonw — no console window) |

## Notes / limitations

- Static images only — Windows has **no API** for video wallpapers; that's
  why Wallpaper Engine uses the overlay technique this app deliberately avoids.
- The slideshow only runs while the app is open (it can stay hidden in the tray).
