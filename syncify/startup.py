"""Optional: run a quiet sync every time you log in to Windows.

Adds a value under HKCU\\...\\Run (current user only, no admin needed) that
launches `Syncify --sync-only`, which syncs and exits without opening a window.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_NAME = "Syncify"


def supported() -> bool:
    return os.name == "nt"


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --sync-only'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    run_py = Path(__file__).resolve().parent.parent / "run.py"
    return f'"{exe}" "{run_py}" --sync-only'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as k:
            winreg.QueryValueEx(k, _NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    if not supported():
        return False
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, _NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(k, _NAME)
            except OSError:
                pass
    return is_enabled()
