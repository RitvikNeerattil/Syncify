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
from .sync.engine import DeviceRemoved
from .tools_setup import ToolSetup
from .youtube import YouTube, best_thumbnail, tool_status

log = logging.getLogger("syncify")


class Syncify:
    def __init__(self, dirs: AppDirs | None = None):
        self.dirs = dirs or AppDirs()
        self.settings = self.dirs.load_settings()
        self.account = GoogleAccount(self.dirs, self.settings)
        self.library: Library | None = Library(self.dirs, self.settings) if self.settings.library_path else None
        if self.library:
            self.library.scan_folder()
        self.yt = YouTube(self.dirs.bin)
        self.tool_setup = ToolSetup(self.dirs.bin)
        self.jobs: dict[str, dict] = {}
        self._sync_lock = threading.Lock()
        self.last_sync: dict = {"at": None, "message": "Not synced yet", "ok": None}
        self.sync_progress: str = ""  # non-empty while a sync is running
        self.devices: list[dict] = []  # from the last sync
        self.removed_notice: str = ""
        self.open_page: str = ""  # page to show first (set when opened from a notification)

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
            moved = self.library.relocate(Path(path))
            self.library.scan_folder()  # the new folder may already have songs in it
            return moved
        self.settings.library_path = str(Path(path).expanduser().resolve())
        self.dirs.save_settings(self.settings)
        self.library = Library(self.dirs, self.settings)
        self.library.scan_folder()
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
            self.sync_progress = "Starting"
            hub = self._hub()
            if hub is None:
                self.last_sync = {"at": None, "message": "Sign in with Google to sync", "ok": None}
                return self.last_sync

            def progress(msg):
                self.sync_progress = msg

            report: SyncReport = sync(self.library, hub, self.redownload, progress, platform=_platform())
            self.devices = report.device_list
            self.last_sync = {"at": time.time(), "message": report.summary(), "ok": not report.failed,
                              "failed": report.failed}
        except DeviceRemoved as e:
            log.info("this device was removed by %s", e.by)
            try:
                hub.delete_catalog(self.settings.device_id)
            except Exception:
                pass
            self.account.sign_out()
            self.devices = []
            self.removed_notice = f"This computer was removed from Syncify by {e.by}. Sign in again to keep syncing."
            self.last_sync = {"at": None, "message": self.removed_notice, "ok": False}
        except Exception as e:
            log.exception("sync failed")
            self.last_sync = {"at": time.time(), "message": f"Sync failed: {e}", "ok": False}
        finally:
            self.sync_progress = ""
            self._sync_lock.release()
        return self.last_sync

    # ---------- account + devices ----------
    def sign_in(self) -> str:
        email = self.account.sign_in()
        self.removed_notice = ""
        try:  # if another device had removed this one, signing in brings it back
            DriveHub(self.account.session()).unremove_device(self.settings.device_id)
        except Exception:
            log.exception("couldn't clear removed flag")
        return email

    def sign_out(self) -> None:
        """Sign this computer out. It drops off the device list on your other computers; songs stay."""
        try:
            hub = self._hub()
            if hub:
                hub.delete_catalog(self.settings.device_id)
        except Exception:
            log.exception("couldn't remove this device's song list from Drive")
        self.account.sign_out()
        self.devices = []

    def remove_device(self, device_id: str) -> None:
        if device_id == self.settings.device_id:
            raise ValueError("To remove this computer, use Sign out")
        hub = self._hub()
        if hub is None:
            raise RuntimeError("Sign in with Google first")
        hub.remove_device(device_id, by=self.settings.device_name)
        self.devices = [d for d in self.devices if d["id"] != device_id]


def _platform() -> str:
    import platform

    return f"{platform.system()} {platform.release()}".strip()
