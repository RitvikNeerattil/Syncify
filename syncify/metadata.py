"""Guess song metadata from a YouTube video and write/read ID3 tags."""
from __future__ import annotations

import io
import re
import urllib.request
from dataclasses import asdict, dataclass

from mutagen.id3 import APIC, COMM, ID3, TALB, TDRC, TIT2, TPE1, TXXX, ID3NoHeaderError


@dataclass
class SongMeta:
    title: str
    artist: str = ""
    album: str = ""
    year: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SongMeta":
        return cls(**{k: str(d.get(k) or "").strip() for k in ("title", "artist", "album", "year")})


# Bracketed junk that is about the upload, not the song.
_NOISE = re.compile(
    r"\s*[\(\[\{]\s*(?:official\s*)?(?:music\s*)?(?:hd|hq|4k|audio|video|lyrics?|lyric video|visuali[sz]er|"
    r"official|clip officiel|explicit|clean|full song|cdq|high quality|remastered audio)\s*[\)\]\}]",
    re.IGNORECASE,
)
_UNRELEASED = re.compile(r"\s*[\(\[\{]\s*(?:unreleased|leak(?:ed)?|unreleased leak)\s*[\)\]\}]", re.IGNORECASE)
_SEPARATORS = (" - ", " – ", " — ", " -- ", " | ")
_CHANNEL_JUNK = re.compile(r"\s*(?:-\s*Topic|VEVO|Official(?: Channel)?|Music)$", re.IGNORECASE)


def guess_from_info(info: dict) -> SongMeta:
    """Best-effort metadata from a yt-dlp info dict. The user gets to edit it before saving."""
    year = str(info.get("release_year") or (info.get("upload_date") or "")[:4] or "")
    album = info.get("album") or ""

    # Official uploads ("Topic" channels, YouTube Music) come with real track data.
    if info.get("track") and (info.get("artist") or info.get("creator")):
        artist = info.get("artist") or info.get("creator")
        return SongMeta(title=info["track"], artist=_first_artists(artist), album=album, year=year)

    raw = info.get("title") or "Untitled"
    if _UNRELEASED.search(raw):
        raw = _UNRELEASED.sub("", raw)
        album = album or "Unreleased"
    raw = _NOISE.sub("", raw).strip()

    artist, title = "", raw
    for sep in _SEPARATORS:
        if sep in raw:
            artist, title = (s.strip() for s in raw.split(sep, 1))
            break
    if not artist:
        artist = _CHANNEL_JUNK.sub("", info.get("channel") or info.get("uploader") or "").strip()
    title = title.strip(" \"'“”")
    return SongMeta(title=title or raw, artist=artist, album=album, year=year)


def _first_artists(artist: str) -> str:
    # yt-dlp joins multiple artists with ", " which is what Spotify expects too.
    return ", ".join(dict.fromkeys(a.strip() for a in artist.split(",") if a.strip()))


def fetch_cover(url: str, size: int = 600) -> bytes | None:
    """Download a thumbnail, crop the 16:9 frame to a centered square, return JPEG bytes."""
    if not url:
        return None
    try:
        from PIL import Image

        with urllib.request.urlopen(url, timeout=15) as r:
            img = Image.open(io.BytesIO(r.read())).convert("RGB")
        w, h = img.size
        side = min(w, h)
        img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
        img = img.resize((size, size))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=90)
        return out.getvalue()
    except Exception:
        return None


def write_tags(path, meta: SongMeta, *, cover: bytes | None = None, song_id: str = "", source_url: str = "") -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    keep_cover = cover is None
    for key in list(tags.keys()):
        if keep_cover and key.startswith("APIC"):
            continue
        tags.delall(key)
    tags.add(TIT2(encoding=3, text=meta.title))
    if meta.artist:
        tags.add(TPE1(encoding=3, text=meta.artist))
    if meta.album:
        tags.add(TALB(encoding=3, text=meta.album))
    if meta.year:
        tags.add(TDRC(encoding=3, text=meta.year))
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
    if song_id:
        tags.add(TXXX(encoding=3, desc="SYNCIFY_ID", text=song_id))
    if source_url:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=source_url))
    # ID3 v2.3 is what Spotify (and Windows Explorer) read most reliably.
    tags.save(path, v2_version=3)


def read_tags(path) -> SongMeta:
    try:
        t = ID3(path)
    except ID3NoHeaderError:
        return SongMeta(title="")

    def g(k):
        return str(t[k].text[0]) if k in t and t[k].text else ""

    return SongMeta(title=g("TIT2"), artist=g("TPE1"), album=g("TALB"), year=g("TDRC")[:4])


def read_cover(path) -> bytes | None:
    try:
        frames = ID3(path).getall("APIC")
        return frames[0].data if frames else None
    except Exception:
        return None
