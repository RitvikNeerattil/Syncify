"""Windows notifications (used after the quiet login sync) and the syncify:// link
that opens the app from a notification.

Unpackaged apps need two registry entries under HKCU (no admin needed):
  Software\\Classes\\AppUserModelId\\<AUMID>   name + icon shown on the notification
  Software\\Classes\\syncify                   makes syncify://settings open Syncify on Settings
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path

log = logging.getLogger("syncify.notify")

AUMID = "RitvikNeerattil.Syncify"
PROTOCOL = "syncify"


def supported() -> bool:
    return os.name == "nt"


def _bundled_icon() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / "syncify.ico"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    run_py = Path(__file__).resolve().parent.parent / "run.py"
    return f'"{exe}" "{run_py}" "%1"'


def register(app_dir: Path) -> None:
    """Register the notification identity and the syncify:// link. Safe to call on every launch."""
    if not supported():
        return
    import winreg

    try:
        icon = app_dir / "syncify.ico"
        if _bundled_icon().exists():
            shutil.copyfile(_bundled_icon(), icon)  # a stable path; the exe's own files move around
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{AUMID}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "Syncify")
            if icon.exists():
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(icon))
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL}") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Syncify")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL}\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _launch_command())
    except OSError:
        log.exception("couldn't register notifications")


def show(title: str, body: str, open_page: str = "") -> None:
    """Show a Windows notification. Clicking it opens Syncify (on `open_page`, e.g. 'settings')."""
    if not supported():
        return
    try:
        from windows_toasts import InteractableWindowsToaster, Toast

        toaster = InteractableWindowsToaster("Syncify", AUMID)
        toaster.show_toast(Toast([title, body], launch_action=f"{PROTOCOL}://{open_page or 'open'}"))
        time.sleep(1)  # give Windows a moment before a --sync-only process exits
    except Exception:
        log.exception("couldn't show notification")


def page_from_argv(argv: list[str]) -> str:
    """'syncify://settings' on the command line -> 'settings'."""
    for a in argv:
        if a.lower().startswith(f"{PROTOCOL}://"):
            return a.split("://", 1)[1].strip("/").lower()
    return ""
