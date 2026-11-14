"""Microphone capture while the hotkey is held."""

import threading

import numpy as np
import sounddevice as sd

from .config import SAMPLE_RATE


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
            # PortAudio resamples to 16 kHz for us (CoreAudio on macOS, MME/WASAPI shared on Windows).
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
