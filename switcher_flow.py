"""switcher_flow.py — cover-flow gallery (YASB "Strip" style, ported).

A faithful port of YASB's wallpaper gallery: tall cards with leaning
parallelogram edges away from the centre, the selected card straight in the
middle with a white outline, no text anywhere.

The gallery is a QWidget created on the Tk main thread and pumped from the
Tk mainloop via `root.after` -> `QApplication.processEvents()`, exactly like
the wallpaper transition engine. Image decoding uses QImageReader +
QThreadPool (Qt releases the GIL), so thumbnails never stall the UI thread.
"""

from __future__ import annotations

import math
import os
import sys
import time
from collections import OrderedDict
from typing import NamedTuple

from PyQt6.QtCore import (
    QEasingCurve, QObject, QPointF, QRunnable, QRect, QRectF, QSize, Qt,
    QThreadPool, QTimer, QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PyQt6.QtWidgets import QApplication, QWidget

HAVE_QT = True
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}

CARD_ANIMATION_DURATION = 320
CARD_ANIMATION_EASING = QEasingCurve.Type.OutCubic
PUMP_MS = 12
CLICK_ALPHA = 1


class _ImgSignals(QObject):
    loaded = pyqtSignal(int, QImage)


class _ImageLoader(QRunnable):
    """Decodes one wallpaper down to a card off the GUI thread (pure Qt)."""

    def __init__(self, path, w, h, index, gallery):
        super().__init__()
        self._path = path
        self._w = w
        self._h = h
        self._index = index
        self._gallery = gallery
        self._signals = _ImgSignals()

    def run(self):
        reader = QImageReader(self._path)
        # Respect EXIF orientation (very common on phone photos) so
        # portrait shots aren't decoded sideways into the cover-flow cards.
        reader.setAutoTransform(True)
        original = reader.size()
        tw, th = self._w, self._h
        if not original.isValid() or original.height() == 0:
            reader.setScaledSize(QSize(tw, th))
        else:
            oa = original.width() / original.height()
            ta = tw / th if th else 1.0
            if oa > ta:
                sh, sw = th, int(th * oa)
            else:
                sw, sh = tw, int(tw / oa) if oa else th
            # Decoding straight to the on-screen size (instead of full
            # resolution then downscaling) is what keeps this fast even for
            # big source photos — QImageReader does the downscale during
            # decode itself, so we never allocate a full-res QImage just to
            # throw most of it away.
            reader.setScaledSize(QSize(max(1, sw), max(1, sh)))
        img = reader.read()
        # Emit even a null QImage on failure (corrupt/unreadable file) so the
        # main thread still clears this index out of `_pending` — otherwise
        # a bad file would permanently block itself from ever being retried
        # or evicted.
        # QImage is thread-safe; QPixmap is NOT (GUI thread only).
        # Send QImage through the signal; the main thread converts to QPixmap.
        self._signals.loaded.emit(self._index, img)



class Placement(NamedTuple):
    x: float
    shear: float = 0.0
    scale: float = 1.0
    opacity: float = 1.0
    focus: float = 0.0


