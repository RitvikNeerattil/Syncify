"""YouTube search + audio download, all through yt-dlp (no API key needed).

Requirements on the machine: ffmpeg (mp3 conversion) and Deno (yt-dlp needs a
JavaScript runtime for full YouTube support). Either can be on PATH or dropped
into <library>/.syncify/bin/.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable

import yt_dlp

_URL = re.compile(r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", re.IGNORECASE)


def tool_status(bin_dir: Path) -> dict:
    def find(name):
        local = bin_dir / (f"{name}.exe" if os.name == "nt" else name)
        return str(local) if local.exists() else shutil.which(name)

    return {"ffmpeg": find("ffmpeg"), "deno": find("deno"), "node": find("node"),
            "yt_dlp": yt_dlp.version.__version__}


class YouTube:
    def __init__(self, bin_dir: Path):
        self.bin_dir = bin_dir

    def _opts(self, **extra) -> dict:
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
        status = tool_status(self.bin_dir)
        if status["ffmpeg"]:
            opts["ffmpeg_location"] = str(Path(status["ffmpeg"]).parent)
        if status["deno"]:
            opts["js_runtimes"] = {"deno": {"path": status["deno"]}}
        elif status["node"]:  # Node 20+ works too if you already have it
            opts["js_runtimes"] = {"node": {"path": status["node"]}}
        opts.update(extra)
        return opts

    def search(self, query: str, limit: int = 15) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        if _URL.match(query):
            info = self.info(query)
            return [_result(info)]
        with yt_dlp.YoutubeDL(self._opts(extract_flat=True, skip_download=True)) as ydl:
            data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return [_result(e) for e in (data or {}).get("entries", []) if e and e.get("id")]

    def info(self, url_or_id: str) -> dict:
        url = url_or_id if _URL.match(url_or_id) else f"https://www.youtube.com/watch?v={url_or_id}"
        with yt_dlp.YoutubeDL(self._opts(skip_download=True)) as ydl:
            return ydl.sanitize_info(ydl.extract_info(url, download=False))

    def download_mp3(self, url: str, workdir: Path, on_progress: Callable[[float, str], None] | None = None) -> Path:
        """Best available audio -> 320k mp3 in workdir. Returns the mp3 path."""

        def hook(d):
            if not on_progress:
                return
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = (d.get("downloaded_bytes", 0) / total * 90) if total else 0
                on_progress(pct, "Downloading")
            elif d.get("status") == "finished":
                on_progress(90, "Converting to mp3")

        opts = self._opts(
            format="bestaudio/best",
            outtmpl=str(workdir / "%(id)s.%(ext)s"),
            progress_hooks=[hook],
            postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}],
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        mp3 = workdir / f"{info['id']}.mp3"
        if not mp3.exists():
            found = list(workdir.glob("*.mp3"))
            if not found:
                raise RuntimeError("yt-dlp finished but no mp3 was produced (is ffmpeg installed?)")
            mp3 = found[0]
        return mp3


def _result(e: dict) -> dict:
    vid = e.get("id")
    dur = e.get("duration") or 0
    return {
        "id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": e.get("title") or "",
        "channel": e.get("channel") or e.get("uploader") or "",
        "duration": f"{int(dur) // 60}:{int(dur) % 60:02d}" if dur else "",
        "views": e.get("view_count"),
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
    }


def best_thumbnail(info: dict) -> str:
    thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
    jpgs = [t for t in thumbs if ".jpg" in t["url"]] or thumbs
    if jpgs:
        return max(jpgs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0) or t.get("preference") or 0)["url"]
    return info.get("thumbnail") or ""
