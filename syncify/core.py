"""Glue between the library, YouTube and sync. Used by both the UI and the CLI."""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .config import AppDirs
from .google_account import GoogleAccount
from .library import Library
from .metadata import SongMeta, fetch_cover, guess_from_info, write_tags
from .sync import DriveHub, FolderHub, SyncReport, sync
from .tools_setup import ToolSetup
from .youtube import YouTube, best_thumbnail, tool_status

log = logging.getLogger("syncify")


class Syncify:
    def __init__(self, dirs: AppDirs | None = None):
        self.dirs = dirs or AppDirs()
        self.settings = self.dirs.load_settings()
        self.account = GoogleAccount(self.dirs, self.settings)
        self.library: Library | None = Library(self.dirs, self.settings) if self.settings.library_path else None
        self.yt = YouTube(self.dirs.bin)
        self.tool_setup = ToolSetup(self.dirs.bin)
        self.jobs: dict[str, dict] = {}
        self._sync_lock = threading.Lock()
        self.last_sync: dict = {"at": None, "message": "Not synced yet", "ok": None}

    # ---------- status ----------
    def tools(self) -> dict:
        return tool_status(self.dirs.bin)

    def require_library(self) -> Library:
        if not self.library:
            raise RuntimeError("Pick your music folder first (Settings)")
        return self.library

    def set_library_path(self, path: str) -> int:
        """First-time pick, or move every managed song to a new folder. Returns songs moved."""
        if self.library:
            return self.library.relocate(Path(path))
        self.settings.library_path = str(Path(path).expanduser().resolve())
        self.dirs.save_settings(self.settings)
        self.library = Library(self.dirs, self.settings)
        return 0

    def _hub(self):
        # SYNCIFY_FOLDER_HUB lets you test sync between two local instances without Google.
        if os.environ.get("SYNCIFY_FOLDER_HUB"):
            return FolderHub(os.environ["SYNCIFY_FOLDER_HUB"])
        if not self.account.signed_in():
            return None
        return DriveHub(self.account.session())

    # ---------- search / prepare ----------
    def search(self, query: str) -> list[dict]:
        results = self.yt.search(query)
        have = {e.get("video_id") for e in self.library.visible()} if self.library else set()
        for r in results:
            r["in_library"] = r["id"] in have
        return results

    def prepare(self, video_id: str) -> dict:
        """Full video info + guessed tags, shown in the edit form before downloading."""
        info = self.yt.info(video_id)
        return {
            "video_id": info["id"],
            "url": info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
            "video_title": info.get("title"),
            "thumbnail": best_thumbnail(info),
            "meta": guess_from_info(info).to_dict(),
        }

    # ---------- download ----------
    def start_download(self, video_id: str, url: str, meta: dict, thumbnail: str = "") -> str:
        job_id = uuid.uuid4().hex[:8]
        self.jobs[job_id] = {"id": job_id, "title": meta.get("title"), "progress": 0, "stage": "Queued",
                             "done": False, "error": None}
        threading.Thread(target=self._download, args=(job_id, video_id, url, SongMeta.from_dict(meta), thumbnail),
                         daemon=True).start()
        return job_id

    def _download(self, job_id, video_id, url, meta: SongMeta, thumbnail):
        job = self.jobs[job_id]

        def progress(pct, stage):
            job["progress"], job["stage"] = round(pct), stage

        try:
            if not meta.title:
                raise ValueError("Title can't be empty")
            library = self.require_library()
            if self.tool_setup.status["running"]:
                progress(0, "Waiting for first-time setup to finish")
                while self.tool_setup.status["running"]:
                    time.sleep(0.5)
            with tempfile.TemporaryDirectory(prefix="syncify-") as tmp:
                mp3 = self.yt.download_mp3(url, Path(tmp), progress)
                progress(95, "Tagging")
                cover = fetch_cover(thumbnail) if thumbnail else None
                entry = library.add_file(mp3, meta, video_id=video_id, source_url=url, cover=cover)
            job.update(progress=100, stage=f"Saved as {entry['filename']}", done=True)
            # push it to the hub right away so other devices get it next launch
            threading.Thread(target=self.sync_now, daemon=True).start()
        except Exception as e:
            log.exception("download failed")
            job.update(stage="Failed", error=str(e), done=True)

    def redownload(self, entry: dict, workdir: Path) -> Path | None:
        """Used by sync when a song's file isn't on the hub."""
        try:
            mp3 = self.yt.download_mp3(entry["source_url"], workdir)
            info_thumb = f"https://i.ytimg.com/vi/{entry['video_id']}/hqdefault.jpg" if entry.get("video_id") else ""
            write_tags(mp3, SongMeta.from_dict(entry), cover=fetch_cover(info_thumb),
                       song_id=entry["id"], source_url=entry["source_url"])
            return mp3
        except Exception as e:
            log.warning("re-download failed: %s", e)
            return None

    # ---------- sync ----------
    def sync_now(self) -> dict:
        if not self.library:
            return {**self.last_sync, "message": "Pick your music folder to start syncing", "ok": None}
        if not self._sync_lock.acquire(blocking=False):
            return {**self.last_sync, "message": "Sync already running"}
        try:
            hub = self._hub()
            if hub is None:
                self.last_sync = {"at": None, "message": "Sign in with Google to sync", "ok": None}
                return self.last_sync
            report: SyncReport = sync(self.library, hub, self.redownload)
            others = f" · {len(report.devices)} other device(s) on hub" if report.devices else ""
            self.last_sync = {"at": time.time(), "message": report.summary() + others, "ok": not report.failed,
                              "failed": report.failed}
        except Exception as e:
            log.exception("sync failed")
            self.last_sync = {"at": time.time(), "message": f"Sync failed: {e}", "ok": False}
        finally:
            self._sync_lock.release()
        return self.last_sync
