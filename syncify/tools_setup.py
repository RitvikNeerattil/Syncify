"""First-run download of ffmpeg and Deno into the app data folder (Windows).

yt-dlp needs ffmpeg to make mp3s and a JavaScript runtime (Deno) for YouTube.
Instead of making users install them, Syncify fetches official builds once
(~130 MB total) into %APPDATA%\\Syncify\\bin, where youtube.py looks first.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from .youtube import tool_status

log = logging.getLogger("syncify.setup")

DOWNLOADS = {
    # yt-dlp's own ffmpeg builds (patched for yt-dlp). Shared build = smaller download.
    "ffmpeg": ("https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
               "ffmpeg-master-latest-win64-gpl-shared.zip", ("ffmpeg.exe", "ffprobe.exe", ".dll")),
    "deno": ("https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
             ("deno.exe",)),
}


class ToolSetup:
    def __init__(self, bin_dir: Path):
        self.bin = bin_dir
        self.status = {"running": False, "message": "", "progress": 0, "error": None}
        self._lock = threading.Lock()

    def missing(self) -> list[str]:
        st = tool_status(self.bin)
        out = []
        if not st["ffmpeg"]:
            out.append("ffmpeg")
        if not (st["deno"] or st["node"]):
            out.append("deno")
        return out

    def start(self) -> None:
        if os.name != "nt" or not self.missing():
            return
        threading.Thread(target=self.run, daemon=True).start()

    def run(self) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            self.status.update(running=True, error=None)
            for name in self.missing():
                url, keep = DOWNLOADS[name]
                self._install(name, url, keep)
            self.status.update(message="Ready", progress=100)
        except Exception as e:
            log.exception("tool setup failed")
            self.status.update(error=f"Couldn't download {e}", message="Setup failed")
        finally:
            self.status["running"] = False
            self._lock.release()

    def _install(self, name: str, url: str, keep: tuple[str, ...]) -> None:
        with tempfile.TemporaryDirectory(prefix="syncify-setup-") as tmp:
            zpath = Path(tmp) / f"{name}.zip"
            with urllib.request.urlopen(url, timeout=60) as r, open(zpath, "wb") as out:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while chunk := r.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    pct = int(done / total * 100) if total else 0
                    self.status.update(progress=pct, message=f"Downloading {name} ({done >> 20} MB)")
            self.status["message"] = f"Installing {name}"
            with zipfile.ZipFile(zpath) as z:
                for member in z.namelist():
                    base = member.rsplit("/", 1)[-1]
                    if base and any(base == k or (k.startswith(".") and base.endswith(k)) for k in keep):
                        with z.open(member) as src, open(self.bin / base, "wb") as dst:
                            shutil.copyfileobj(src, dst)
