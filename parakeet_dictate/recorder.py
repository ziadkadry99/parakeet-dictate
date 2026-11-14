"""Microphone capture while the hotkey is held."""

import logging
import sys
import threading

import numpy as np
import sounddevice as sd

from .config import SAMPLE_RATE

log = logging.getLogger("dictate")

# Windows lists every mic once per host API (MME names are cut at 31 chars). WASAPI has the
# same full names as Windows Settings and resamples to 16 kHz for us with auto_convert.
_PREFERRED_API = "Windows WASAPI" if sys.platform == "win32" else None


def _host_api_index():
    if _PREFERRED_API is None:
        return None
    for i, a in enumerate(sd.query_hostapis()):
        if a["name"] == _PREFERRED_API:
            return i
    return None


def list_input_devices() -> list[str]:
    """Names of the available microphones, in the system's order."""
    api = _host_api_index()
    names = []
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and (api is None or d["hostapi"] == api) and d["name"] not in names:
            names.append(d["name"])
    return names


def _resolve(name: str | None):
    """Device name -> (index, extra_settings). Indices change when devices are plugged in or
    out, so we store the name and look it up on every recording. None = system default."""
    if name is None:
        return None, None
    for attempt in range(2):
        api = _host_api_index()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and d["name"] == name and (api is None or d["hostapi"] == api):
                extra = sd.WasapiSettings(auto_convert=True) if api is not None else None
                return i, extra
        if attempt == 0:
            refresh_devices()  # maybe it was plugged in after launch
    log.warning("Microphone %r not found; using the system default", name)
    return None, None


def refresh_devices():
    """PortAudio only enumerates devices when it starts; restart it to see hot-plugged mics.
    Only call while no stream is open."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        log.warning("Could not refresh the audio device list", exc_info=True)


class Recorder:
    def __init__(self, on_level=None, device: str | None = None):
        self.on_level = on_level
        self.device = device  # microphone name, or None for the system default
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
            index, extra = _resolve(self.device)
            # PortAudio resamples to 16 kHz for us (CoreAudio on macOS, MME / WASAPI
            # auto_convert on Windows).
            self._stream = sd.InputStream(
                device=index, samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=512,
                callback=self._callback, extra_settings=extra,
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
