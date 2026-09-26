"""Methods the UI calls through pywebview (`window.pywebview.api.<name>(...)`).

pywebview runs each call on its own thread and hands the return value to JS as
a Promise. Everything returns {"ok": bool, "data"|"error": ...}.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from . import startup
from .core import Syncify
from .metadata import SongMeta


def _safe(fn):
    def wrapper(*a, **kw):
        try:
            return {"ok": True, "data": fn(*a, **kw)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    wrapper.__name__ = fn.__name__
    return wrapper


def _default_music_folder() -> str:
    return str(Path.home() / "Music" / "Syncify")


class Api:
    def __init__(self, app: Syncify):
        self._app = app
        self._window = None  # set by app.py; underscore keeps pywebview from exposing it

    def _attach(self, window):
        self._window = window

    # ---------- state ----------
    @_safe
    def state(self):
        a = self._app
        return {
            "library_root": a.settings.library_path,
            "suggested_folder": _default_music_folder(),
            "device_name": a.settings.device_name,
            "account": {"configured": a.account.configured(), "signed_in": a.account.signed_in(),
                        "email": a.account.email},
            "songs": a.library.visible() if a.library else [],
            "tools": a.tools(),
            "setup": {**a.tool_setup.status, "missing": a.tool_setup.missing()},
            "last_sync": a.last_sync,
            "startup": {"supported": startup.supported(), "enabled": startup.is_enabled()},
        }

    # ---------- account ----------
    @_safe
    def sign_in(self):
        email = self._app.account.sign_in()
        threading.Thread(target=self._app.sync_now, daemon=True).start()
        return email

    @_safe
    def sign_out(self):
        self._app.account.sign_out()

    # ---------- music folder ----------
    @_safe
    def choose_music_folder(self):
        """Folder picker. Returns the chosen path (or None if cancelled) without applying it."""
        import webview

        picked = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not picked:
            return None
        return picked[0] if isinstance(picked, (list, tuple)) else picked

    @_safe
    def set_music_folder(self, path: str):
        if not path:
            raise ValueError("No folder given")
        moved = self._app.set_library_path(path)
        threading.Thread(target=self._app.sync_now, daemon=True).start()
        return {"path": self._app.settings.library_path, "moved": moved}

    @_safe
    def open_folder(self):
        root = self._app.settings.library_path
        if not root:
            return
        if sys.platform == "win32":
            os.startfile(root)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", root])
        else:
            subprocess.Popen(["xdg-open", root])

    # ---------- search + download ----------
    @_safe
    def search(self, query: str):
        return self._app.search(query)

    @_safe
    def prepare(self, video_id: str):
        return self._app.prepare(video_id)

    @_safe
    def download(self, video_id: str, url: str, meta: dict, thumbnail: str = ""):
        self._app.require_library()
        return self._app.start_download(video_id, url, meta, thumbnail)

    @_safe
    def jobs(self):
        return list(self._app.jobs.values())

    @_safe
    def clear_finished_jobs(self):
        for jid in [j for j, v in self._app.jobs.items() if v["done"]]:
            del self._app.jobs[jid]

    # ---------- library ----------
    @_safe
    def update_song(self, song_id: str, meta: dict):
        return self._app.require_library().update_meta(song_id, SongMeta.from_dict(meta))

    @_safe
    def delete_song(self, song_id: str):
        self._app.require_library().delete(song_id)

    # ---------- sync + settings ----------
    @_safe
    def sync_now(self):
        return self._app.sync_now()

    @_safe
    def set_device_name(self, name: str):
        self._app.settings.device_name = name.strip() or self._app.settings.device_name
        self._app.dirs.save_settings(self._app.settings)

    @_safe
    def retry_setup(self):
        self._app.tool_setup.start()

    @_safe
    def set_startup(self, enabled: bool):
        return startup.set_enabled(enabled)
