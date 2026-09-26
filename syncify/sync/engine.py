"""Two-way sync between this device's library and the hub.

Rules:
- Every song has an id. For each id, the entry with the newest `updated_at`
  (from any device) wins. That covers adds, metadata edits and deletes.
- If the winning version's file isn't here, pull it from the hub by hash.
  If the hub doesn't have it either (e.g. the other device hasn't finished
  uploading), fall back to re-ripping it from the saved YouTube link.
- Any local file the hub is missing gets uploaded.
- Finally this device publishes its catalog so the others see it.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..library import Library, sha256_of
from .hub import Hub

log = logging.getLogger("syncify.sync")

# (entry, workdir) -> path to a tagged mp3, or None if it couldn't be fetched
Redownloader = Callable[[dict, Path], "Path | None"]


@dataclass
class SyncReport:
    pulled: list[str] = field(default_factory=list)
    redownloaded: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(v)} {k}" for k, v in (
            ("new/updated from other devices", self.pulled + self.updated),
            ("re-ripped from YouTube", self.redownloaded),
            ("removed", self.deleted),
            ("uploaded", self.uploaded),
            ("failed", self.failed),
        ) if v]
        return ", ".join(parts) if parts else "Everything's already in sync"


def _newer(a: dict | None, b: dict | None) -> bool:
    """True if b should replace a."""
    if b is None:
        return False
    if a is None:
        return True
    ka = (a.get("updated_at") or 0, bool(a.get("deleted")), a.get("sha256") or "")
    kb = (b.get("updated_at") or 0, bool(b.get("deleted")), b.get("sha256") or "")
    return kb > ka  # ties break deterministically so every device agrees


def sync(library: Library, hub: Hub, redownload: Redownloader | None = None) -> SyncReport:
    report = SyncReport()
    library.refresh_from_disk()
    if hasattr(hub, "refresh"):
        hub.refresh()
    me = library.settings.device_id

    # 1. Newest version of every song across all other devices.
    remote: dict[str, dict] = {}
    for device_id, cat in hub.read_catalogs().items():
        if device_id == me:
            continue
        report.devices.append(cat.get("device_name") or device_id)
        for sid, entry in (cat.get("songs") or {}).items():
            if _newer(remote.get(sid), entry):
                remote[sid] = entry

    # 2. Bring this device up to date.
    for sid, theirs in remote.items():
        mine = library.get(sid)
        if not _newer(mine, theirs):
            continue
        name = theirs.get("title") or sid
        try:
            if theirs.get("deleted"):
                library.apply_remote_delete(theirs)
                report.deleted.append(name)
                continue
            if mine and not mine.get("deleted") and mine.get("sha256") == theirs.get("sha256"):
                # same bytes, only bookkeeping differs
                with library.lock:
                    mine.update({k: theirs[k] for k in ("updated_at", "added_by") if k in theirs})
                    library.save()
                continue
            with tempfile.TemporaryDirectory(prefix="syncify-") as tmp:
                tmp = Path(tmp)
                dest = tmp / "incoming.mp3"
                if hub.get_blob(theirs["sha256"], dest) and sha256_of(dest) == theirs["sha256"]:
                    library.install_remote(theirs, dest)
                    (report.updated if mine and not mine.get("deleted") else report.pulled).append(name)
                elif redownload and theirs.get("source_url"):
                    ripped = redownload(theirs, tmp)
                    if not ripped:
                        raise RuntimeError("re-download failed")
                    library.install_remote(theirs, ripped, redownloaded=True)
                    report.redownloaded.append(name)
                else:
                    raise RuntimeError("file not on the hub yet")
        except Exception as e:  # one bad song shouldn't stop the rest
            log.warning("sync failed for %s: %s", name, e)
            report.failed.append(f"{name} ({e})")

    # 3. Upload anything the hub is missing.
    for entry in list(library.songs.values()):
        if entry.get("deleted") or not entry.get("sha256"):
            continue
        path = library.path_of(entry)
        if path.exists() and not hub.has_blob(entry["sha256"]):
            try:
                hub.put_blob(entry["sha256"], path)
                report.uploaded.append(entry.get("title") or entry["id"])
            except OSError as e:
                report.failed.append(f"{entry.get('title')} upload ({e})")

    # 4. Publish.
    mine = library.shared_view()
    hub.write_catalog(me, library.settings.device_name, mine)

    # 5. Clean up song files nobody points at any more (hubs that support it).
    if hasattr(hub, "gc"):
        live = {e.get("sha256") for e in [*remote.values(), *mine.values()] if not e.get("deleted")}
        try:
            hub.gc(live)
        except Exception as e:
            log.warning("cleanup failed: %s", e)
    return report
