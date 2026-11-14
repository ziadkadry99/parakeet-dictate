"""Qt renderer for the dictation pill (Windows; also runs on macOS/Linux for development).

Animation and state live in overlay_model.py; this is a frameless, click-through,
never-activating, always-on-top window that paints the model each frame. Windows has no
behind-window blur we can use cheaply, so the glass is a little more opaque than on macOS.

All methods must be called on the Qt main thread, except set_level() (any thread).
"""

import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import config
from .overlay_model import PILL_H, PILL_Y, SCREEN_MARGIN, WIN_H, WIN_W, OverlayModel


def _rgba(r, g, b, a):
    return QColor.fromRgbF(r, g, b, max(0.0, min(1.0, a)))


class _PillWindow(QWidget):
    def __init__(self, model: OverlayModel):
        flags = (
            Qt.WindowType.Tool                      # no taskbar button
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(int(WIN_W), int(WIN_H))
        self.model = model
        if config.IS_WIN:
            self._win32_no_activate()

    # ------------------------------------------------------------ internals
    def _win32_no_activate(self):
        """Belt and braces on top of Qt's flags: WS_EX_NOACTIVATE so clicking/showing never
        takes focus from the app being dictated into, WS_EX_TRANSPARENT for click-through."""
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x20, 0x80, 0x08000000
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        style = user32.GetWindowLongPtrW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
        user32.SetWindowLongPtrW(
            hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        )

    def place_on_active_screen(self):
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        ag = screen.availableGeometry()  # excludes the taskbar
        x = ag.x() + (ag.width() - WIN_W) / 2.0
        y = ag.y() + ag.height() - SCREEN_MARGIN - (WIN_H - PILL_Y)
        self.move(round(x), round(y))

    def paintEvent(self, _event):
        m = self.model
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(m.window_alpha)
        p.setPen(Qt.PenStyle.NoPen)

        w, h = m.pill_width, PILL_H
        # Qt is y-down: the pill sits PILL_Y above the window's bottom edge, lifted by `lift`.
        x0 = (WIN_W - w) / 2.0
        y0 = WIN_H - PILL_Y - m.lift.value - h
        r = h / 2.0

        # Soft drop shadow (stacked translucent pills).
        for i in range(8, 0, -1):
            p.setBrush(QColor(0, 0, 0, int(9 * (9 - i) / 8)))
            p.drawRoundedRect(QRectF(x0 - i, y0 - i + 4, w + 2 * i, h + 2 * i), r + i, r + i)

        p.translate(x0, y0)
        body = QPainterPath()
        body.addRoundedRect(QRectF(0, 0, w, h), r, r)
        # Smoked glass.
        p.fillPath(body, _rgba(0.05, 0.05, 0.06, 0.88))
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, _rgba(1, 1, 1, 0.07))
        grad.setColorAt(1.0, _rgba(1, 1, 1, 0.0))
        p.fillPath(body, grad)
        # Hairline rim, brighter along the top edge.
        rim = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_rgba(1, 1, 1, 0.10), 1.0))
        p.drawRoundedRect(rim, r - 0.5, r - 0.5)
        p.save()
        p.setClipRect(QRectF(0, 0, w, h * 0.5))
        p.drawRoundedRect(rim, r - 0.5, r - 0.5)
        p.restore()

        p.setPen(Qt.PenStyle.NoPen)
        for b in m.bars(w, h):
            # Cheap glow: two widening translucent halos behind the bar.
            for grow, k in ((4.0, 0.16), (2.0, 0.30)):
                p.setBrush(_rgba(*b.rgb, b.glow_alpha * k))
                p.drawRoundedRect(
                    QRectF(b.x - grow, b.y - grow, b.w + 2 * grow, b.h + 2 * grow),
                    b.w / 2 + grow, b.w / 2 + grow,
                )
            p.setBrush(_rgba(*b.rgb, b.alpha))
            p.drawRoundedRect(QRectF(b.x, b.y, b.w, b.h), b.w / 2, b.w / 2)
        p.end()


class Overlay:
    def __init__(self):
        self.model = OverlayModel(
            call_later=lambda secs, fn: QTimer.singleShot(int(secs * 1000), fn)
        )
        self.window = _PillWindow(self.model)
        self.timer = QTimer()
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.last = time.perf_counter()

    def set_level(self, rms: float):
        self.model.set_level(rms)

    def show_recording(self):
        if self.model.show_recording():
            self.window.place_on_active_screen()
            self.window.show()
            self.window.raise_()
        if not self.timer.isActive():
            self.last = time.perf_counter()
            self.timer.start()

    def show_transcribing(self):
        self.model.show_transcribing()

    def hide(self, success=True):
        self.model.hide(success)

    def show_error(self):
        self.model.show_error()

    def _tick(self):
        now = time.perf_counter()
        dt, self.last = now - self.last, now
        visible = self.model.tick(dt)
        self.window.update()
        if not visible:
            self.timer.stop()
            self.window.hide()
