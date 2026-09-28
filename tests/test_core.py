import shutil
import subprocess
import time
from pathlib import Path

import pytest

from syncify.config import AppDirs
from syncify.library import Library
from syncify.metadata import SongMeta, guess_from_info, read_tags, write_tags
from syncify.paths import OutsideLibraryError, inside, safe_filename, unique_mp3_name
from syncify.sync import FolderHub, sync


# ---------- helpers ----------
def make_mp3(path: Path, seconds: float = 1.0, freq: int = 440) -> Path:
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-codec:a", "libmp3lame", "-b:a", "128k", str(path)],
        check=True,
    )
    return path


def device(tmp_path: Path, name: str) -> Library:
    dirs = AppDirs(tmp_path / name / "appdata")
    s = dirs.load_settings()
    s.device_name = name
    s.library_path = str(tmp_path / name / "Music")
    return Library(dirs, s)


# ---------- paths ----------
def test_safe_filename():
    assert safe_filename('What/If: "Demo"?') == "WhatIf Demo"
    assert safe_filename("CON") == "_CON"
    assert safe_filename("  ...  ") == "Untitled"


def test_unique_names(tmp_path):
    (tmp_path / "Song.mp3").touch()
    assert unique_mp3_name(tmp_path, "Song", "Artist") == "Song (Artist).mp3"
    (tmp_path / "Song (Artist).mp3").touch()
    assert unique_mp3_name(tmp_path, "Song", "Artist") == "Song (2).mp3"


def test_inside_guard(tmp_path):
    assert inside(tmp_path, "a.mp3") == (tmp_path / "a.mp3").resolve()
    with pytest.raises(OutsideLibraryError):
        inside(tmp_path, "../escape.mp3")


# ---------- metadata ----------
KNOWN_ARTISTS = {"Childish Gambino", "Playboi Carti", "Kid Cudi", "Juice WRLD"}


def known(name):
    return name in KNOWN_ARTISTS


@pytest.mark.parametrize("info,expected", [
    # usual "Artist - Song"; both sides are artist names, so keep the order
    ({"title": "Playboi Carti - Kid Cudi (Unreleased) [Official Audio]", "channel": "leaks"},
     SongMeta("Kid Cudi", "Playboi Carti", "", "")),
    # "Song - Artist" gets flipped
    ({"title": "Do Ya Like - Childish Gambino (HQ)", "channel": "randomuploads", "upload_date": "20190304"},
     SongMeta("Do Ya Like", "Childish Gambino", "", "2019")),
    # channel name decides before MusicBrainz is even asked
    ({"title": "Cavalier - Juice WRLD", "channel": "Juice WRLD"}, SongMeta("Cavalier", "Juice WRLD", "", "")),
    ({"title": "Juice WRLD – Cavalier (feat. Someone) (Lyrics)", "channel": "x", "upload_date": "20210304"},
     SongMeta("Cavalier (feat. Someone)", "Juice WRLD", "", "2021")),
    ({"title": "just a title", "channel": "Some Artist - Topic"}, SongMeta("just a title", "Some Artist", "", "")),
    # official track data wins; album is never filled in automatically
    ({"title": "ignored", "track": "Real Track", "artist": "A, B, A", "album": "LP", "release_year": 2019},
     SongMeta("Real Track", "A, B", "", "2019")),
])
def test_guess(info, expected):
    assert guess_from_info(info, is_artist=known) == expected


def test_musicbrainz_parsing(monkeypatch):
    import io
    import json as _json

    from syncify import musicbrainz

    reply = {"artists": [{"score": 100, "name": "Childish Gambino", "sort-name": "Gambino, Childish",
                          "aliases": [{"name": "Donald Glover"}]}]}
    monkeypatch.setattr(musicbrainz.urllib.request, "urlopen",
                        lambda req, timeout: io.BytesIO(_json.dumps(reply).encode()))
    monkeypatch.setattr(musicbrainz, "_cache", {})
    monkeypatch.setattr(musicbrainz.time, "sleep", lambda s: None)
    assert musicbrainz.is_artist("Childish Gambino feat. Someone")
    assert musicbrainz.is_artist("donald glover")
    assert not musicbrainz.is_artist("Do Ya Like")


def test_tag_roundtrip(tmp_path):
    p = make_mp3(tmp_path / "x.mp3")
    write_tags(p, SongMeta("Tïtle", "Artist", "Album", "2024"), cover=b"\xff\xd8fakejpeg", song_id="abc")
    assert read_tags(p) == SongMeta("Tïtle", "Artist", "Album", "2024")


# ---------- library + sync ----------
def add_song(lib: Library, tmp_path: Path, title: str, freq=440) -> dict:
    src = make_mp3(tmp_path / f"dl-{title}.mp3", freq=freq)
    return lib.add_file(src, SongMeta(title, "Artist", "Unreleased", "2025"),
                        video_id=f"vid{freq}", source_url=f"https://youtu.be/vid{freq}")


