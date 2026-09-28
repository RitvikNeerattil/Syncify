"""Tiny MusicBrainz lookup: "is this string the name of a real artist?"

Used to tell which side of "A - B" is the artist when an upload is titled
"Song - Artist" instead of the usual "Artist - Song". Free API, no key; they ask
for a descriptive User-Agent and at most ~1 request per second.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request

from . import __version__

_UA = f"Syncify/{__version__} ( https://github.com/RitvikNeerattil/Syncify )"
_lock = threading.Lock()
_last = [0.0]


def _norm(s: str) -> str:
    return re.sub(r"[^\w]+", " ", s.casefold()).strip()


def primary_artist(s: str) -> str:
    """'A feat. B', 'A & B', 'A x B', 'A, B' -> 'A'."""
    return re.split(r"\s+(?:feat\.?|ft\.?|featuring|x|&|and|with)\s+|,\s*", s, maxsplit=1, flags=re.I)[0].strip()


_cache: dict[str, bool] = {}


def is_artist(name: str, timeout: float = 4.0) -> bool:
    """True if MusicBrainz knows an artist with exactly this name (or alias). False on any error."""
    name = primary_artist(name)
    if len(name) < 2:
        return False
    key = _norm(name)
    if key in _cache:
        return _cache[key]
    q = urllib.parse.urlencode({"query": f'artist:"{name}"', "fmt": "json", "limit": 5})
    try:
        with _lock:  # be polite: max ~1 request/second
            wait = 1.0 - (time.monotonic() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.monotonic()
        req = urllib.request.Request(f"https://musicbrainz.org/ws/2/artist?{q}", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception:
        return False
    found = False
    for a in data.get("artists", []):
        if int(a.get("score") or 0) < 90:
            continue
        names = [a.get("name", ""), a.get("sort-name", "")] + [x.get("name", "") for x in a.get("aliases") or []]
        if any(_norm(n) == key for n in names):
            found = True
            break
    _cache[key] = found  # only successful lookups are cached
    return found