class StripGallery(QWidget):
    """YASB's Strip layout — cards tile edge-to-edge with leaning edges."""

    step = 1.0
    lean = 0.21
    border = 2
    dim = 0.40
    corner_radius = 0
    wraps = True
    accent = "#ffffff"
    neighbours = 4

    def __init__(self, folder: str, current: str = ""):
        super().__init__()
        self.image_files = self._scan(folder)
        self.is_closing = False
        # Set by apply_wallpaper()/keyPressEvent(Esc) and drained by
        # FlowBar._pump() on a clean Tk tick — never acted on from inside a
        # Qt event handler. See apply_wallpaper() for why.
        self.pending_apply: str | None = None
        self.pending_close = False

        screen = QApplication.primaryScreen().geometry()
        # Matches the reference cover-flow's card proportions: shorter and
        # noticeably narrower (~9:16, phone-wallpaper-shaped) than the old
        # 3:4 cards, so more of the strip is visible edge-to-edge at once.
        card_h = int(screen.height() * 0.46)
        card_w = int(card_h * 9 / 16)
        self.card_size = (card_w, card_h)

        self._cache: OrderedDict[int, QPixmap] = OrderedDict()
        self._pending: set[int] = set()
        self._pool = QThreadPool()
        # Decoding is CPU-bound (JPEG/PNG decode + scale), so scale with the
        # machine instead of hardcoding 2 — capped so we don't thrash disk
        # I/O or starve the GUI thread on very large batches of neighbours.
        self._pool.setMaxThreadCount(max(2, min(6, (os.cpu_count() or 4))))

        self.view = _CardsView(self)
        self.view.setParent(self)

        self._preselect = 0
        if current:
            norm = os.path.normpath(current).lower()
            for i, p in enumerate(self.image_files):
                if os.path.normpath(p).lower() == norm:
                    self._preselect = i
                    break

        self.fit_to_view(screen.width(), card_h + 40)
        self.view.setGeometry(0, 0, screen.width(), card_h + 40)
        self.view.go_to(self._preselect, animate=False)
        self.on_focus_changed(self._preselect)

    def _scan(self, folder: str) -> list[str]:
        files: list[str] = []
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                    files.append(os.path.join(folder, name))
        return files

    def layout_cards(self):
        cards = []
        for index, distance in self.view.visible_cards():
            spot = self.place(distance)
            cards.append((index, spot._replace(
                focus=max(0.0, 1.0 - abs(distance)))))
        return cards

    def place(self, distance: float) -> Placement:
        card_w, _ = self.card_size
        # All cards now lean the same way — no special upright center card.
        return Placement(x=distance * self.step * card_w, shear=self.lean)  

    def fit_to_view(self, width: int, height: int):
        card_w, _ = self.card_size
        step = max(1.0, self.step * card_w)
        needed = int(math.ceil(width / (2.0 * step))) + 1
        count = len(self.image_files)
        self.neighbours = max(1, min(needed, max(1, (count - 1) // 2)))
        self.wraps = count > 2 * self.neighbours

    def offset_for(self, index: int) -> float:
        return self.view.offset + self.view.relative_distance(index)

    def normalise_offset(self, offset: float) -> float:
        count = len(self.image_files)
        return offset % count if count and self.wraps else offset

    def on_focus_changed(self, index: int):
        if self.image_files:
            self._load_around(index)

    def selected_image(self):
        if not self.image_files:
            return None
        return self.image_files[self.view.index]

    def pixmap_for(self, index: int):
        pixmap = self._cache.get(index)
        if pixmap is not None:
            self._cache.move_to_end(index)
        return pixmap

    def _load_around(self, index: int):
        count = len(self.image_files)
        if not count:
            return
        radius = self.neighbours + 3
        wanted = {(index + o) % count for o in range(-radius, radius + 1)}
        card_w, card_h = self.card_size
        for target in sorted(wanted, key=lambda t: abs(self.view.relative_distance(t))):
            if target in self._cache or target in self._pending:
                continue
            self._pending.add(target)
            loader = _ImageLoader(self.image_files[target],
                                  card_w, card_h, target, self)
            loader._signals.loaded.connect(self._on_image_loaded)
            self._pool.start(loader)
        limit = 2 * radius + 8
        for cached in [i for i in self._cache if i not in wanted]:
            if len(self._cache) <= limit:
                break
            del self._cache[cached]

    def _on_image_loaded(self, index: int, image: QImage):
        self._pending.discard(index)
        if self.is_closing or index >= len(self.image_files):
            return
        try:
            if image.isNull():
                return  # decode failed — leave the placeholder in place
            pixmap = QPixmap.fromImage(image)
            self._cache[index] = pixmap
            if abs(self.view.relative_distance(index)) <= self.neighbours + 0.5:
                self.view.update()
        except RuntimeError:
            # The C++ side of this widget was already torn down (deleteLater
            # finally ran) between the signal being queued and being
            # delivered. Nothing to do — just swallow it instead of letting
            # it bubble up out of a Qt signal dispatch.
            pass

    def apply_wallpaper(self, image_path: str):
        # IMPORTANT: this can run *while Qt is still dispatching* the mouse
        # click or Enter key that triggered it (we're inside
        # QApplication.processEvents(), called from FlowBar._pump). Calling
        # straight into app._apply_path() from here used to spin up a brand
        # new Qt overlay window and pump a *nested* processEvents() call
        # before this event had finished dispatching — with the image
        # loader threads still able to deliver queued signals into a
        # gallery that was about to be torn down. That reentrancy is what
        # crashed the app on apply. So: only ever record *intent* here, and
        # let FlowBar act on it from a clean, non-reentrant Tk callback.
        if self.is_closing:
            return
        self.is_closing = True
        self.pending_apply = image_path

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if not self.is_closing:
                self.is_closing = True
                self.pending_close = True
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            path = self.selected_image()
            if path:
                self.apply_wallpaper(path)
        elif key == Qt.Key.Key_Left:
            self.view.move_by(-1)
        elif key == Qt.Key.Key_Right:
            self.view.move_by(1)
        elif key == Qt.Key.Key_Home:
            self.view.go_to(0)
        elif key == Qt.Key.Key_End:
            self.view.go_to(len(self.image_files) - 1)
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.view.setGeometry(0, 0, self.width(), self.height())
        self.fit_to_view(self.width(), self.height())


class _CardsView(QWidget):
    """Paints the cards, handles wheel + click, animates the offset."""

    def __init__(self, gallery: StripGallery):
        super().__init__(gallery)
        self.gallery = gallery
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.offset = 0.0
        self.index = 0
        self.hovered: int | None = None
        self.hit_areas: list[tuple[int, QPolygonF]] = []
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(CARD_ANIMATION_DURATION)
        self.animation.setEasingCurve(CARD_ANIMATION_EASING)
        self.animation.valueChanged.connect(self._on_animation_value)
        self.animation.finished.connect(self._on_animation_finished)
        # Grid-style smooth wheel glide: accumulate a fractional target and ease
        # towards it with an exponential curve (~60 fps, same feel as the grid
        # bar) instead of jumping one discrete card per wheel notch.
        self._wheel_target: float | None = None
        self._last_tick = 0.0
        self._wheel_timer = QTimer(self)
        self._wheel_timer.setInterval(15)
        self._wheel_timer.timeout.connect(self._wheel_tick)

    @property
    def count(self) -> int:
        return len(self.gallery.image_files)

    def relative_distance(self, index: int) -> float:
        count = self.count
        distance = index - self.offset
        if self.gallery.wraps and count > 1:
            distance = ((distance + count / 2.0) % count) - count / 2.0
        return distance

    def visible_cards(self) -> list[tuple[int, float]]:
        count = self.count
        if count == 0:
            return []
        neighbours = self.gallery.neighbours
        limit = neighbours + 0.5
        centre = int(math.floor(self.offset + 0.5))
        nearest: dict[int, float] = {}
        for step in range(centre - neighbours - 1, centre + neighbours + 2):
            index = step % count
            distance = self.relative_distance(index)
            if abs(distance) > limit:
                continue
            if index not in nearest or abs(distance) < abs(nearest[index]):
                nearest[index] = distance
        cards = list(nearest.items())
        cards.sort(key=lambda card: -abs(card[1]))
        return cards

    def project(self, spot: Placement, inset: float = 0.0) -> QPolygonF:
        card_w, card_h = self.gallery.card_size
        half_w = max(1.0, card_w * spot.scale / 2.0 - inset)
        half_h = max(1.0, card_h * spot.scale / 2.0 - inset)
        centre_x = self.width() / 2.0 + spot.x
        centre_y = self.height() / 2.0
        lean = spot.shear * half_h
        return QPolygonF([
            QPointF(centre_x - half_w + lean, centre_y - half_h),
            QPointF(centre_x + half_w + lean, centre_y - half_h),
            QPointF(centre_x + half_w - lean, centre_y + half_h),
            QPointF(centre_x - half_w - lean, centre_y + half_h),
        ])

    @staticmethod
    def draw_cover(painter, pixmap, rect: QRectF):
        size = pixmap.deviceIndependentSize()
        scale = max(rect.width() / size.width(), rect.height() / size.height())
        width, height = size.width() * scale, size.height() * scale
        centre = rect.center()
        target = QRectF(centre.x() - width / 2.0, centre.y() - height / 2.0, width, height)
        painter.drawPixmap(target, pixmap, QRectF(0, 0, pixmap.width(), pixmap.height()))

    def paintEvent(self, event):
        try:
            self._paint_and_hit(event)
        except Exception:
            import traceback
            traceback.print_exc()

    def _paint_and_hit(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        gallery = self.gallery
        border = gallery.border
        self.hit_areas = []
        if CLICK_ALPHA:
            painter.fillRect(self.rect(), QColor(0, 0, 0, CLICK_ALPHA))
        for index, spot in gallery.layout_cards():
            quad = self.project(spot)
            opacity = spot.opacity
            if opacity <= 0.01:
                continue
            bounds = quad.boundingRect()
            if bounds.right() < 0 or bounds.left() > self.width():
                continue
            if bounds.bottom() < 0 or bounds.top() > self.height():
                continue
            picture = self.project(spot, border) if border else quad
            area = picture.boundingRect()
            focus = spot.focus
            painter.setOpacity(opacity)
            painter.save()
            window = QPainterPath()
            radius = gallery.corner_radius
            if spot.shear or radius <= 0:
                window.addPolygon(picture)
                window.closeSubpath()
            else:
                window.addRoundedRect(area, radius, radius)
            painter.setClipPath(window)
            pixmap = gallery.pixmap_for(index)
            if pixmap is not None:
                self.draw_cover(painter, pixmap, area)
            else:
                painter.fillRect(area, QColor(255, 255, 255, 18))
            shade = int(255 * gallery.dim * (1.0 - focus))
            if shade:
                painter.fillRect(area, QColor(0, 0, 0, shade))
            painter.restore()
            strength = 1.0 if index == self.hovered else focus ** 3
            if border and strength > 0.01:
                edge = self.project(spot, border / 2.0)
                outline = QPainterPath()
                if spot.shear or radius <= 0:
                    outline.addPolygon(edge)
                    outline.closeSubpath()
                else:
                    corner = radius + border / 2.0
                    outline.addRoundedRect(edge.boundingRect(), corner, corner)
                accent = QColor(gallery.accent)
                accent.setAlpha(int(255 * strength))
                pen = QPen(accent)
                pen.setWidthF(border)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(outline)
            self.hit_areas.append((index, quad))
        painter.end()

    def card_at(self, position: QPointF):
        for index, quad in reversed(self.hit_areas):
            if quad.containsPoint(position, Qt.FillRule.OddEvenFill):
                return index
        return None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.gallery.fit_to_view(self.width(), self.height())

    def _on_animation_value(self, value):
        self.offset = float(value)
        self.update()

    def _on_animation_finished(self):
        self.offset = self.gallery.normalise_offset(self.offset)
        self.update()

    def go_to(self, index: int, animate: bool = True):
        count = self.count
        if not count:
            return
        self.index = index % count
        target = self.gallery.offset_for(self.index)
        self.gallery.on_focus_changed(self.index)
        self.animation.stop()
        self._wheel_timer.stop()
        self._wheel_target = None
        if not animate:
            self.offset = target
            self.update()
            return
        self.animation.setStartValue(self.offset)
        self.animation.setEndValue(target)
        self.animation.start()

    def move_by(self, delta: int):
        count = self.count
        if not count:
            return
        if self.gallery.wraps:
            self.go_to(self.index + delta)
        else:
            self.go_to(max(0, min(count - 1, self.index + delta)))

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        count = self.count
        if not count:
            event.ignore()
            return
        notches = delta / 120.0
        if self._wheel_target is None:
            self._wheel_target = self.offset
        step = 0.55  # cards a full 120 px notch glides (partial-card, grid-like)
        target = self._wheel_target - notches * step
        bound = float(count)
        if self.gallery.wraps:
            # allow scrolling a little past either end so wrap-around stays usable
            target = max(-bound, min(2.0 * bound, target))
        else:
            target = max(0.0, min(bound - 1.0, target))
        self._wheel_target = target
        self.animation.stop()
        self._last_tick = 0.0
        if not self._wheel_timer.isActive():
            self._wheel_timer.start()
        event.accept()

    def _wheel_tick(self):
        now = time.monotonic()
        dt = 0.016 if self._last_tick == 0 else min(0.05, now - self._last_tick)
        self._last_tick = now
        if self._wheel_target is None:
            self._wheel_timer.stop()
            return
        diff = self._wheel_target - self.offset
        if abs(diff) < 0.003:
            self.offset = self._wheel_target
            self._wheel_target = None
            self._wheel_timer.stop()
            self._settle_offset()
            return
        # frame-rate independent exponential ease-out (time constant 90 ms)
        self.offset += diff * (1.0 - math.exp(-dt / 0.09))
        self.update()

    def _settle_offset(self):
        count = self.count
        if not count:
            return
        idx = int(round(self.offset))
        if self.gallery.wraps:
            idx %= count
        else:
            idx = max(0, min(count - 1, idx))
        self.offset = float(idx)
        if idx != self.index:
            self.index = idx
            self.gallery.on_focus_changed(idx)
        self.update()

    def mouseMoveEvent(self, event):
        hovered = self.card_at(event.position())
        if hovered != self.hovered:
            self.hovered = hovered
            self.update()

    def leaveEvent(self, event):
        if self.hovered is not None:
            self.hovered = None
            self.update()

    def mousePressEvent(self, event):
        event.accept()
        # Grid-style apply: clicking a card that isn't centred centres it; a
        # single click on the already-centred (focused) card applies it through
        # the same animated-transition path as Grid Cards. Enter also applies.
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.card_at(event.position())
            if index is None:
                return
            if index != self.index:
                self.go_to(index, animate=True)
            else:
                self.gallery.apply_wallpaper(self.gallery.image_files[index])


class FlowBar:
    """Tk-side controller: creates the Qt gallery and pumps its event loop."""

    def __init__(self, app) -> None:
        self.app = app
        self.visible = False
        self._gallery = None
        self._qapp = None
        self._job = None

    def show(self) -> None:
        if self.visible:
            return
        if not HAVE_QT:
            return
        self._qapp = QApplication.instance() or QApplication([])
        current = self.app.manager.get_wallpaper() or ""
        try:
            self._gallery = StripGallery(self.app.folder, current)
        except Exception:
            self._gallery = None
            return

        from PyQt6.QtCore import Qt as _Qt
        self._gallery.setWindowFlags(
            _Qt.WindowType.FramelessWindowHint
            | _Qt.WindowType.WindowStaysOnTopHint
            | _Qt.WindowType.Tool
        )
        self._gallery.setAttribute(_Qt.WidgetAttribute.WA_TranslucentBackground)

        screen = QApplication.primaryScreen().geometry()
        h = self._gallery.card_size[1] + 40
        self._gallery.setGeometry(
            0, (screen.height() - h) // 2, screen.width(), h)
        self._gallery.resize(screen.width(), h)
        self._gallery.show()
        self._gallery.setFocus()
        self._gallery.activateWindow()

        self.visible = True
        self._pump()

    def hide(self) -> None:
        gallery, self._gallery = self._gallery, None
        self._stop_pump()
        self.visible = False
        if gallery is None:
            return
        gallery.is_closing = True
        # Stop the decode pool *before* tearing the widget down: drop any
        # thumbnail jobs that haven't started yet, and give the (at most
        # two) already-running jobs a brief, bounded window to finish so
        # QThreadPool's blocking destructor never has real work left to
        # wait on. This guarantees no queued cross-thread `loaded` signal
        # can still be in flight when we delete the widget it targets.
        try:
            gallery._pool.clear()
            gallery._pool.waitForDone(1500)
        except Exception:
            pass
        try:
            gallery.close()
        except Exception:
            pass
        try:
            gallery.deleteLater()
        except Exception:
            pass

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    def destroy(self) -> None:
        self.hide()

    def on_images_changed(self) -> None:
        pass

    def _pump(self) -> None:
        if not self.visible or self._gallery is None:
            return
        try:
            self._qapp.processEvents()
        except Exception:
            self.hide()
            return
        # Only act on a click-to-apply / Esc-to-close request *here* — a
        # plain Tk callback, invoked after processEvents() has fully
        # returned control to us. Never act on it from inside the Qt
        # mousePressEvent/keyPressEvent handler that set it: those run
        # nested inside the processEvents() call above, and jumping
        # straight into app._apply_path() from there used to spin up a
        # second Qt window and pump a *nested* processEvents() call before
        # the original event had finished dispatching — with the gallery's
        # own background image-loader threads still able to deliver
        # queued signals into a widget that was about to be torn down.
        # That reentrancy is what crashed the app on apply.
        gallery = self._gallery
        if gallery is not None:
            if gallery.pending_apply is not None:
                path = gallery.pending_apply
                gallery.pending_apply = None
                self.hide()
                self.app._apply_path(path)
                return
            if gallery.pending_close:
                gallery.pending_close = False
                self.hide()
                return
        self._job = self.app.root.after(PUMP_MS, self._pump)

    def _stop_pump(self) -> None:
        if self._job is not None:
            try:
                self.app.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
