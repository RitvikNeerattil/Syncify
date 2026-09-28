"""The hub is the shared place devices sync through.

`Hub` is the interface. The app uses `DriveHub` (Google Drive, see drive_hub.py).
`FolderHub` stores everything in a plain local folder; it's used by the tests and
for trying sync between two instances on one machine (SYNCIFY_FOLDER_HUB).

Layout of a FolderHub:
    catalog/<device_id>.json   each device's view of the library (single writer per file,
                               so cloud-sync conflict copies can't happen)
    files/<sha256>.mp3         song files, named by content hash
"""
from __future__ import annotations

import json
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import write_json_atomic


class Hub(ABC):
    @abstractmethod
    def read_catalogs(self) -> dict[str, dict]:
        """{device_id: {"device_name": str, "songs": {song_id: entry}}} for every device."""

    @abstractmethod
    def write_catalog(self, device_id: str, device_name: str, songs: dict[str, dict], *,
                      synced_at: float | None = None, platform: str = "") -> None: ...

    # ---- devices ----
    @abstractmethod
    def read_removed(self) -> dict[str, dict]:
        """{device_id: {"by": device name, "at": timestamp}} for devices removed from the account."""

    @abstractmethod
    def _write_removed(self, removed: dict[str, dict]) -> None: ...

    @abstractmethod
    def delete_catalog(self, device_id: str) -> None: ...

    def remove_device(self, device_id: str, by: str) -> None:
        """Sign a device out of Syncify remotely: it notices on its next sync. Its files are untouched."""
        import time

        removed = self.read_removed()
        removed[device_id] = {"by": by, "at": time.time()}
        self._write_removed(removed)
        self.delete_catalog(device_id)

    def unremove_device(self, device_id: str) -> None:
        """Called when a removed device signs in again."""
        removed = self.read_removed()
        if removed.pop(device_id, None) is not None:
            self._write_removed(removed)

    @abstractmethod
    def has_blob(self, sha: str) -> bool: ...

    @abstractmethod
    def put_blob(self, sha: str, src: Path) -> None: ...

    @abstractmethod
    def get_blob(self, sha: str, dest: Path) -> bool:
        """Copy the blob to dest. False if the hub doesn't have it."""


class FolderHub(Hub):
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        if not self.path.is_dir():
            raise FileNotFoundError(f"Hub folder not found: {self.path}")
        self.catalog = self.path / "catalog"
        self.files = self.path / "files"
        self.catalog.mkdir(exist_ok=True)
        self.files.mkdir(exist_ok=True)

    def read_catalogs(self) -> dict[str, dict]:
        out = {}
        for f in self.catalog.glob("*.json"):
            try:
                data = json.loads(f.read_text("utf-8"))
                out[f.stem] = data
            except (OSError, ValueError):
                continue  # half-synced file; we'll get it next time
        return out

    def write_catalog(self, device_id, device_name, songs, *, synced_at=None, platform=""):
        write_json_atomic(self.catalog / f"{device_id}.json", {
            "device_name": device_name, "synced_at": synced_at, "platform": platform, "songs": songs})

    def read_removed(self):
        f = self.path / "removed.json"
        try:
            return json.loads(f.read_text("utf-8")) if f.exists() else {}
        except (OSError, ValueError):
            return {}

    def _write_removed(self, removed):
        write_json_atomic(self.path / "removed.json", removed)

    def delete_catalog(self, device_id):
        f = self.catalog / f"{device_id}.json"
        if f.exists():
            f.unlink()

    def _blob(self, sha: str) -> Path:
        return self.files / f"{sha}.mp3"

    def has_blob(self, sha):
        return self._blob(sha).exists()

    def put_blob(self, sha, src):
        if self.has_blob(sha):
            return
        tmp = self._blob(sha).with_suffix(".part")
        shutil.copyfile(src, tmp)
        os.replace(tmp, self._blob(sha))

    def get_blob(self, sha, dest):
        if not self.has_blob(sha):
            return False
        shutil.copyfile(self._blob(sha), dest)
        return True
