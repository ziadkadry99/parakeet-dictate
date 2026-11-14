"""Parakeet Dictate: hold Fn anywhere on macOS, speak, release, and the text is typed
into whatever app has focus. Runs NVIDIA Parakeet TDT 0.6B v3 locally via MLX.

Shows a floating waveform pill while you talk.
Needs: Microphone, Input Monitoring (to see Fn), Accessibility (to paste).
Set System Settings > Keyboard > "Press 🌐 key to" = "Do Nothing", or macOS will also
open the emoji picker / switch input source every time you press Fn.
"""

import fcntl
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import AppKit
import mlx.core as mx
import numpy as np
import Quartz
import rumps
import sounddevice as sd
from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
from parakeet_mlx import from_pretrained
from parakeet_mlx.audio import get_logmel
from PyObjCTools import AppHelper

sys.path.insert(0, str(Path(__file__).resolve().parent))  # embedded launcher doesn't add it
from overlay import Overlay  # noqa: E402

MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v3"
SAMPLE_RATE = 16000
MIN_HOLD_SEC = 0.25          # shorter Fn taps are treated as accidental and ignored
PASTE_RESTORE_DELAY = 0.4    # wait for the target app to read the clipboard before restoring it
TRAILING_SPACE = True        # so consecutive dictations don't run together
FN_KEYCODE = 63              # kVK_Function

LOG_PATH = Path.home() / "Library/Logs/ParakeetDictate.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("dictate")

ICON_LOADING, ICON_IDLE, ICON_REC, ICON_BUSY = "⏳", "🎙️", "🔴", "✍️"


# ---------------------------------------------------------------- transcription
# MLX streams are thread-local, so load + every inference runs on this one thread.
MLX_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")
_model = None


def _load_model():
    global _model
    t0 = time.perf_counter()
    _model = from_pretrained(MODEL_ID)
    _transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))  # warm up kernels
    log.info("Model ready in %.1fs", time.perf_counter() - t0)


def _transcribe(audio: np.ndarray) -> str:
    # Mel features must be computed in float32 (same as parakeet's own load_audio path).
    mel = get_logmel(mx.array(audio, dtype=mx.float32), _model.preprocessor_config)
    return _model.generate(mel)[0].text.strip()


# ---------------------------------------------------------------- recording
class Recorder:
    def __init__(self, on_level=None):
        self.on_level = on_level
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def _callback(self, indata, frames, t, status):
        mono = indata[:, 0].copy()
        self._chunks.append(mono)
        if self.on_level:
            self.on_level(float(np.sqrt(np.mean(mono * mono))))

    def start(self):
        with self._lock:
            if self._stream:
                return
            self._chunks = []
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=512,
                callback=self._callback,
            )
            self._stream.start()

    def stop(self) -> np.ndarray:
        with self._lock:
            if not self._stream:
                return np.zeros(0, dtype=np.float32)
            self._stream.stop()
            self._stream.close()
            self._stream = None
            return np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)


# ---------------------------------------------------------------- text insertion
def paste_text(text: str):
    """Put text on the clipboard, send Cmd+V, then restore the previous clipboard."""
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

    time.sleep(PASTE_RESTORE_DELAY)
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


