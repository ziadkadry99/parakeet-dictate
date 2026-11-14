"""Parakeet TDT 0.6B v3 demo: record or upload audio, get a transcript with timestamps."""

import subprocess
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import gradio as gr
from parakeet_mlx import from_pretrained

MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v3"

# MLX streams are thread-local, and Gradio runs handlers on worker threads.
# So every MLX call (load, warm-up, transcribe) goes through this one thread.
MLX_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")
model = None


def _load():
    global model
    print(f"Loading {MODEL_ID} (first run downloads ~1.2 GB)...", flush=True)
    t0 = time.perf_counter()
    model = from_pretrained(MODEL_ID)
    print(f"Model loaded in {time.perf_counter() - t0:.1f}s", flush=True)
    # Warm up MLX kernels so the first real request reflects true speed.
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        with wave.open(f.name, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000 * 2)
        model.transcribe(f.name)
    print("Warm-up done", flush=True)


MLX_THREAD.submit(_load).result()


def audio_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def fmt_ts(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:05.2f}"


def transcribe(audio_path: str | None):
    if not audio_path:
        return "", [], "No audio yet: record something or upload a file."

    duration = audio_duration(audio_path)
    t0 = time.perf_counter()
    # Chunk long audio so memory stays bounded; short clips go through in one pass.
    chunk = 120.0 if duration > 120 else None
    result = MLX_THREAD.submit(
        model.transcribe, audio_path, chunk_duration=chunk, overlap_duration=15.0
    ).result()
    elapsed = time.perf_counter() - t0

    rows = [[fmt_ts(s.start), fmt_ts(s.end), s.text.strip()] for s in result.sentences]
    speed = duration / elapsed if elapsed > 0 else 0
    stats = (
        f"**Audio:** {duration:.1f}s  ·  **Processing:** {elapsed:.2f}s  ·  "
        f"**Speed:** {speed:.0f}× real-time  ·  **Sentences:** {len(rows)}"
    )
    return result.text.strip(), rows, stats


with gr.Blocks(title="Parakeet TDT 0.6B v3") as demo:
    gr.Markdown(
        "# 🦜 Parakeet TDT 0.6B v3\n"
        "NVIDIA's multilingual ASR model (25 European languages, auto-detected), "
        "running locally on Apple Silicon via MLX. Record from your mic or upload a file."
    )
    with gr.Row():
        with gr.Column(scale=1):
            audio = gr.Audio(sources=["microphone", "upload"], type="filepath", label="Audio")
            btn = gr.Button("Transcribe", variant="primary")
            stats = gr.Markdown()
        with gr.Column(scale=2):
            text = gr.Textbox(label="Transcript", lines=8)
            table = gr.Dataframe(
                headers=["Start", "End", "Sentence"],
                label="Timestamps",
                wrap=True,
                interactive=False,
            )

    btn.click(transcribe, inputs=audio, outputs=[text, table, stats])
    # Auto-transcribe when a mic recording finishes or a file is uploaded.
    audio.stop_recording(transcribe, inputs=audio, outputs=[text, table, stats])
    audio.upload(transcribe, inputs=audio, outputs=[text, table, stats])

if __name__ == "__main__":
    demo.launch(inbrowser=True)
