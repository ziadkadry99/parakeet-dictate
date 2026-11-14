"""Windows frontend: system-tray app, hold-to-talk hotkey on a low-level keyboard hook,
Ctrl+V paste. UI is Qt (tray icon, overlay, clipboard).

Hotkey: defaults to your keyboard's Fn key. Windows only sees Fn if the keyboard reports it,
so on first launch (no hotkey saved yet) the app asks you to press Fn and records whatever
code arrives; if nothing does within CAPTURE_TIMEOUT it falls back to Right Ctrl. Rebind any
time from the tray menu. Pressing another key while the hotkey is held cancels, so
hotkey+C etc. keep working as shortcuts.
"""

import logging
import signal
import struct
import sys

from pynput import keyboard
from PySide6.QtCore import QMimeData, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import config, win_autostart
from .controller import Controller
from .qt_overlay import Overlay
from .recorder import list_input_devices, refresh_devices
from .win_hotkey import RIGHT_CTRL, WinHotkey, is_typing_key

log = logging.getLogger("dictate")

STATE_COLORS = {
    "loading": "#8a8a90", "idle": "#8f80ff", "rec": "#ff4d5e", "busy": "#4cdcff", "warn": "#ffb020",
    "capture": "#3ddc84",
}
CAPTURE_TIMEOUT = 20.0
OWN_FORMAT = "application/x-parakeet-dictate"


def _win_format(name: str) -> str:
    """MIME name Qt maps to a registered Windows clipboard format."""
    return f'application/x-qt-windows-mime;value="{name}"'


class _MainThread(QObject):
    """call(fn, *args) from any thread -> runs fn(*args) on the Qt main thread."""

    _sig = Signal(object)

    def __init__(self):
        super().__init__()
        self._sig.connect(lambda f: f(), Qt.ConnectionType.QueuedConnection)

    def call(self, fn, *args):
        self._sig.emit(lambda: fn(*args))


class Paster:
    """Clipboard + Ctrl+V, then restore the previous clipboard (all on the Qt main thread)."""

    def __init__(self, call_main):
        self.call_main = call_main
        self.kb = keyboard.Controller()

    def paste(self, text: str):  # worker thread
        self.call_main(self._paste, text)

    def _paste(self, text: str):
        cb = QGuiApplication.clipboard()
        saved = _snapshot(cb.mimeData())

        md = QMimeData()
        md.setText(text)
        md.setData(OWN_FORMAT, b"1")
        if config.IS_WIN:
            # Keep dictation out of Win+V history, cloud clipboard and clipboard managers.
            md.setData(_win_format("ExcludeClipboardContentFromMonitorProcessing"), struct.pack("<I", 1))
            md.setData(_win_format("CanIncludeInClipboardHistory"), struct.pack("<I", 0))
            md.setData(_win_format("CanUploadToCloudClipboard"), struct.pack("<I", 0))
        cb.setMimeData(md)

        paste_mod = keyboard.Key.cmd if sys.platform == "darwin" else keyboard.Key.ctrl
        with self.kb.pressed(paste_mod):
            self.kb.tap("v")
        QTimer.singleShot(int(config.PASTE_RESTORE_DELAY * 1000), lambda: self._restore(saved))

    def _restore(self, saved):
        cb = QGuiApplication.clipboard()
        cur = cb.mimeData()
        if cur is None or not cur.hasFormat(OWN_FORMAT):
            return  # user copied something meanwhile; don't clobber it
        if saved is not None:
            cb.setMimeData(saved)
        else:
            cb.clear()


def _snapshot(md):
    """Deep-copy clipboard contents (QClipboard's own QMimeData dies on the next change)."""
    if md is None:
        return None
    out = QMimeData()
    for f in md.formats():
        try:
            out.setData(f, md.data(f))
        except Exception:  # some native formats can't be read back; skip them
            log.debug("clipboard: skipped format %s", f)
    if md.hasImage():
        out.setImageData(md.imageData())
    return out if out.formats() else None


