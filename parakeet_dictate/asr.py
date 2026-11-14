"""Speech recognition backends. Both run Parakeet TDT 0.6B v3 locally.

  mlx   parakeet-mlx on Apple Silicon (Metal)
  onnx  onnx-asr on ONNX Runtime: CPU everywhere, or DirectML / CUDA if that build of
        onnxruntime is installed

All loading and inference runs on one dedicated thread (MLX streams are thread-local, and
it keeps the ONNX session single-threaded from our side too).
"""

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

from . import config

log = logging.getLogger("dictate")


class Transcriber:
    def __init__(self, backend: str = config.BACKEND):
        self.backend = backend
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")
        self._impl = None

    def load(self) -> Future:
        return self._thread.submit(self._load)

    def transcribe(self, audio: np.ndarray) -> str:
        """Blocking; call from a worker thread, never the UI thread."""
        return self._thread.submit(self._impl, audio).result()

    def _load(self):
        t0 = time.perf_counter()
        if self.backend == "mlx":
            self._impl = _load_mlx()
        elif self.backend == "onnx":
            self._impl = _load_onnx()
        else:
            raise ValueError(f"Unknown PARAKEET_BACKEND {self.backend!r}")
        self._impl(np.zeros(config.SAMPLE_RATE, dtype=np.float32))  # warm up kernels
        log.info("Model ready (%s) in %.1fs", self.backend, time.perf_counter() - t0)


def _load_mlx():
    import mlx.core as mx
    from parakeet_mlx import from_pretrained
    from parakeet_mlx.audio import get_logmel

    model = from_pretrained(config.MLX_MODEL_ID)

    def run(audio: np.ndarray) -> str:
        # Mel features must be computed in float32 (same as parakeet's own load_audio path).
        mel = get_logmel(mx.array(audio, dtype=mx.float32), model.preprocessor_config)
        return model.generate(mel)[0].text.strip()

    return run


def _load_onnx():
    import onnx_asr
    import onnxruntime as rt

    preferred = ["DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    available = rt.get_available_providers()
    providers = [p for p in preferred if p in available] or available
    log.info("ONNX Runtime providers: %s (quantization=%s)", providers, config.ONNX_QUANTIZATION)
    model = onnx_asr.load_model(
        config.ONNX_MODEL_ID, quantization=config.ONNX_QUANTIZATION, providers=providers
    )

    def run(audio: np.ndarray) -> str:
        return model.recognize(audio.astype(np.float32, copy=False), sample_rate=config.SAMPLE_RATE).strip()

    return run
