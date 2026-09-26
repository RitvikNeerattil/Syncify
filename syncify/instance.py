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
        return fh
    except OSError:
        fh.close()
        return None
