# Parakeet Dictate

Hold a key anywhere, speak, let go, and the text is typed into whatever app has focus.
Speech recognition runs locally with NVIDIA's
[Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) (25 European
languages, auto-detected). Nothing leaves your machine.

| | macOS (Apple Silicon) | Windows |
|---|---|---|
| Inference | [parakeet-mlx](https://github.com/senstella/parakeet-mlx) on Metal | [onnx-asr](https://github.com/istupakov/onnx-asr) on ONNX Runtime (CPU, int8) |
| UI | Menu-bar app (rumps / AppKit) | Tray app (Qt) |
| Default hotkey | Fn | Fn, if your keyboard reports it to Windows; otherwise Right Ctrl |
| Status | Daily use | Ported; needs testing on real hardware ([hand-off notes](docs/WINDOWS_HANDOFF.md)) |

While you hold the key, a small waveform pill shows at the bottom of the screen. Releasing
the key transcribes the audio (typically well under a second) and pastes the text. Your
previous clipboard is put back afterwards. Pressing another key while the hotkey is held
cancels the recording, so shortcuts like Fn+arrows keep working.

## Changing the hotkey

Choose **Set Hotkey…** in the menu-bar or tray menu, then press the key you want to hold.
Modifiers (left and right sides are told apart), function keys and other keys all work;
Esc cancels. Your choice is saved to `settings.json`:

- macOS: `~/Library/Application Support/ParakeetDictate/`
- Windows: `%LOCALAPPDATA%\ParakeetDictate\`

On Windows the first launch asks you to press Fn. Many laptops handle Fn inside the keyboard
and never tell Windows about it. If no key press arrives, the app falls back to Right Ctrl.

## Run it

Requires [uv](https://docs.astral.sh/uv/). The first launch downloads the model (~1.2 GB on
macOS, ~670 MB on Windows) from Hugging Face.

```sh
uv sync
uv run python -m parakeet_dictate        # or: uv run parakeet-dictate
```

On Windows, `uv run parakeet-dictate-gui` runs the app without a console window.

### macOS permissions

Grant **Microphone**, **Input Monitoring** (to see the hotkey) and **Accessibility** (to
paste). Also set System Settings → Keyboard → "Press 🌐 key to" → **Do Nothing**, or Fn
will also open the emoji picker.

These permissions belong to whichever process runs the code. To give the app its own
identity, build the small native launcher; it embeds the project's venv Python:

```sh
./build_app.sh        # creates "Parakeet Dictate.app"
```

Rebuilding changes the code signature, so macOS asks for the permissions again.

### Windows notes

- Microphone: Settings → Privacy & security → Microphone → allow desktop apps.
- Windows blocks synthetic key presses into apps running as administrator, so pasting into
  them needs the app to run elevated as well.

## Configuration (environment variables)

| Variable | Default | |
|---|---|---|
| `PARAKEET_BACKEND` | `mlx` on macOS, `onnx` elsewhere | `onnx` also runs on macOS, which is handy for testing the Windows path |
| `PARAKEET_QUANT` | `int8` | ONNX model quantization; empty string = fp32 (~2.4 GB) |

## Layout

```
parakeet_dictate/
  controller.py     platform-independent state machine (press / release / cancel -> paste)
  asr.py            Parakeet backends: MLX and ONNX
  recorder.py       16 kHz mic capture (sounddevice)
  overlay_model.py  pill animation: springs, waveform and colours (no UI toolkit)
  config.py         paths, settings.json, logging, single-instance lock
  mac_app.py        macOS: rumps menu bar, Quartz event tap, NSPasteboard + Cmd+V
  mac_hotkey.py     macOS: hotkey matching and rebinding
  mac_overlay.py    macOS: NSPanel renderer for the pill
  win_app.py        Windows: Qt tray, clipboard + Ctrl+V, first-run Fn capture
  win_hotkey.py     Windows: low-level keyboard hook, hotkey matching and rebinding
  qt_overlay.py     Windows: Qt renderer for the pill
dictate.py          launch script (the macOS .app runs this)
demo_gradio.py      browser demo with timestamps (macOS: uv sync --group demo)
```

Logs: `~/Library/Logs/ParakeetDictate.log` (macOS) or `%LOCALAPPDATA%\ParakeetDictate\ParakeetDictate.log` (Windows).
