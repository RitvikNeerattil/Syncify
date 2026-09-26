"""App data location and per-device settings.

Everything Syncify keeps for itself (settings, song manifest, trash, log) lives
in the user's app data folder (%APPDATA%\\Syncify on Windows), so the exe can
live anywhere and the music folder only ever contains mp3s.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


def app_home() -> Path:
    if os.environ.get("SYNCIFY_HOME"):  # handy for testing several "devices" on one machine
        home = Path(os.environ["SYNCIFY_HOME"])
    elif os.name == "nt":
        home = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Syncify"
    elif sys.platform == "darwin":
        home = Path.home() / "Library" / "Application Support" / "Syncify"
    else:
        home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "syncify"
    home.mkdir(parents=True, exist_ok=True)
    return home


@dataclass
class Settings:
    device_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    device_name: str = field(default_factory=socket.gethostname)
    library_path: str = ""  # the folder Spotify reads Local Files from
    account_email: str = ""
    startup_default_applied: bool = False
    auto_sync_minutes: int = 10


class AppDirs:
    def __init__(self, home: Path | None = None):
        self.app = home or app_home()
        self.trash = self.app / "trash"
        self.bin = self.app / "bin"  # optional: drop ffmpeg.exe / deno.exe here
        for d in (self.app, self.trash, self.bin):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def manifest(self) -> Path:
        return self.app / "library.json"

    @property
    def settings_file(self) -> Path:
        return self.app / "settings.json"

    @property
    def log_file(self) -> Path:
        return self.app / "syncify.log"

    def load_settings(self) -> Settings:
        s = Settings()
        if self.settings_file.exists():
            data = json.loads(self.settings_file.read_text("utf-8"))
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        self.save_settings(s)  # persists a freshly generated device_id
        return s

    def save_settings(self, s: Settings) -> None:
        write_json_atomic(self.settings_file, asdict(s))


def write_json_atomic(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)