def _dot_icon(color: str) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(6, 6, 52, 52)
    p.setBrush(QColor(255, 255, 255, 230))
    p.drawRoundedRect(25, 14, 14, 26, 7, 7)       # mic capsule
    p.drawRect(30, 42, 4, 8)                      # stem
    p.end()
    return QIcon(pm)


class TrayApp:
    def __init__(self, app: QApplication):
        self.app = app
        self.main = _MainThread()
        self.icons = {k: _dot_icon(c) for k, c in STATE_COLORS.items()}
        self.status_text = "Loading model…"

        self.tray = QSystemTrayIcon(self.icons["loading"])
        saved = config.load_settings().get("hotkey")
        self.hotkey_spec = saved or RIGHT_CTRL
        self.capture_token = 0

        menu = QMenu()
        self.status_action = QAction(self.status_text, menu, enabled=False)
        self.last_action = QAction("Last: —", menu, triggered=self.copy_last)
        self.hotkey_action = QAction(self._hotkey_menu_title(), menu, triggered=self.rebind)
        reset_action = QAction("Reset Hotkey to Right Ctrl", menu, triggered=self.reset_hotkey)
        self.autostart_action = QAction(
            "Start with Windows", menu, checkable=True, checked=self._init_autostart(),
            toggled=self._set_autostart,
        )
        log_action = QAction("Open log folder", menu, triggered=self.open_log_folder)
        quit_action = QAction("Quit", menu, triggered=app.quit)
        menu.addAction(self.status_action)
        menu.addAction(self.last_action)
        menu.addSeparator()
        menu.addAction(self.hotkey_action)
        menu.addAction(reset_action)
        self.mic_menu = menu.addMenu("Microphone")
        self.mic_menu.aboutToShow.connect(self._build_mic_menu)
        menu.addSeparator()
        menu.addAction(self.autostart_action)
        menu.addAction(log_action)
        menu.addAction(quit_action)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Parakeet Dictate")
        self.tray.show()

        self.overlay = Overlay()
        self.paster = Paster(self.main.call)
        self.ctl = Controller(
            ui=self, overlay=self.overlay, paste=self.paster.paste,
            call_main=self.main.call, hotkey_label=self.hotkey_spec["name"],
        )
        self.ctl.recorder.device = config.load_settings().get("microphone")
        log.info("Microphone: %s", self.ctl.recorder.device or "system default")
        self.hotkey = WinHotkey(self.hotkey_spec, self.ctl, self.main.call)
        self.hotkey.start()
        self.ctl.start()
        if saved is None:
            self._first_run_fn_capture()

    # -- ui protocol for Controller
    def set_status(self, state, text=None):
        self.tray.setIcon(self.icons[state])
        if text:
            self.status_text = text
            self.status_action.setText(text)
        self.tray.setToolTip(f"Parakeet Dictate: {self.status_text}")

    def set_last(self, text):
        self.last_action.setText(f"Last: {text}")

    def copy_last(self):
        if self.ctl.last_text:
            QGuiApplication.clipboard().setText(self.ctl.last_text)

    # -- hotkey rebinding
    def _hotkey_menu_title(self):
        return f"Set Hotkey… (now: {self.hotkey_spec['name']})"

    def _first_run_fn_capture(self):
        self._start_capture(
            "Welcome! Press your Fn key once to use it as the dictation key "
            "(or another non-typing key like Right Ctrl or F13; Esc keeps Right Ctrl).",
            first_run=True,
        )

    def rebind(self):
        self._start_capture("Press the key you want to hold for dictation (Esc cancels).")

    def _start_capture(self, message, first_run=False):
        self.capture_token += 1
        token = self.capture_token
        self.set_status("capture", "Waiting for a key…")
        self.tray.showMessage("Parakeet Dictate", message, QSystemTrayIcon.MessageIcon.Information, 10000)
        self.hotkey.capture(
            lambda spec: self._on_captured(token, spec, first_run),
            # First run starts by itself, maybe while you're typing: don't bind a letter.
            accept=(lambda vk: not is_typing_key(vk)) if first_run else None,
        )
        QTimer.singleShot(int(CAPTURE_TIMEOUT * 1000), lambda: self._capture_timeout(token, first_run))

    def _capture_timeout(self, token, first_run):
        if token != self.capture_token:
            return
        self.capture_token += 1
        self.hotkey.cancel_capture()
        if first_run:
            self._apply_hotkey(RIGHT_CTRL)
            self.tray.showMessage(
                "Parakeet Dictate",
                "No Fn key press was detected (many keyboards keep Fn to themselves). "
                "Using Right Ctrl; change it any time with Set Hotkey… in the tray menu.",
                QSystemTrayIcon.MessageIcon.Information, 10000,
            )
        self.ctl.set_hotkey_label(self.hotkey_spec["name"])

    def _on_captured(self, token, spec, first_run):
        if token != self.capture_token:
            return
        self.capture_token += 1
        if spec is not None:
            if first_run and spec["vk"] in (0, 0xFF):
                spec["name"] = "Fn"  # vendor key with no virtual-key mapping: that's the Fn key
            self._apply_hotkey(spec)
            self.tray.showMessage(
                "Parakeet Dictate", f"Hold {spec['name']} to dictate.",
                QSystemTrayIcon.MessageIcon.Information, 4000,
            )
        elif first_run:
            self._apply_hotkey(RIGHT_CTRL)  # Esc: accept the default and stop asking
        self.ctl.set_hotkey_label(self.hotkey_spec["name"])

    def reset_hotkey(self):
        self.capture_token += 1
        self.hotkey.cancel_capture()
        self._apply_hotkey(RIGHT_CTRL)
        self.ctl.set_hotkey_label(self.hotkey_spec["name"])

    def _apply_hotkey(self, spec):
        self.hotkey_spec = spec
        self.hotkey.set_spec(spec)
        config.save_setting("hotkey", spec)
        self.hotkey_action.setText(self._hotkey_menu_title())
        log.info("Hotkey set to %s", spec)

    # -- start with Windows (on by default)
    def _init_autostart(self) -> bool:
        want = config.load_settings().get("start_at_login", True)
        try:
            win_autostart.set_enabled(want)  # also refreshes the command if the project moved
            if "start_at_login" not in config.load_settings():
                config.save_setting("start_at_login", want)
        except OSError:
            log.exception("Could not update the Start with Windows entry")
            return win_autostart.is_enabled()
        return want

    def _set_autostart(self, on: bool):
        try:
            win_autostart.set_enabled(on)
            config.save_setting("start_at_login", on)
        except OSError:
            log.exception("Could not change Start with Windows")
            self.tray.showMessage("Parakeet Dictate", "Couldn't change Start with Windows (see log).",
                                  QSystemTrayIcon.MessageIcon.Warning, 5000)

    # -- microphone choice
    def _build_mic_menu(self):
        m = self.mic_menu
        m.clear()
        if not self.ctl.holding:  # can't restart PortAudio mid-recording
            refresh_devices()
        current = self.ctl.recorder.device
        group = QActionGroup(m)
        names = list_input_devices()
        entries = [("System default", None)] + [(n, n) for n in names]
        if current is not None and current not in names:
            entries.append((f"{current} (not connected)", current))
        for label, value in entries:
            a = QAction(label, m, checkable=True, checked=value == current)
            a.triggered.connect(lambda _=False, v=value: self._set_mic(v))
            group.addAction(a)
            m.addAction(a)

    def _set_mic(self, name):
        self.ctl.recorder.device = name
        config.save_setting("microphone", name)
        log.info("Microphone set to %s", name or "system default")

    def open_log_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.LOG_PATH.parent)))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Parakeet Dictate")
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.error("No system tray available")
    _tray = TrayApp(app)  # noqa: F841 - keep alive
    # Let Ctrl+C in a console stop the app: Python only sees SIGINT between Qt events.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(250)
    sys.exit(app.exec())
