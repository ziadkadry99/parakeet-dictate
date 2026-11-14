"""Windows hold-to-talk hotkey on a low-level keyboard hook (pynput's WH_KEYBOARD_LL thread).

The hotkey is stored as raw codes, not a key name, so any key the keyboard actually reports
can be bound, including vendor keys that arrive as vk 0xFF + a scan code:
    {"vk": int, "scan": int, "ext": bool, "name": str}

Fn: on most laptops Fn is handled inside the keyboard firmware and Windows never sees it.
Some keyboards do report it. So the default is "whatever your Fn key sends": on first launch
the app asks you to press Fn and records it; if nothing arrives it falls back to Right Ctrl.
"""

import ctypes
import logging

from pynput import keyboard

log = logging.getLogger("dictate")

WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_EXTENDED, LLKHF_LOWER_IL_INJECTED, LLKHF_INJECTED = 0x01, 0x02, 0x10
VK_ESCAPE = 0x1B
FAKE_ALTGR_CTRL_SCAN = 0x21D   # AltGr sends a synthetic Left Ctrl with this scan code

RIGHT_CTRL = {"vk": 0xA3, "scan": 0x1D, "ext": True, "name": "Right Ctrl"}

_NAMES = {
    0xA0: "Left Shift", 0xA1: "Right Shift", 0xA2: "Left Ctrl", 0xA3: "Right Ctrl",
    0xA4: "Left Alt", 0xA5: "Right Alt", 0x5B: "Left Win", 0x5C: "Right Win", 0x5D: "Menu",
    0x14: "Caps Lock", 0x91: "Scroll Lock", 0x90: "Num Lock", 0x13: "Pause", 0x2D: "Insert",
    0x20: "Space",
    **{0x70 + i: f"F{i + 1}" for i in range(24)},
}


def key_name(vk: int, scan: int, ext: bool) -> str:
    if vk in _NAMES:
        return _NAMES[vk]
    buf = ctypes.create_unicode_buffer(64)
    lparam = (scan & 0xFF) << 16 | (1 << 24 if ext else 0)
    if ctypes.windll.user32.GetKeyNameTextW(lparam, buf, 64) and buf.value:
        return buf.value
    return f"Key vk={vk:#04x} scan={scan:#04x}"


def is_typing_key(vk: int) -> bool:
    """Keys that produce a character (letters, digits, punctuation, Space, Enter, Tab...)."""
    MAPVK_VK_TO_CHAR = 2
    return vk not in (0, 0xFF) and ctypes.windll.user32.MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR) != 0


class WinHotkey:
    """Forwards hotkey press/release and "other key while held" to the controller, on the
    UI thread via call_main. The hook callback must return fast or Windows drops the hook."""

    def __init__(self, spec: dict, ctl, call_main):
        self.spec = spec
        self.ctl, self.call_main = ctl, call_main
        self.down = False
        self._capture = None  # callback(spec) while waiting for a key to bind
        self._accept = None
        self.listener = keyboard.Listener(win32_event_filter=self._filter)

    def start(self):
        self.listener.start()

    def capture(self, on_key, accept=None):
        """Bind the next key pressed. on_key(spec, or None if cancelled with Esc) runs on the
        UI thread. accept(vk) -> bool can skip keys (they don't end the capture)."""
        self.down = False
        self._accept = accept
        self._capture = on_key

    def cancel_capture(self):
        self._capture = None

    def set_spec(self, spec: dict):
        self.spec = spec
        self.down = False

    def _matches(self, vk, scan, ext) -> bool:
        s = self.spec
        if vk != s["vk"] or ext != s["ext"]:
            return False
        # vk 0 / 0xFF ("no mapping") is shared by many vendor keys; the scan code tells them apart.
        return scan == s["scan"] if vk in (0, 0xFF) else True

    def _filter(self, msg, data):
        try:
            if data.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED):
                return False  # our own Ctrl+V, or other software typing
            if data.scanCode == FAKE_ALTGR_CTRL_SCAN:
                return False
            vk, scan, ext = data.vkCode, data.scanCode, bool(data.flags & LLKHF_EXTENDED)
            is_down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)

            if self._capture is not None:
                if is_down and (vk == VK_ESCAPE or self._accept is None or self._accept(vk)):
                    cb, self._capture = self._capture, None
                    if vk == VK_ESCAPE:
                        self.call_main(cb, None)
                    else:
                        spec = {"vk": vk, "scan": scan, "ext": ext, "name": key_name(vk, scan, ext)}
                        log.info("Captured key %s", spec)
                        self.call_main(cb, spec)
                return False

            if self._matches(vk, scan, ext):
                if is_down and not self.down:       # repeats arrive as more key-downs
                    self.down = True
                    self.call_main(self.ctl.press)
                elif not is_down and self.down:
                    self.down = False
                    self.call_main(self.ctl.release)
            elif is_down and self.down:
                self.call_main(self.ctl.other_key)
        except Exception:
            log.exception("hotkey filter")
        return False  # we never need pynput's own key translation
