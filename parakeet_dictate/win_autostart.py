"""Start with Windows: a value under HKCU\\...\\Run (per user, no admin rights, and it appears
in Task Manager's Startup tab).

On by default: the first launch turns it on. After that the user's choice (tray menu) is kept
in settings.json, and each launch rewrites the command so it follows the project if it moves.
"""

import logging
import sys
import winreg
from pathlib import Path

log = logging.getLogger("dictate")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ParakeetDictate"


def launch_command() -> str:
    """Command that starts this app with no console window."""
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):  # packaged build (PyInstaller etc.)
        return f'"{exe}"'
    pythonw = exe.with_name("pythonw.exe")  # same venv, windowed interpreter
    if not pythonw.exists():
        pythonw = exe
    return f'"{pythonw}" -m parakeet_dictate'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def set_enabled(on: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            cmd = launch_command()
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, cmd)
            log.info("Start with Windows: on (%s)", cmd)
        else:
            try:
                winreg.DeleteValue(k, VALUE_NAME)
            except FileNotFoundError:
                pass
            log.info("Start with Windows: off")
