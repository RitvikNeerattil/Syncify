"""DriveHub against a small in-memory fake of the Drive v3 endpoints it uses."""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from syncify.metadata import SongMeta
from syncify.sync import DriveHub, sync
from test_core import add_song, device

FOLDER = "application/vnd.google-apps.folder"


class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    @property
    def text(self):
        return self._body.decode(errors="replace")

    def json(self):
        return json.loads(self._body)

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]


class FakeDrive:
    def __init__(self):
        self.files: dict[str, dict] = {}
        self.uploads: dict[str, dict] = {}
        self.requests = 0

    def _new(self, meta, content=b""):
        fid = uuid.uuid4().hex[:10]
        self.files[fid] = {"id": fid, "name": meta.get("name"), "mimeType": meta.get("mimeType", ""),
                           "parents": meta.get("parents", []), "appProperties": meta.get("appProperties", {}),
                           "trashed": False, "content": content,
                           "createdTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        return fid

    def _query(self, q):
        out = [f for f in self.files.values() if not f["trashed"]]
        if m := re.search(r"appProperties has \{ key='(\w+)' and value='(\w+)' \}", q):
            out = [f for f in out if f["appProperties"].get(m[1]) == m[2]]
        if m := re.search(r"'(\w+)' in parents", q):
            out = [f for f in out if m[1] in f["parents"]]
        if "mimeType!=" in q:
            out = [f for f in out if f["mimeType"] != FOLDER]
        return [{k: f[k] for k in ("id", "name", "appProperties", "createdTime")} for f in out]

    def request(self, method, url, params=None, json=None, data=None, headers=None, stream=False, timeout=None):
        import json as _json

        self.requests += 1
        params = params or {}
        if url.startswith("resumable://"):
            meta = self.uploads.pop(url)
            body = data.read() if hasattr(data, "read") else data
            return Resp(body=_json.dumps({"id": self._new(meta, body)}).encode())
        path = url.split("googleapis.com", 1)[1]
        if method == "GET" and path == "/drive/v3/files":
            return Resp(body=_json.dumps({"files": self._query(params["q"])}).encode())
        if method == "POST" and path == "/drive/v3/files":
            return Resp(body=_json.dumps({"id": self._new(json)}).encode())
        if m := re.fullmatch(r"/drive/v3/files/(\w+)", path):
            f = self.files[m[1]]
            if method == "GET":
                return Resp(body=f["content"])
            if method == "PATCH":
                f.update(json)
                return Resp(body=b"{}")
        if method == "POST" and path == "/upload/drive/v3/files":
            if params["uploadType"] == "resumable":
                loc = f"resumable://{uuid.uuid4().hex}"
                self.uploads[loc] = json
                return Resp(headers={"Location": loc})
            boundary = headers["Content-Type"].split("boundary=")[1].encode()
            parts = data.split(b"--" + boundary)
            meta = _json.loads(parts[1].split(b"\r\n\r\n", 1)[1].strip())
            content = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
            return Resp(body=_json.dumps({"id": self._new(meta, content)}).encode())
        if (m := re.fullmatch(r"/upload/drive/v3/files/(\w+)", path)) and method == "PATCH":
            self.files[m[1]]["content"] = data
            return Resp(body=b"{}")
        return Resp(404, b"not handled: " + f"{method} {path}".encode())


def test_drive_hub_roundtrip(tmp_path):
    drive = FakeDrive()
    pc, laptop = device(tmp_path, "pc"), device(tmp_path, "laptop")

    e = add_song(pc, tmp_path, "Drive Song")
    r1 = sync(pc, DriveHub(drive))
    assert r1.uploaded == ["Drive Song"]
    # one Syncify folder, one _sync folder, the song named nicely, one catalog
    names = sorted(f["name"] for f in drive.files.values())
    assert names == ["Drive Song.mp3", "Syncify", "_sync", "catalog-%s.json" % pc.settings.device_id]

    r2 = sync(laptop, DriveHub(drive))  # a fresh hub finds the existing folder instead of making another
    assert r2.pulled == ["Drive Song"]
    assert (laptop.root / "Drive Song.mp3").read_bytes() == (pc.root / "Drive Song.mp3").read_bytes()
    assert sum(1 for f in drive.files.values() if f["name"] == "Syncify") == 1

    # edit on laptop -> catalog updated in place, PC picks it up
    laptop.update_meta(e["id"], SongMeta("Drive Song v2", "Artist"))
    sync(laptop, DriveHub(drive))
    assert sum(1 for f in drive.files.values() if f["name"].startswith("catalog-")) == 2
    r3 = sync(pc, DriveHub(drive))
    assert r3.updated == ["Drive Song v2"] and (pc.root / "Drive Song v2.mp3").exists()


def test_drive_gc_only_trashes_old_unreferenced(tmp_path):
    drive = FakeDrive()
    pc = device(tmp_path, "pc")
    e = add_song(pc, tmp_path, "Old")
    sync(pc, DriveHub(drive))
    pc.update_meta(e["id"], SongMeta("New", "Artist"))
    sync(pc, DriveHub(drive))
    mp3s = [f for f in drive.files.values() if f["name"].endswith(".mp3")]
    assert len(mp3s) == 2 and not any(f["trashed"] for f in mp3s)  # too new to clean up yet

    for f in mp3s:  # pretend it's two days later
        f["createdTime"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    sync(pc, DriveHub(drive))
    live = [f["name"] for f in drive.files.values() if f["name"].endswith(".mp3") and not f["trashed"]]
    assert live == ["New.mp3"]
