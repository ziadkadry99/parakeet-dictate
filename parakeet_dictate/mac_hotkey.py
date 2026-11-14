"""macOS hold-to-talk hotkey, fed from the app's Quartz event tap.

Stored as {"keycode": int, "name": str}. Default is Fn (keycode 63). Modifier keys arrive as
kCGEventFlagsChanged and are tracked per side through the device-dependent flag bits; any
other key arrives as keyDown/keyUp (auto-repeat ignored). Caps Lock can't be held, so it is
rejected.
"""

import logging

import AppKit
import Quartz

log = logging.getLogger("dictate")

FN = {"keycode": 63, "name": "Fn"}
KC_ESCAPE, KC_CAPS_LOCK = 53, 57

# keycode -> (name, flag bit that is set while that physical key is down)
MODIFIERS = {
    63: ("Fn", Quartz.kCGEventFlagMaskSecondaryFn),
    59: ("Left Control", 0x0001), 62: ("Right Control", 0x2000),
    56: ("Left Shift", 0x0002), 60: ("Right Shift", 0x0004),
    55: ("Left Command", 0x0008), 54: ("Right Command", 0x0010),
    58: ("Left Option", 0x0020), 61: ("Right Option", 0x0040),
}
_NAMES = {
    122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6", 98: "F7", 100: "F8",
    101: "F9", 109: "F10", 103: "F11", 111: "F12", 105: "F13", 107: "F14", 113: "F15",
    106: "F16", 64: "F17", 79: "F18", 80: "F19", 90: "F20", 49: "Space", 36: "Return",
    48: "Tab", 51: "Delete", 117: "Forward Delete", 114: "Help", 115: "Home", 119: "End",
    116: "Page Up", 121: "Page Down", 123: "Left", 124: "Right", 125: "Down", 126: "Up",
}


def _key_name(keycode, event) -> str:
    if keycode in MODIFIERS:
        return MODIFIERS[keycode][0]
    if keycode in _NAMES:
        return _NAMES[keycode]
    try:
        chars = AppKit.NSEvent.eventWithCGEvent_(event).charactersIgnoringModifiers()
        if chars and chars.strip():
            return chars.upper()
    except Exception:
        pass
    return f"Key {keycode}"


class MacHotkey:
    def __init__(self, spec: dict, ctl):
        self.keycode = spec["keycode"]
        self.ctl = ctl
        self.down = False
        self._capture = None

    def set_spec(self, spec: dict):
        self.keycode = spec["keycode"]
        self.down = False

    def capture(self, on_key):
        """Bind the next key pressed. on_key(spec | None on Esc | "caps") on the main thread."""
        self.down = False
        self._capture = on_key

    def cancel_capture(self):
        self._capture = None

    def handle(self, etype, event):
        """Called from the event tap for FlagsChanged / KeyDown / KeyUp (main thread)."""
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        flags = Quartz.CGEventGetFlags(event)

        if self._capture is not None:
            if etype == Quartz.kCGEventKeyDown or (
                etype == Quartz.kCGEventFlagsChanged
                and keycode in MODIFIERS and flags & MODIFIERS[keycode][1]
            ):
                cb, self._capture = self._capture, None
                if keycode == KC_ESCAPE:
                    cb(None)
                else:
                    cb({"keycode": keycode, "name": _key_name(keycode, event)})
            elif etype == Quartz.kCGEventFlagsChanged and keycode == KC_CAPS_LOCK:
                cb, self._capture = self._capture, None
                cb("caps")
            return

        if keycode == self.keycode:
            if keycode in MODIFIERS:
                if etype != Quartz.kCGEventFlagsChanged:
                    return
                down = bool(flags & MODIFIERS[keycode][1])
            elif etype == Quartz.kCGEventKeyDown:
                if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                    return
                down = True
            elif etype == Quartz.kCGEventKeyUp:
                down = False
            else:
                return
            if down != self.down:
                self.down = down
                self.ctl.press() if down else self.ctl.release()
        elif etype == Quartz.kCGEventKeyDown and self.down:
            # Hotkey+other key is a shortcut (Fn+arrows, Fn+F-keys, ...), not dictation.
            self.ctl.other_key()
