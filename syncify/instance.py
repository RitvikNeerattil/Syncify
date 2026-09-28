"""Keep one Syncify process at a time (a second window, or the login sync
running while the app is open, would fight over the manifest)."""
from __future__ import annotations

import os
from pathlib import Path


def acquire(path: Path):
    """Returns an open handle that holds the lock, or None if another process has it."""
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    # Record our PID after the locked first byte so a second launch can find our window.
    try:
        fh.seek(1)
        fh.truncate()
        fh.write(f" {os.getpid()}")
        fh.flush()
    except OSError:
        pass
    return fh


def owner_pid(path: Path) -> int | None:
    try:
        with open(path, "rb") as f:
            f.seek(1)
            return int(f.read().decode().strip() or 0) or None
    except (OSError, ValueError):
        return None


def focus_window_of(pid: int) -> bool:
    """Windows only: bring the visible top-level window owned by `pid` to the front."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(each, 0)
    if not found:
        return False
    user32.ShowWindow(found[0], 9)  # SW_RESTORE
    user32.SetForegroundWindow(found[0])
    return True
