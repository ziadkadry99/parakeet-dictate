<div align="center">

# 🦜 Parakeet Dictate

**Hold a key. Speak. Let go. Your words appear wherever you're typing.**

Push-to-talk dictation for **Windows** and **macOS**. NVIDIA's Parakeet speech model runs
on your own machine, so there's no cloud, no account and no subscription.

![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)
![macOS](https://img.shields.io/badge/macOS-Apple%20Silicon-000000?logo=apple&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![Offline](https://img.shields.io/badge/runs-100%25%20offline-2ea44f)
![Languages](https://img.shields.io/badge/languages-25-8f80ff)

</div>

---

## ✨ Features

- **🔒 Private.** Audio is transcribed on your computer and never uploaded. Internet is
  only used once, to download the model.
- **⚡ Fast.** Usually under a second from letting go of the key to seeing the text, even
  on a plain CPU (6 s of speech in about 0.85 s on Windows).
- **🎯 Accurate.** Uses [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3),
  with punctuation and capitalisation included.
- **🌍 25 European languages**, detected automatically. Just start talking.
- **⌨️ Works in any app** with a text box: browsers, editors, chat apps, Word, terminals.
- **📋 Leaves your clipboard alone.** Whatever you had copied is put back after each
  dictation, and on Windows dictations stay out of Win+V history.
- **🎙️ Choose your microphone** from the tray menu (Windows).
- **🔌 Starts with Windows** automatically (you can turn this off in the tray menu).
- **🔁 Pick your own hold key**: Fn, Right Ctrl, F13, or almost any other key.
- **🫧 A small waveform bar** at the bottom of the screen shows when it's listening.

## 🚀 How it works

```
  hold key  ──►  🎙️ recording (waveform bar shows)
  release   ──►  🧠 transcribing locally (< 1 s)
            ──►  📝 text is pasted where your cursor is
```

Tip: pressing another key while holding the hotkey cancels the recording, so shortcuts like
**Right Ctrl + C** still work as normal.

---

## 📦 Install

You'll need **[uv](https://docs.astral.sh/uv/)**, a Python package manager. It installs the
right Python (3.11) for you, so you don't need Python already. You'll also need **git**, plus
about **1–2 GB** of free disk space for the model.

### 🪟 Windows

**1. Install uv** (skip this if you already have it). Open PowerShell and run:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close PowerShell and open a new window so the `uv` command is found.

**2. Download and set up Parakeet Dictate:**

```powershell
git clone https://github.com/ziadkadry99/parakeet-dictate.git
cd parakeet-dictate
uv sync
```

**3. Run it:**

```powershell
uv run parakeet-dictate-gui      # runs in the background, no console window
# or
uv run parakeet-dictate          # keeps a console open so you can watch the log
```

The first launch downloads the model (~670 MB). After that it starts in a few seconds.

From now on it **starts automatically when you sign in to Windows**. You only need to run
it by hand this once. To turn this off, untick **Start with Windows** in the tray menu.

**4. Allow microphone access** if Windows asks: **Settings → Privacy & security →
Microphone → Let desktop apps access your microphone**.

### 🍎 macOS (Apple Silicon)

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # install uv, if you don't have it
git clone https://github.com/ziadkadry99/parakeet-dictate.git
cd parakeet-dictate
uv sync
uv run parakeet-dictate
```

The first launch downloads the model (~1.2 GB). Then grant these in **System Settings →
Privacy & Security**:

| Permission | Why |
|---|---|
| **Microphone** | to hear you |
| **Input Monitoring** | to see the hotkey |
| **Accessibility** | to paste the text |

Also set **System Settings → Keyboard → "Press 🌐 key to" → Do Nothing**, otherwise Fn
also opens the emoji picker.

> **Start at login:** after building the app (below), add `Parakeet Dictate.app` in
> **System Settings → General → Login Items**.

> **Optional: a real app bundle.** macOS gives permissions to whichever program runs the
> code, which by default is your terminal. To give Parakeet Dictate its own entry, run
> `./build_app.sh`. It creates `Parakeet Dictate.app`, which you can launch like any other
> app. Rebuilding it makes macOS ask for the permissions again.

---

## 🎙️ Using it

1. Start the app. An icon appears in the **system tray** (Windows) or **menu bar** (macOS).
   On Windows 11 it may be in the hidden-icons overflow (`^`); drag it onto the taskbar to
   keep it visible.
2. Wait until the icon turns **purple** ("Ready").
3. Click into any text box, **hold the hotkey**, speak, and **let go**. The text appears.

| Icon colour | Meaning |
|---|---|
| ⚪ grey | loading the model |
| 🟣 purple | ready |
| 🔴 red | recording |
| 🔵 cyan | transcribing |
| 🟠 orange | something's wrong (open the log) |
| 🟢 green | waiting for you to press a new hotkey |

### The hotkey

| | Default |
|---|---|
| **macOS** | **Fn** (🌐) |
| **Windows** | **Fn**, if your keyboard reports it to Windows; otherwise **Right Ctrl** |

On the **first launch on Windows**, the app asks you to press your Fn key. Many laptop
keyboards handle Fn in their own hardware and never tell Windows about it. If no key press
arrives within 20 seconds, the app uses Right Ctrl.

To change it: tray/menu-bar icon → **Set Hotkey…**, then press the key you want to hold
(Esc cancels). **Reset Hotkey** goes back to the default.

### Choosing a microphone (Windows)

Tray icon → **Microphone** lists every input device by the name Windows Settings uses.
Pick one, or leave it on **System default**. Your choice is remembered. If that mic is
unplugged, the app uses the default until it's back. Mics plugged in later appear the next
time you open the menu.

### Tray / menu-bar options

| Item | What it does |
|---|---|
| Status line | shows whether it's ready, plus timing for the last dictation |
| **Last: …** | click to copy the last transcript again |
| **Set Hotkey…** / **Reset Hotkey** | change the hold key |
| **Microphone** | choose the input device (Windows) |
| **Start with Windows** | launch automatically at sign-in; on by default (Windows) |
| **Open log folder** | for troubleshooting (Windows) |
| **Quit** | exit |

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| Nothing happens when I hold the key | Check that the icon is purple. If you chose Fn and it doesn't work, your keyboard probably hides Fn from the system: use **Set Hotkey…** to pick another key (Right Ctrl, F13…). |
| "Microphone unavailable" | Allow microphone access in privacy settings, or choose a different mic from the **Microphone** menu. |
| It records but types nothing | You may be recording from the wrong (silent) device. Pick your real mic in the **Microphone** menu. |
| No text in an app running as administrator | Windows blocks simulated keystrokes into elevated apps. Run Parakeet Dictate as administrator too. |
| It doesn't start at sign-in | Make sure **Start with Windows** is ticked, and that it's enabled in Task Manager → Startup apps. If you moved the project folder, launch it once by hand to update the entry. |
| Want a fresh start | Delete `settings.json` (location below) and relaunch. |

**Files**

| | Windows | macOS |
|---|---|---|
| Settings | `%LOCALAPPDATA%\ParakeetDictate\settings.json` | `~/Library/Application Support/ParakeetDictate/settings.json` |
| Log | `%LOCALAPPDATA%\ParakeetDictate\ParakeetDictate.log` | `~/Library/Logs/ParakeetDictate.log` |
| Model | Hugging Face cache (`~/.cache/huggingface`) | same |

---

## ⚙️ Under the hood

| | macOS (Apple Silicon) | Windows |
|---|---|---|
| Inference | [parakeet-mlx](https://github.com/senstella/parakeet-mlx) on Metal | [onnx-asr](https://github.com/istupakov/onnx-asr) on ONNX Runtime (CPU, int8) |
| UI | menu-bar app (rumps / AppKit) | tray app (Qt / PySide6) |
| Hotkey | Quartz event tap | low-level keyboard hook (pynput) |

**Environment variables**

| Variable | Default | |
|---|---|---|
| `PARAKEET_BACKEND` | `mlx` on macOS, `onnx` elsewhere | `onnx` also runs on macOS, which helps when testing the Windows code path |
| `PARAKEET_QUANT` | `int8` | ONNX model quantization; empty string = full fp32 (~2.4 GB) |

<details>
<summary><b>Project layout</b></summary>

```
parakeet_dictate/
  controller.py     platform-independent state machine (press / release / cancel -> paste)
  asr.py            Parakeet backends: MLX and ONNX
  recorder.py       16 kHz mic capture + device selection (sounddevice)
  overlay_model.py  pill animation: springs, waveform and colours (no UI toolkit)
  config.py         paths, settings.json, logging, single-instance lock
  mac_app.py        macOS: rumps menu bar, Quartz event tap, NSPasteboard + Cmd+V
  mac_hotkey.py     macOS: hotkey matching and rebinding
  mac_overlay.py    macOS: NSPanel renderer for the pill
  win_app.py        Windows: Qt tray, clipboard + Ctrl+V, first-run Fn capture, mic menu
  win_hotkey.py     Windows: low-level keyboard hook, hotkey matching and rebinding
  win_autostart.py  Windows: Start with Windows (HKCU Run key)
  qt_overlay.py     Windows: Qt renderer for the pill
dictate.py          launch script (the macOS .app runs this)
demo_gradio.py      browser demo with timestamps (macOS: uv sync --group demo)
docs/               Windows port notes
```

</details>

## 🙏 Credits

- [NVIDIA Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), the speech model
- [parakeet-mlx](https://github.com/senstella/parakeet-mlx) by senstella, for Apple Silicon inference
- [onnx-asr](https://github.com/istupakov/onnx-asr) by istupakov, for the ONNX export and runtime