# ---------------------------------------------------------------- app
class DictateApp(rumps.App):
    def __init__(self):
        super().__init__("Parakeet Dictate", title=ICON_LOADING, quit_button="Quit")
        # Menu-bar-only app: no Dock icon, and never steals focus from the app you dictate into.
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory
        )
        self.overlay = Overlay()
        self.recorder = Recorder(on_level=self.overlay.set_level)
        self.ready = False
        self.fn_down = False
        self.cancelled = False
        self.press_time = 0.0
        self.last_text = ""

        self.status_item = rumps.MenuItem("Loading model…")
        self.last_item = rumps.MenuItem("Last: —", callback=self.copy_last)
        self.menu = [self.status_item, self.last_item, None]

        self._request_mic()
        self._install_event_tap()
        MLX_THREAD.submit(_load_model).add_done_callback(self._on_model_loaded)

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

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) | Quartz.CGEventMaskBit(
            Quartz.kCGEventKeyDown
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

    def _on_model_loaded(self, fut):
        if fut.exception():
            log.error("Model load failed: %s", fut.exception())
            AppHelper.callAfter(self._set_status, "⚠️", "Model failed to load (see log)")
            return
        self.ready = True
        AppHelper.callAfter(self._set_status, ICON_IDLE, "Ready: hold Fn to dictate")

    def _set_status(self, icon, text=None):
        self.title = icon
        if text:
            self.status_item.title = text

    # -- key handling (runs on main thread; keep it fast)
    def _on_event(self, proxy, etype, event, refcon):
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self.tap, True)
            return event

        if etype == Quartz.kCGEventKeyDown:
            # Fn+key is a shortcut (Fn+arrows, Fn+F-keys, ...), not dictation.
            if self.fn_down and not self.cancelled:
                self.cancelled = True
                self.recorder.stop()
                self.overlay.hide(success=False)
                self._set_status(ICON_IDLE)
            return event

        if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode) != FN_KEYCODE:
            return event
        fn = bool(Quartz.CGEventGetFlags(event) & Quartz.kCGEventFlagMaskSecondaryFn)
        if fn == self.fn_down:
            return event
        self.fn_down = fn
        if fn:
            self._on_press()
        else:
            self._on_release()
        return event

    def _on_press(self):
        if not self.ready:
            return
        self.cancelled = False
        self.press_time = time.perf_counter()
        try:
            self.recorder.start()
        except Exception as e:
            log.error("Mic start failed: %s", e)
            self._set_status("⚠️", "Microphone unavailable (check permission)")
            return
        self.overlay.show_recording()
        self._set_status(ICON_REC)

    def _on_release(self):
        if not self.ready or self.cancelled:
            return
        held = time.perf_counter() - self.press_time
        audio = self.recorder.stop()
        if held < MIN_HOLD_SEC or len(audio) < SAMPLE_RATE * 0.2:
            self.overlay.hide(success=False)
            self._set_status(ICON_IDLE)
            return
        self.overlay.show_transcribing()
        self._set_status(ICON_BUSY)
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def _process(self, audio: np.ndarray):
        t0 = time.perf_counter()
        try:
            text = MLX_THREAD.submit(_transcribe, audio).result()
        except Exception as e:
            log.exception("Transcription failed")
            AppHelper.callAfter(self.overlay.show_error)
            AppHelper.callAfter(self._set_status, "⚠️", f"Error: {e}")
            return
        elapsed = time.perf_counter() - t0
        log.info("%.1fs audio -> %.2fs: %r", len(audio) / SAMPLE_RATE, elapsed, text)
        AppHelper.callAfter(self.overlay.hide, bool(text))
        if text:
            self.last_text = text
            paste_text(text + (" " if TRAILING_SPACE else ""))
            short = text if len(text) <= 50 else text[:47] + "…"
            AppHelper.callAfter(setattr, self.last_item, "title", f"Last: {short}")
        AppHelper.callAfter(
            self._set_status, ICON_IDLE, f"Ready · last: {len(audio)/SAMPLE_RATE:.1f}s in {elapsed:.2f}s"
        )

    def copy_last(self, _):
        if self.last_text:
            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(self.last_text, AppKit.NSPasteboardTypeString)


def _single_instance_lock():
    """Exit if another copy is already running (e.g. login item + a terminal launch)."""
    f = open(Path.home() / "Library/Caches/ParakeetDictate.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("Another instance is already running; exiting")
        sys.exit(0)
    return f  # keep the file open for the life of the process


if __name__ == "__main__":
    _lock = _single_instance_lock()
    DictateApp().run()
