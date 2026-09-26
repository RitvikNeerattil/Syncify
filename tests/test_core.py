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
@pytest.mark.parametrize("info,expected", [
    ({"title": "Playboi Carti - Kid Cudi (Unreleased) [Official Audio]", "channel": "leaks"},
     SongMeta("Kid Cudi", "Playboi Carti", "Unreleased", "")),
    ({"title": "Juice WRLD – Cavalier (feat. Someone) (Lyrics)", "channel": "x", "upload_date": "20210304"},
     SongMeta("Cavalier (feat. Someone)", "Juice WRLD", "", "2021")),
    ({"title": "just a title", "channel": "Some Artist - Topic"},
     SongMeta("just a title", "Some Artist", "", "")),
    ({"title": "ignored", "track": "Real Track", "artist": "A, B, A", "album": "LP", "release_year": 2019},
     SongMeta("Real Track", "A, B", "LP", "2019")),
])
def test_guess(info, expected):
    assert guess_from_info(info) == expected


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