def test_two_devices_sync_add_edit_delete(tmp_path):
    hub_dir = tmp_path / "OneDrive" / "SyncifyHub"
    hub_dir.mkdir(parents=True)
    hub = FolderHub(hub_dir)
    pc, laptop = device(tmp_path, "pc"), device(tmp_path, "laptop")

    # add on PC -> shows up on laptop
    e = add_song(pc, tmp_path, "First Song")
    assert (pc.root / "First Song.mp3").exists()
    r1 = sync(pc, hub)
    assert r1.uploaded == ["First Song"]
    r2 = sync(laptop, hub)
    assert r2.pulled == ["First Song"]
    assert (laptop.root / "First Song.mp3").read_bytes() == (pc.root / "First Song.mp3").read_bytes()
    assert read_tags(laptop.root / "First Song.mp3").title == "First Song"

    # nothing to do second time
    assert sync(laptop, hub).summary().startswith("Everything")

    # edit on laptop (renames file) -> PC gets new tags + name
    time.sleep(0.01)
    laptop.update_meta(e["id"], SongMeta("First Song (v2)", "Artist", "Unreleased", "2025"))
    sync(laptop, hub)
    r3 = sync(pc, hub)
    assert r3.updated == ["First Song (v2)"]
    assert (pc.root / "First Song (v2).mp3").exists()
    assert not (pc.root / "First Song.mp3").exists()
    assert read_tags(pc.root / "First Song (v2).mp3").title == "First Song (v2)"

    # delete on PC -> removed on laptop (moved to trash, not destroyed)
    time.sleep(0.01)
    pc.delete(e["id"])
    sync(pc, hub)
    r4 = sync(laptop, hub)
    assert r4.deleted == ["First Song (v2)"]
    assert not list(laptop.root.glob("*.mp3"))
    assert list(laptop.dirs.trash.glob("*.bak"))
    assert laptop.visible() == []


def test_redownload_fallback_when_blob_missing(tmp_path):
    hub_dir = tmp_path / "hub"
    hub_dir.mkdir()
    hub = FolderHub(hub_dir)
    pc, laptop = device(tmp_path, "pc"), device(tmp_path, "laptop")
    add_song(pc, tmp_path, "Leak", freq=500)
    sync(pc, hub)
    shutil.rmtree(hub_dir / "files")  # simulate cloud folder not finished syncing
    (hub_dir / "files").mkdir()

    calls = []

    def fake_redownload(entry, workdir):
        calls.append(entry["source_url"])
        p = make_mp3(workdir / "r.mp3", freq=500)
        write_tags(p, SongMeta.from_dict(entry), song_id=entry["id"])
        return p

    r = sync(laptop, hub, fake_redownload)
    assert r.redownloaded == ["Leak"] and calls == ["https://youtu.be/vid500"]
    assert (laptop.root / "Leak.mp3").exists()
    assert r.uploaded == ["Leak"]  # laptop's copy now seeds the hub


def test_external_retag_is_picked_up(tmp_path):
    lib = device(tmp_path, "pc")
    e = add_song(lib, tmp_path, "Old Name")
    time.sleep(0.02)
    write_tags(lib.root / "Old Name.mp3", SongMeta("Fixed In Mp3tag", "Artist"))
    assert lib.refresh_from_disk() == [e["id"]]
    assert lib.get(e["id"])["title"] == "Fixed In Mp3tag"


def test_filename_collision_on_receiving_device(tmp_path):
    hub_dir = tmp_path / "hub"
    hub_dir.mkdir()
    hub = FolderHub(hub_dir)
    pc, laptop = device(tmp_path, "pc"), device(tmp_path, "laptop")
    make_mp3(laptop.root / "Same.mp3", freq=300)  # a file the user put there by hand
    add_song(pc, tmp_path, "Same", freq=600)
    sync(pc, hub)
    sync(laptop, hub)
    assert (laptop.root / "Same.mp3").exists() and (laptop.root / "Same (Artist).mp3").exists()


def test_relocate_moves_songs(tmp_path):
    lib = device(tmp_path, "pc")
    add_song(lib, tmp_path, "Mover")
    new = tmp_path / "NewMusic"
    assert lib.relocate(new) == 1
    assert (new / "Mover.mp3").exists() and lib.settings.library_path == str(new.resolve())
    assert lib.refresh_from_disk() == []  # nothing looks edited after the move


def test_existing_mp3s_are_adopted(tmp_path):
    lib = device(tmp_path, "pc")
    make_mp3(lib.root / "Old Song.mp3")
    (lib.root / "sub").mkdir()
    p = make_mp3(lib.root / "sub" / "Tagged.mp3", freq=500)
    write_tags(p, SongMeta("Real Title", "Real Artist"))
    before = p.read_bytes()
    added = lib.scan_folder()
    assert len(added) == 2
    titles = sorted((e["title"], e["filename"]) for e in lib.visible())
    assert titles == [("Old Song", "Old Song.mp3"), ("Real Title", "sub/Tagged.mp3")]
    assert p.read_bytes() == before  # adopting never modifies the file
    assert lib.scan_folder() == []  # second scan finds nothing new


