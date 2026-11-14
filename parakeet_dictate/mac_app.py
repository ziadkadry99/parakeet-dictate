"""macOS frontend: menu-bar app, hold-to-talk hotkey via a Quartz event tap, Cmd+V paste.

The hotkey defaults to Fn and can be rebound from the menu ("Set Hotkey…").

Needs: Microphone, Input Monitoring (to see Fn), Accessibility (to paste).
Set System Settings > Keyboard > "Press 🌐 key to" = "Do Nothing", or macOS will also
open the emoji picker / switch input source every time you press Fn.
"""

import logging
import time

import AppKit
import Quartz
import rumps
from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
from PyObjCTools import AppHelper

from . import config
from .controller import Controller
from .mac_hotkey import FN, MacHotkey
from .mac_overlay import Overlay

log = logging.getLogger("dictate")

ICONS = {"loading": "⏳", "idle": "🎙️", "rec": "🔴", "busy": "✍️", "warn": "⚠️", "capture": "⌨️"}
CAPTURE_TIMEOUT = 15.0


def paste_text(text: str):
    """Put text on the clipboard, send Cmd+V, then restore the previous clipboard.

    Called on a worker thread (blocks for PASTE_RESTORE_DELAY).
    """
    pb = AppKit.NSPasteboard.generalPasteboard()
    saved = [
        {t: item.dataForType_(t) for t in item.types()} for item in (pb.pasteboardItems() or [])
    ]

    pb.clearContents()
    pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
    # Tell clipboard managers (Raycast, Maccy, ...) not to record this entry.
    pb.setString_forType_("", "org.nspasteboard.TransientType")
    change_count = pb.changeCount()

    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(src, 9, down)  # 9 = 'v'
        Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    time.sleep(config.PASTE_RESTORE_DELAY)
    if pb.changeCount() != change_count:
        return  # user copied something meanwhile; don't clobber it
    pb.clearContents()
    items = []
    for d in saved:
        item = AppKit.NSPasteboardItem.alloc().init()
        for t, data in d.items():
            if data is not None:
                item.setData_forType_(data, t)
        items.append(item)
    if items:
        pb.writeObjects_(items)


class DictateApp(rumps.App):
    def __init__(self):
        super().__init__("Parakeet Dictate", title=ICONS["loading"], quit_button="Quit")
        # Menu-bar-only app: no Dock icon, and never steals focus from the app you dictate into.
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory
        )
        self.hotkey_spec = config.load_settings().get("hotkey") or FN
        self.status_item = rumps.MenuItem("Loading model…")
        self.last_item = rumps.MenuItem("Last: —", callback=self.copy_last)
        self.hotkey_item = rumps.MenuItem(self._hotkey_menu_title(), callback=self.rebind)
        self.reset_item = rumps.MenuItem("Reset Hotkey to Fn", callback=self.reset_hotkey)
        self.menu = [self.status_item, self.last_item, None, self.hotkey_item, self.reset_item, None]

        self.overlay = Overlay()
        self.ctl = Controller(
            ui=self, overlay=self.overlay, paste=paste_text,
            call_main=AppHelper.callAfter, hotkey_label=self.hotkey_spec["name"],
        )
        self.hotkey = MacHotkey(self.hotkey_spec, self.ctl)
        self.capture_token = 0
        self._request_mic()
        self._install_event_tap()
        self.ctl.start()

    # -- ui protocol for Controller
    def set_status(self, state, text=None):
        self.title = ICONS[state]
        if text:
            self.status_item.title = text

    def set_last(self, text):
        self.last_item.title = f"Last: {text}"

    def copy_last(self, _):
        if self.ctl.last_text:
            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(self.ctl.last_text, AppKit.NSPasteboardTypeString)

    # -- hotkey rebinding
    def _hotkey_menu_title(self):
        return f"Set Hotkey… (now: {self.hotkey_spec['name']})"

    def rebind(self, _):
        if self.tap is None:
            return
        self.capture_token += 1
        token = self.capture_token
        self.set_status("capture", "Press the key to hold for dictation (Esc cancels)…")
        self.hotkey.capture(self._on_captured)
        AppHelper.callLater(CAPTURE_TIMEOUT, lambda: self._capture_timeout(token))

    def _capture_timeout(self, token):
        if token == self.capture_token and self.hotkey._capture is not None:
            self.hotkey.cancel_capture()
            self.ctl.set_hotkey_label(self.hotkey_spec["name"])

    def _on_captured(self, spec):
        self.capture_token += 1
        if spec == "caps":
            self.ctl.set_hotkey_label(self.hotkey_spec["name"])
            self.status_item.title = "Caps Lock can't be held; hotkey unchanged"
            return
        if spec is not None:
            self._apply_hotkey(spec)
        self.ctl.set_hotkey_label(self.hotkey_spec["name"])

    def reset_hotkey(self, _):
        self.hotkey.cancel_capture()
        self._apply_hotkey(FN)
        self.ctl.set_hotkey_label(FN["name"])

    def _apply_hotkey(self, spec):
        self.hotkey_spec = spec
        self.hotkey.set_spec(spec)
        config.save_setting("hotkey", spec)
        self.hotkey_item.title = self._hotkey_menu_title()
        log.info("Hotkey set to %s", spec)

    # -- setup
    def _request_mic(self):
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeAudio, lambda granted: log.info("Microphone access: %s", granted)
        )

    def _install_event_tap(self):
        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
        if not Quartz.CGPreflightPostEventAccess():
            Quartz.CGRequestPostEventAccess()
        log.info(
            "Input Monitoring: %s, Accessibility (paste): %s",
            Quartz.CGPreflightListenEventAccess(), Quartz.CGPreflightPostEventAccess(),
        )

        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        )
        self.tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._on_event,
            None,
        )
        if self.tap is None:
            log.error("Could not create event tap: Input Monitoring permission missing")
            self.status_item.title = "⚠️ Grant Input Monitoring, then relaunch"
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self.tap, True)
        log.info("Event tap installed")

    # -- key handling (runs on main thread; keep it fast)
    def _on_event(self, proxy, etype, event, refcon):
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self.tap, True)
            return event

        try:
            self.hotkey.handle(etype, event)
        except Exception:
            log.exception("hotkey handler")
        return event


def main():
    DictateApp().run()
