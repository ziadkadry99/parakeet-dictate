"""Platform-independent dictation state machine.

A frontend (mac_app / qt_app) owns the UI, the hotkey and text insertion, and feeds key
events in here. press() / release() / other_key() must be called on the UI thread.

The frontend provides:
  ui.set_status(state, text=None)   state: "loading" | "idle" | "rec" | "busy" | "warn"
                                    (frontends also use "capture" while rebinding)
  ui.set_last(text)                 last transcript, for the "copy last" menu item
  overlay                           show_recording / show_transcribing / hide(success) /
                                    show_error / set_level(rms)  (see overlay_model.py)
  paste(text)                       insert text into the focused app; called on a worker
                                    thread, may block
  call_main(fn, *args)              run fn on the UI thread soon; callable from any thread
"""

import logging
import threading
import time

import numpy as np

from . import config
from .asr import Transcriber
from .recorder import Recorder

log = logging.getLogger("dictate")


class Controller:
    def __init__(self, ui, overlay, paste, call_main, hotkey_label: str):
        self.ui = ui
        self.overlay = overlay
        self.paste = paste
        self.call_main = call_main
        self.hotkey_label = hotkey_label
        self.recorder = Recorder(on_level=overlay.set_level)
        self.asr = Transcriber()
        self.ready = False
        self.load_failed = False
        self.holding = False
        self.cancelled = False
        self.press_time = 0.0
        self.last_text = ""

    def start(self):
        self.ui.set_status("loading", "Loading model…")
        self.asr.load().add_done_callback(self._on_model_loaded)

    def _on_model_loaded(self, fut):
        if fut.exception():
            log.error("Model load failed: %r", fut.exception())
            self.load_failed = True
            self.call_main(self.ui.set_status, "warn", "Model failed to load (see log)")
            return
        self.ready = True
        self.call_main(self.ui.set_status, "idle", f"Ready: hold {self.hotkey_label} to dictate")

    def set_hotkey_label(self, label: str):
        """Hotkey was rebound (or a rebind was cancelled): refresh the status line."""
        self.hotkey_label = label
        if self.ready:
            self.ui.set_status("idle", f"Ready: hold {label} to dictate")
        elif self.load_failed:
            self.ui.set_status("warn", "Model failed to load (see log)")
        else:
            self.ui.set_status("loading", "Loading model…")

    # -- key events (UI thread; keep fast)
    def press(self):
        self.holding = True
        if not self.ready:
            return
        self.cancelled = False
        self.press_time = time.perf_counter()
        try:
            self.recorder.start()
        except Exception as e:
            log.error("Mic start failed: %s", e)
            self.cancelled = True
            self.ui.set_status("warn", "Microphone unavailable (check permission)")
            return
        self.overlay.show_recording()
        self.ui.set_status("rec")

    def other_key(self):
        """Another key went down while the hotkey is held: it's a shortcut, not dictation."""
        if self.holding and self.ready and not self.cancelled:
            self.cancelled = True
            self.recorder.stop()
            self.overlay.hide(success=False)
            self.ui.set_status("idle")

    def release(self):
        self.holding = False
        if not self.ready or self.cancelled:
            return
        held = time.perf_counter() - self.press_time
        audio = self.recorder.stop()
        if held < config.MIN_HOLD_SEC or len(audio) < config.SAMPLE_RATE * config.MIN_AUDIO_SEC:
            self.overlay.hide(success=False)
            self.ui.set_status("idle")
            return
        self.overlay.show_transcribing()
        self.ui.set_status("busy")
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    # -- worker thread
    def _process(self, audio: np.ndarray):
        t0 = time.perf_counter()
        try:
            text = self.asr.transcribe(audio)
        except Exception as e:
            log.exception("Transcription failed")
            self.call_main(self.overlay.show_error)
            self.call_main(self.ui.set_status, "warn", f"Error: {e}")
            return
        elapsed = time.perf_counter() - t0
        secs = len(audio) / config.SAMPLE_RATE
        rms = float(np.sqrt(np.mean(audio * audio)))
        peak = float(np.max(np.abs(audio)))
        log.info("%.1fs audio (rms %.4f, peak %.3f, mic %s) -> %.2fs: %r", secs, rms, peak,
                 self.recorder.device or "default", elapsed, text)
        if not text:
            _save_empty_clip(audio)
        self.call_main(self.overlay.hide, bool(text))
        if text:
            self.last_text = text
            try:
                self.paste(text + (" " if config.TRAILING_SPACE else ""))
            except Exception:
                log.exception("Paste failed")
            short = text if len(text) <= 50 else text[:47] + "…"
            self.call_main(self.ui.set_last, short)
        if text:
            self.call_main(self.ui.set_status, "idle", f"Ready · last: {secs:.1f}s in {elapsed:.2f}s")
        else:
            self.call_main(self.ui.set_status, "idle", f"Ready · last {secs:.1f}s: no speech recognised")


EMPTY_CLIPS_KEEP = 5


def _save_empty_clip(audio: np.ndarray):
    """Keep the last few recordings that produced no text, so they can be listened to.
    Stored next to the log; nothing leaves the machine."""
    import wave

    try:
        folder = config.LOG_PATH.parent / "empty_clips"
        folder.mkdir(exist_ok=True)
        path = folder / time.strftime("%Y%m%d-%H%M%S.wav")
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        for old in sorted(folder.glob("*.wav"))[:-EMPTY_CLIPS_KEEP]:
            old.unlink()
        log.info("No text; saved the audio to %s", path)
    except Exception:
        log.exception("Could not save the empty clip")
