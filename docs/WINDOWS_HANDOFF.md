# Windows port: hand-off notes

Branch `cross-platform`. The Windows frontend is written and its logic is tested, but it
has **never run on Windows**. Everything below was checked on macOS, using stubs for the
Win32 calls. Your job is to get it running on real Windows hardware, fix what breaks, and
then package it.

## Set up

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # if uv is missing
git clone https://github.com/ziadkadry99/parakeet-dictate.git
cd parakeet-dictate
git checkout cross-platform
uv sync                                  # Python 3.11 (pinned), onnx-asr, PySide6-Essentials, pynput
uv run python -m parakeet_dictate        # console stays open, so you can read the log
```

The first run downloads `istupakov/parakeet-tdt-0.6b-v3-onnx` (int8, ~670 MB) to the
Hugging Face cache. Log: `%LOCALAPPDATA%\ParakeetDictate\ParakeetDictate.log`. Settings
(the hotkey): `%LOCALAPPDATA%\ParakeetDictate\settings.json`; delete this file to repeat the
first-run flow.

## How the Windows side works

- `win_app.py`: Qt tray icon and menu (status, last transcript, Set Hotkey…, Reset, open
  log folder, Quit). Includes `Paster`: it saves the clipboard as QMimeData, sets the text,
  sends Ctrl+V with pynput, and restores the clipboard 400 ms later unless someone else
  changed it in between (checked with a private MIME marker).
- `win_hotkey.py`: pynput `Listener(win32_event_filter=...)`. We only use the raw
  `KBDLLHOOKSTRUCT` (vk, scan code, extended flag) and never pynput's key translation.
  The hotkey is stored as `{"vk", "scan", "ext", "name"}`; keys with vk 0 or 0xFF are
  matched on their scan code. Injected events (including our own Ctrl+V) are ignored, and
  so is AltGr's fake Left Ctrl (scan 0x21D).
- First run (no saved hotkey): the app captures the next *non-typing* key, shows a tray
  balloon asking for Fn, and waits 20 s before falling back to Right Ctrl. Esc keeps Right
  Ctrl. A captured key with vk 0/0xFF is labelled "Fn".
- `qt_overlay.py`: a frameless, translucent, always-on-top window that ignores the mouse,
  with WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW added through ctypes. It
  paints `overlay_model.py`, which is shared with macOS. There's no acrylic blur; the pill
  is just a little more opaque.
- `asr.py`: onnx-asr, preferring the DirectML provider, then CUDA, then CPU. Only the CPU
  build of onnxruntime is installed right now (see below).

## Already verified (on macOS)

- ONNX backend: same Parakeet v3 model; a 4.3 s clip was transcribed exactly in 0.2 s
  (int8, CPU, M-series).
- Hotkey filter, fed synthetic KBDLLHOOKSTRUCTs: hold/release, repeats ignored, Left vs
  Right Ctrl, hotkey+C cancels, injected events ignored, vendor vk 0xFF captured and matched
  by scan code, Esc cancels a rebind, AltGr doesn't cancel a Right Alt hold.
- Tray app, offscreen Qt: first-run capture → saved to settings.json → status text updates;
  clipboard save → paste → restore; Reset.
- Overlay renders every state (record / think / error / done) and hides itself after fading.
- `uv lock` resolves for win_amd64 / Python 3.11.

## Verified on real Windows (2026-11-14, Windows 11, Python 3.11, CPU only)

- `uv sync` and the first-run model download work. The model loads in about 5 s once cached, and
  6 s of speech transcribes exactly in 0.85 s (int8, CPU).
- The tray app starts with no errors, and a second launch exits right away (msvcrt lock).
- Overlay: showing it leaves the foreground window unchanged. Tested with a separate target
  process; the paste lands in the target.
- Clipboard: the text and image clipboards are both restored after a paste. Qt's
  `x-qt-windows-mime` names do register the real `ExcludeClipboardContentFromMonitorProcessing`,
  `CanIncludeInClipboardHistory` and `CanUploadToCloudClipboard` formats (checked with
  EnumClipboardFormats).
- Microphone: the new **Microphone** tray submenu lists the WASAPI inputs (full names), with
  "System default" first. The choice is saved as `"microphone"` (the device name) in settings.json.
  Every device opens at 16 kHz through WASAPI `auto_convert`, in under 20 ms. If the saved mic is
  missing, PortAudio is re-enumerated once and then the system default is used. The submenu
  re-enumerates each time it opens, so hot-plugged mics appear.
- Start with Windows (`win_autostart.py`): adds a `HKCU\...\Run` value pointing at the venv's
  `pythonw.exe -m parakeet_dictate`. It's on by default (the first launch turns it on), the tray
  toggle is saved as `start_at_login`, and each launch rewrites the command so it follows a moved
  project. Launching that exact command from `C:\` works, and the model loads with
  `HF_HUB_OFFLINE=1` (onnx-asr tries the local cache first), so sign-in without network is fine.
- Not yet tested, because it needs a person at the keyboard (injected keys are ignored by
  design): whether Fn reaches the hook, real hold/release dictation, hotkey+key cancel, AltGr,
  multi-monitor/DPI placement, and pasting into elevated windows.

## To test on real Windows (in priority order)

1. **Does the user's Fn key reach the hook at all?** Delete settings.json, launch, press Fn
   at the balloon, and check the log for `Captured key {...}`.
   - If nothing arrives, the keyboard handles Fn in firmware. Options: (a) the vendor's
     software may be able to remap Fn or an Fn combination to F13–F24 (bindable as normal);
     (b) some keyboards report Fn only as a vendor-defined HID usage; reading that needs a
     Raw Input listener (`RegisterRawInputDevices`) on the HID usage page, not the keyboard
     hook. Ask the user before building (b).
   - If something arrives, hold it and dictate; check that releasing it is detected.
2. Dictation end to end: Notepad, a browser text field, VS Code, Word. Make sure the overlay
   **never takes focus** from the target window (the paste would land in the wrong place).
3. Clipboard: copy text, an image and a file in Explorer, then dictate. Each should be
   restored. Also check that dictations don't appear in Win+V history. That relies on
   Qt's `application/x-qt-windows-mime;value="..."` names mapping to the
   `ExcludeClipboardContentFromMonitorProcessing` / `CanIncludeInClipboardHistory`
   formats; confirm they actually do.
4. Hotkey+other key cancels (e.g. Right Ctrl+C still copies, with no dictation). Set
   Hotkey… with Right Alt on a layout with AltGr.
5. Overlay position: centred above the taskbar on the monitor containing the mouse;
   check high-DPI (150%), a second monitor, and a taskbar on the left or top.
6. Microphone: the default input device; also with mic privacy turned off. Starting the
   recording should fail with "Microphone unavailable", not silently record nothing. If
   16 kHz opening fails on some devices, open at the device rate and resample.
7. Tray icon visible (Windows 11 may hide it in the overflow), menu works, Quit exits
   cleanly, and a second launch exits immediately (msvcrt lock).
8. Paste into an elevated window (expected to fail because of UIPI); log it and decide
   whether that's acceptable.

## Still to build

- **Packaging**: PyInstaller (or Nuitka) one-folder build, windowed, with an icon. Include
  the onnx-asr/onnxruntime DLLs and the PySide6 plugins. Set `HF_HUB_OFFLINE=1` once the
  model is cached; the macOS launcher does the same.
- **GPU (optional)**: `onnxruntime-directml` replaces `onnxruntime` (they can't both be
  installed). That would need the `onnx-asr[cpu]` extra swapped for plain `onnx-asr` plus
  `onnxruntime-directml` in pyproject. CPU int8 is probably fast enough; measure first.

Keep `controller.py`, `overlay_model.py`, `asr.py` and `recorder.py` platform-neutral.
The macOS app shares them.
