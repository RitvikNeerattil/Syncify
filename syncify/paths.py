"""Filename cleanup and the 'stay inside the library' guard."""
from __future__ import annotations

import re
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class OutsideLibraryError(PermissionError):
    pass


def safe_filename(name: str, max_len: int = 150) -> str:
    """Make a string safe to use as a Windows/macOS/Linux filename (no extension)."""
    name = _ILLEGAL.sub("", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if not name:
        name = "Untitled"
    if name.upper() in _RESERVED:
        name = f"_{name}"
    return name[:max_len].rstrip(". ")


def inside(root: Path, rel_or_abs: str | Path) -> Path:
    """Resolve a path and refuse anything that escapes the library root."""
    root = root.resolve()
    p = Path(rel_or_abs)
    p = (p if p.is_absolute() else root / p).resolve()
    if p != root and root not in p.parents:
        raise OutsideLibraryError(f"{p} is outside the library folder {root}")
    return p


def unique_mp3_name(root: Path, title: str, artist: str = "", taken: set[str] | None = None) -> str:
    """'Song.mp3', falling back to 'Song (Artist).mp3', then 'Song (2).mp3'..."""
    taken = {t.lower() for t in (taken or set())}

    def free(n: str) -> bool:
        return n.lower() not in taken and not (root / n).exists()

    base = safe_filename(title)
    candidates = [f"{base}.mp3"]
    if artist:
        candidates.append(f"{safe_filename(f'{title} ({artist})')}.mp3")
    for c in candidates:
        if free(c):
            return c
    i = 2
    while not free(f"{base} ({i}).mp3"):
        i += 1
    return f"{base} ({i}).mp3"