def test_adopted_songs_sync_and_dedupe(tmp_path):
    hub_dir = tmp_path / "hub"
    hub_dir.mkdir()
    hub = FolderHub(hub_dir)
    pc, laptop = device(tmp_path, "pc"), device(tmp_path, "laptop")
    # both computers already had the same file before Syncify, plus one only the PC had
    make_mp3(pc.root / "Shared.mp3", freq=321)
    shutil.copy(pc.root / "Shared.mp3", laptop.root / "Shared.mp3")
    (laptop.root / "sub").mkdir()
    make_mp3(laptop.root / "sub" / "Laptop Only.mp3", freq=654)

    sync(pc, hub)
    sync(laptop, hub)
    sync(pc, hub)
    sync(laptop, hub)
    for lib in (pc, laptop):
        names = sorted(p.relative_to(lib.root).as_posix() for p in lib.root.rglob("*.mp3"))
        assert names == ["Shared.mp3", "sub/Laptop Only.mp3"]
        assert len(lib.visible()) == 2
    assert {e["id"] for e in pc.visible()} == {e["id"] for e in laptop.visible()}


def test_edit_keeps_subfolder(tmp_path):
    lib = device(tmp_path, "pc")
    (lib.root / "sub").mkdir()
    make_mp3(lib.root / "sub" / "a.mp3")
    [sid] = lib.scan_folder()
    lib.update_meta(sid, SongMeta("Renamed", "X"))
    assert lib.get(sid)["filename"] == "sub/Renamed.mp3" and (lib.root / "sub" / "Renamed.mp3").exists()


def test_device_list_and_remote_removal(tmp_path):
    from syncify.sync.engine import DeviceRemoved

    hub_dir = tmp_path / "hub"
    hub_dir.mkdir()
    hub = FolderHub(hub_dir)
    pc, laptop = device(tmp_path, "pc"), device(tmp_path, "laptop")
    add_song(pc, tmp_path, "One")
    sync(pc, hub, platform="Windows 11")
    r = sync(laptop, hub)
    names = {(d["name"], d["songs"], d["this_device"]) for d in r.device_list}
    assert names == {("laptop", 1, True), ("pc", 1, False)}
    assert all(d["synced_at"] for d in r.device_list)

    # laptop removes the PC
    hub.remove_device(pc.settings.device_id, by="laptop")
    assert [d["name"] for d in sync(laptop, hub).device_list] == ["laptop"]
    with pytest.raises(DeviceRemoved) as e:
        sync(pc, hub)
    assert e.value.by == "laptop"
    assert (pc.root / "One.mp3").exists()  # files untouched

    # PC signs in again -> back on the list
    hub.unremove_device(pc.settings.device_id)
    sync(pc, hub)
    assert {d["name"] for d in sync(laptop, hub).device_list} == {"laptop", "pc"}


def test_notifications(tmp_path, monkeypatch):
    from syncify import app as app_mod, notify

    assert notify.page_from_argv(["--x", "syncify://settings/"]) == "settings"
    assert notify.page_from_argv([]) == ""

    shown = []
    monkeypatch.setattr(notify, "show", lambda title, body, page="": shown.append((title, body, page)))

    class Fake:
        removed_notice = ""
        library = None

    f = Fake()
    app_mod._notify_result(f, {"ok": True, "message": "Everything's already in sync"})
    app_mod._notify_result(f, {"ok": False, "message": "Sync failed: boom"})
    app_mod._notify_result(f, {"ok": None, "message": "Sign in with Google to sync"})
    f.removed_notice = "removed by laptop"
    app_mod._notify_result(f, {"ok": False, "message": "x"})
    assert [s[0] for s in shown] == ["Sync worked", "Sync failed", "Signed out of Syncify"]
    assert shown[1][2] == "settings"


def test_google_redirect_page():
    import threading
    import urllib.request

    from syncify.google_account import catch_redirect

    seen = {}

    def fake_browser(redirect_uri):
        def visit():
            with urllib.request.urlopen(redirect_uri + "?state=abc&code=4/xyz&scope=email") as r:
                seen["type"] = r.headers["Content-Type"]
                seen["body"] = r.read().decode()
        threading.Thread(target=visit).start()

    uri = catch_redirect(fake_browser, timeout=10)
    assert "code=4/xyz" in uri and uri.startswith("http://127.0.0.1:")
    assert seen["type"].startswith("text/html") and "Signed in to Syncify" in seen["body"]


def test_google_redirect_cancelled():
    import threading
    import urllib.request

    from syncify.google_account import catch_redirect

    def deny(redirect_uri):
        threading.Thread(target=lambda: urllib.request.urlopen(redirect_uri + "?error=access_denied").read()).start()

    with pytest.raises(RuntimeError, match="cancelled"):
        catch_redirect(deny, timeout=10)


def test_length_and_size_tracked(tmp_path):
    lib = device(tmp_path, "pc")
    src = make_mp3(tmp_path / "x.mp3", seconds=3)
    e = lib.add_file(src, SongMeta("Three Secs", "A"))
    assert 2.5 < e["duration"] < 3.5 and e["size"] > 0
    del lib.songs[e["id"]]["duration"]  # older library.json without lengths
    assert 2.5 < lib.visible()[0]["duration"] < 3.5
    assert "duration" not in lib.shared_view()[e["id"]]  # local-only, not synced
