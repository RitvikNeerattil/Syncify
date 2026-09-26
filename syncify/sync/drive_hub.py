"""Hub stored in the user's Google Drive, in a folder the app creates and manages.

    My Drive/
      Syncify/                      found by an app property, so renaming/moving it is fine
        Song Title.mp3 ...          the songs (tagged with their content hash)
        _sync/
          catalog-<device>.json     each device's song list (one writer per file)

With the drive.file scope the app only ever sees files it created.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .hub import Hub

log = logging.getLogger("syncify.drive")

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
FIELDS = "nextPageToken,files(id,name,appProperties,createdTime)"
GC_GRACE = timedelta(days=1)  # never delete blobs younger than this (another device may be mid-sync)


class DriveError(RuntimeError):
    pass


class DriveHub(Hub):
    def __init__(self, session):
        self.s = session
        self.root_id = self._folder({"syncify": "root"}, "Syncify", "root")
        self.meta_id = self._folder({"syncify": "meta"}, "_sync", self.root_id)
        self._blobs: dict[str, dict] = {}
        self._catalogs: dict[str, dict] = {}
        self.refresh()

    # ---------- http helpers ----------
    def _req(self, method, url, **kw):
        r = self.s.request(method, url, timeout=kw.pop("timeout", 60), **kw)
        if r.status_code >= 400:
            raise DriveError(f"Google Drive {method} failed ({r.status_code}): {r.text[:300]}")
        return r

    def _list(self, q: str) -> list[dict]:
        out, token = [], None
        while True:
            params = {"q": q, "fields": FIELDS, "pageSize": 1000, "spaces": "drive"}
            if token:
                params["pageToken"] = token
            data = self._req("GET", f"{API}/files", params=params).json()
            out += data.get("files", [])
            token = data.get("nextPageToken")
            if not token:
                return out

    def _folder(self, props: dict, name: str, parent: str) -> str:
        k, v = next(iter(props.items()))
        found = self._list(f"appProperties has {{ key='{k}' and value='{v}' }} and trashed=false")
        if found:
            return sorted(found, key=lambda f: f.get("createdTime", ""))[0]["id"]
        meta = {"name": name, "mimeType": FOLDER, "parents": [parent], "appProperties": props}
        return self._req("POST", f"{API}/files", json=meta, params={"fields": "id"}).json()["id"]

    def _multipart(self, method: str, url: str, meta: dict, data: bytes, mime: str) -> dict:
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
            f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--".encode()
        return self._req(method, url, params={"uploadType": "multipart", "fields": "id"}, data=body,
                         headers={"Content-Type": f"multipart/related; boundary={boundary}"}).json()

    # ---------- Hub interface ----------
    def refresh(self) -> None:
        self._blobs = {}
        for f in self._list(f"'{self.root_id}' in parents and trashed=false and mimeType!='{FOLDER}'"):
            sha = (f.get("appProperties") or {}).get("sha")
            if sha:
                self._blobs[sha] = f
        self._catalogs = {}
        for f in self._list(f"'{self.meta_id}' in parents and trashed=false"):
            dev = (f.get("appProperties") or {}).get("device")
            if dev:
                self._catalogs[dev] = f

    def read_catalogs(self) -> dict[str, dict]:
        out = {}
        for dev, f in self._catalogs.items():
            try:
                out[dev] = self._req("GET", f"{API}/files/{f['id']}", params={"alt": "media"}).json()
            except (DriveError, ValueError) as e:
                log.warning("couldn't read catalog %s: %s", dev, e)
        return out

    def write_catalog(self, device_id, device_name, songs):
        data = json.dumps({"device_name": device_name, "songs": songs}, ensure_ascii=False).encode()
        existing = self._catalogs.get(device_id)
        if existing:
            self._req("PATCH", f"{UPLOAD}/files/{existing['id']}", params={"uploadType": "media"}, data=data,
                      headers={"Content-Type": "application/json"})
        else:
            meta = {"name": f"catalog-{device_id}.json", "parents": [self.meta_id],
                    "appProperties": {"device": device_id}, "description": f"Syncify song list for {device_name}"}
            self._catalogs[device_id] = self._multipart("POST", f"{UPLOAD}/files", meta, data, "application/json")

    def has_blob(self, sha):
        return sha in self._blobs

    def put_blob(self, sha, src: Path):
        if self.has_blob(sha):
            return
        meta = {"name": Path(src).name, "parents": [self.root_id], "appProperties": {"sha": sha}}
        start = self._req("POST", f"{UPLOAD}/files", params={"uploadType": "resumable", "fields": "id"},
                          json=meta, headers={"X-Upload-Content-Type": "audio/mpeg"})
        with open(src, "rb") as fh:
            created = self._req("PUT", start.headers["Location"], data=fh, timeout=600,
                                headers={"Content-Type": "audio/mpeg"}).json()
        self._blobs[sha] = {"id": created["id"], "createdTime": datetime.now(timezone.utc).isoformat()}

    def get_blob(self, sha, dest: Path):
        f = self._blobs.get(sha)
        if not f:
            return False
        r = self._req("GET", f"{API}/files/{f['id']}", params={"alt": "media"}, stream=True, timeout=600)
        with open(dest, "wb") as out:
            for chunk in r.iter_content(1 << 20):
                out.write(chunk)
        return True

    def gc(self, live_shas: set[str]) -> int:
        """Move song files no device references any more (old versions, deleted songs) to Drive's trash."""
        cutoff = datetime.now(timezone.utc) - GC_GRACE
        removed = 0
        for sha, f in list(self._blobs.items()):
            if sha in live_shas:
                continue
            created = datetime.fromisoformat(f.get("createdTime", "").replace("Z", "+00:00") or cutoff.isoformat())
            if created > cutoff:
                continue
            try:
                self._req("PATCH", f"{API}/files/{f['id']}", json={"trashed": True})
                del self._blobs[sha]
                removed += 1
            except DriveError as e:
                log.warning("gc failed for %s: %s", sha, e)
        return removed
