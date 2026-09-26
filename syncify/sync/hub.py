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
    def write_catalog(self, device_id: str, device_name: str, songs: dict[str, dict]) -> None: ...

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

    def write_catalog(self, device_id, device_name, songs):
        write_json_atomic(self.catalog / f"{device_id}.json", {"device_name": device_name, "songs": songs})

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
