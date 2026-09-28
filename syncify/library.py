"""The library: mp3 files in the Spotify Local Files folder + a manifest describing them.

The manifest lives in the app data folder; the music folder only holds mp3s.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from .config import AppDirs, Settings, write_json_atomic
from .metadata import SongMeta, audio_length, read_tags, write_tags
from .paths import inside, unique_mp3_name

# Fields that describe the song and get shared with other devices.
SHARED_FIELDS = (
    "id", "video_id", "source_url", "title", "artist", "album", "year",
    "filename", "sha256", "added_at", "added_by", "updated_at", "deleted",
)
# Per-device bookkeeping that never leaves this machine.
LOCAL_FIELDS = ("mtime", "size", "duration")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now() -> float:
    return round(time.time(), 3)


class Library:
    def __init__(self, dirs: AppDirs, settings: Settings):
        if not settings.library_path:
            raise ValueError("No music folder chosen yet")
        self.dirs = dirs
        self.root = Path(settings.library_path).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.lock = threading.RLock()
        self.songs: dict[str, dict] = {}
        self.load()

    # ---------- persistence ----------
    def load(self) -> None:
        if self.dirs.manifest.exists():
            data = json.loads(self.dirs.manifest.read_text("utf-8"))
            self.songs = data.get("songs", {})

    def save(self) -> None:
        with self.lock:
            write_json_atomic(self.dirs.manifest, {"version": 1, "songs": self.songs})

    def shared_view(self) -> dict[str, dict]:
        """What this device publishes to the hub."""
        with self.lock:
            return {sid: {k: e.get(k) for k in SHARED_FIELDS} for sid, e in self.songs.items()}

    # ---------- queries ----------
    def visible(self) -> list[dict]:
        out, filled = [], False
        with self.lock:
            for e in self.songs.values():
                if e.get("deleted"):
                    continue
                present = self.path_of(e).exists()
                if present and "duration" not in e:  # songs from before lengths were tracked
                    e["duration"] = audio_length(self.path_of(e))
                    filled = True
                out.append(dict(e, present=present))
            if filled:
                self.save()
        return sorted(out, key=lambda e: e.get("added_at", 0), reverse=True)

    def get(self, song_id: str) -> dict | None:
        return self.songs.get(song_id)

    def path_of(self, entry: dict) -> Path:
        return inside(self.root, entry["filename"])

    def _taken_names(self, exclude_id: str = "") -> set[str]:
        return {e["filename"] for sid, e in self.songs.items() if sid != exclude_id and not e.get("deleted")}

    def _stamp_file(self, entry: dict) -> None:
        p = self.path_of(entry)
        st = p.stat()
        entry["sha256"] = sha256_of(p)
        entry["mtime"], entry["size"] = st.st_mtime, st.st_size
        entry["duration"] = audio_length(p)

    # ---------- mutations ----------
    def add_file(self, src: Path, meta: SongMeta, *, video_id: str = "", source_url: str = "",
                 cover: bytes | None = None) -> dict:
        """Tag a freshly downloaded mp3 and move it into the library."""
        with self.lock:
            song_id = uuid.uuid4().hex[:12]
            write_tags(src, meta, cover=cover, song_id=song_id, source_url=source_url)
            filename = unique_mp3_name(self.root, meta.title, meta.artist, self._taken_names())
            dest = inside(self.root, filename)
            shutil.move(str(src), dest)
            t = now()
            entry = {
                "id": song_id, "video_id": video_id, "source_url": source_url,
                **meta.to_dict(), "filename": filename, "added_at": t, "updated_at": t,
                "added_by": self.settings.device_name, "deleted": False,
            }
            self._stamp_file(entry)
            self.songs[song_id] = entry
            self.save()
            return entry

    def update_meta(self, song_id: str, meta: SongMeta) -> dict:
        with self.lock:
            entry = self.songs[song_id]
            path = self.path_of(entry)
            write_tags(path, meta, song_id=song_id, source_url=entry.get("source_url", ""))
            if meta.title != entry["title"] or meta.artist != entry["artist"]:
                folder = Path(entry["filename"]).parent  # keep songs in their subfolder
                taken = {Path(n).name for n in self._taken_names(song_id) if Path(n).parent == folder}
                new_name = (folder / unique_mp3_name(self.root / folder, meta.title, meta.artist, taken)).as_posix()
                if new_name != entry["filename"]:
                    os.replace(path, inside(self.root, new_name))
                    entry["filename"] = new_name
            entry.update(meta.to_dict())
            entry["updated_at"] = now()
            self._stamp_file(entry)
            self.save()
            return entry

    def delete(self, song_id: str) -> None:
        """Soft delete: the file goes to .syncify/trash and the deletion syncs to other devices."""
        with self.lock:
            entry = self.songs[song_id]
            self._trash_file(entry)
            entry["deleted"] = True
            entry["updated_at"] = now()
            self.save()

    def _trash_file(self, entry: dict) -> None:
        p = self.path_of(entry)
        if p.exists():
            # .bak extension so Spotify doesn't list trashed songs.
            dest = self.dirs.trash / f"{int(time.time())}-{p.name}.bak"
            shutil.move(str(p), dest)

    def refresh_from_disk(self) -> list[str]:
        """Pick up edits made outside Syncify (e.g. retagging in another app).

        Returns ids whose file changed. Missing files are left alone; sync re-pulls them.
        """
        changed = []
        with self.lock:
            for sid, e in self.songs.items():
                if e.get("deleted"):
                    continue
                p = self.path_of(e)
                if not p.exists():
                    continue
                st = p.stat()
                if st.st_mtime == e.get("mtime") and st.st_size == e.get("size"):
                    continue
                digest = sha256_of(p)
                e["mtime"], e["size"] = st.st_mtime, st.st_size
                e["duration"] = audio_length(p)
                if digest != e.get("sha256"):
                    e.update({k: v for k, v in read_tags(p).to_dict().items() if v})
                    e["sha256"] = digest
                    e["updated_at"] = now()
                    changed.append(sid)
            if changed or self.songs:
                self.save()
        return changed

    def scan_folder(self) -> list[str]:
        """Add mp3s that are in the music folder but not in the library yet (songs you had
        before Syncify, or files dropped in by hand). Files are left untouched. Returns new ids."""
        added = []
        with self.lock:
            known = {e["filename"].casefold() for e in self.songs.values() if not e.get("deleted")}
            for p in sorted(self.root.rglob("*.mp3")):
                rel = p.relative_to(self.root)
                if any(part.startswith(".") for part in rel.parts) or not p.is_file():
                    continue
                name = rel.as_posix()
                if name.casefold() in known:
                    continue
                tags = read_tags(p)
                t = now()
                sid = uuid.uuid4().hex[:12]
                entry = {
                    "id": sid, "video_id": "", "source_url": "",
                    "title": tags.title or p.stem, "artist": tags.artist, "album": tags.album, "year": tags.year,
                    "filename": name, "added_at": t, "updated_at": t,
                    "added_by": self.settings.device_name, "deleted": False,
                }
                self._stamp_file(entry)
                self.songs[sid] = entry
                known.add(name.casefold())
                added.append(sid)
            if added:
                self.save()
        return added

    def rekey(self, old_id: str, new_id: str) -> None:
        """Another device added the exact same file under a different id; use theirs."""
        with self.lock:
            e = self.songs.pop(old_id)
            e["id"] = new_id
            self.songs[new_id] = e
            self.save()

    def relocate(self, new_root: Path) -> int:
        """Switch to a different music folder, moving every managed song along. Returns songs moved."""
        new_root = Path(new_root).expanduser().resolve()
        new_root.mkdir(parents=True, exist_ok=True)
        moved = 0
        with self.lock:
            old_root = self.root
            if new_root == old_root:
                return 0
            for e in self.songs.values():
                if e.get("deleted"):
                    continue
                src = inside(old_root, e["filename"])
                if not src.exists():
                    continue
                taken = {x["filename"] for x in self.songs.values() if x is not e and not x.get("deleted")}
                name = e["filename"] if not (new_root / e["filename"]).exists() else \
                    unique_mp3_name(new_root, e["title"], e["artist"], taken)
                (new_root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), new_root / name)
                e["filename"] = name
                moved += 1
            self.root = new_root
            self.settings.library_path = str(new_root)
            self.dirs.save_settings(self.settings)
            for e in self.songs.values():
                if not e.get("deleted") and self.path_of(e).exists():
                    st = self.path_of(e).stat()
                    e["mtime"], e["size"] = st.st_mtime, st.st_size
            self.save()
        return moved

    # ---------- used by sync ----------
    def install_remote(self, entry: dict, src: Path, *, redownloaded: bool = False) -> None:
        """Put a file that came from another device into the library, replacing any older version."""
        with self.lock:
            old = self.songs.get(entry["id"])
            if old and not old.get("deleted") and self.path_of(old).exists():
                self._trash_file(old)
            e = {k: entry.get(k) for k in SHARED_FIELDS}
            wanted = e["filename"]
            if wanted.lower() in {n.lower() for n in self._taken_names(e["id"])} or inside(self.root, wanted).exists():
                e["filename"] = unique_mp3_name(self.root, e["title"], e["artist"], self._taken_names(e["id"]))
            dest = inside(self.root, e["filename"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), dest)
            self.songs[e["id"]] = e
            self._stamp_file(e)
            if redownloaded:
                # A fresh rip never matches the original bytes, so this becomes a new
                # version that we publish (and upload) ourselves.
                e["updated_at"] = now()
            self.save()

    def apply_remote_delete(self, entry: dict) -> None:
        with self.lock:
            old = self.songs.get(entry["id"])
            if old and not old.get("deleted"):
                self._trash_file(old)
            self.songs[entry["id"]] = {**(old or {}), **{k: entry.get(k) for k in SHARED_FIELDS}}
            self.save()
