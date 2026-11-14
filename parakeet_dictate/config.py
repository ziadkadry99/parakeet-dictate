"""Settings, per-OS paths, logging and the single-instance lock."""

import json
import logging
import os
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

SAMPLE_RATE = 16000
MIN_HOLD_SEC = 0.25          # shorter hotkey taps are treated as accidental and ignored
MIN_AUDIO_SEC = 0.2
PASTE_RESTORE_DELAY = 0.4    # wait for the target app to read the clipboard before restoring it
TRAILING_SPACE = True        # so consecutive dictations don't run together

# ASR backend: "mlx" (Apple Silicon) or "onnx" (Windows, or anywhere ONNX Runtime runs).
BACKEND = os.environ.get("PARAKEET_BACKEND", "mlx" if IS_MAC else "onnx")
MLX_MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v3"
ONNX_MODEL_ID = "nemo-parakeet-tdt-0.6b-v3"      # onnx-asr name -> istupakov/parakeet-tdt-0.6b-v3-onnx
# int8 is ~670 MB and transcribes a few seconds of speech in ~0.2 s on CPU. "" = full fp32.
ONNX_QUANTIZATION = os.environ.get("PARAKEET_QUANT", "int8") or None

if IS_MAC:
    LOG_PATH = Path.home() / "Library/Logs/ParakeetDictate.log"
    LOCK_PATH = Path.home() / "Library/Caches/ParakeetDictate.lock"
    SETTINGS_PATH = Path.home() / "Library/Application Support/ParakeetDictate/settings.json"
else:
    _base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "ParakeetDictate"
    _base.mkdir(parents=True, exist_ok=True)
    LOG_PATH = _base / "ParakeetDictate.log"
    LOCK_PATH = _base / "ParakeetDictate.lock"
    SETTINGS_PATH = _base / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logging.getLogger("dictate").exception("Bad settings file %s; using defaults", SETTINGS_PATH)
        return {}


def save_setting(key: str, value):
    s = load_settings()
    s[key] = value
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS_PATH)


def setup_logging() -> logging.Logger:
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stderr is not None:  # None under pythonw / windowed PyInstaller builds
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers
    )
    return logging.getLogger("dictate")


def single_instance_lock():
    """Exit if another copy is already running (e.g. login item + a manual launch).

    Returns the open lock file; keep a reference to it for the life of the process.
    """
    f = open(LOCK_PATH, "a+")
    try:
        if IS_WIN:
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:  # BlockingIOError on POSIX, PermissionError on Windows
        logging.getLogger("dictate").info("Another instance is already running; exiting")
        sys.exit(0)
    return f
