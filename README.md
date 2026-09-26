# Syncify

Get songs that aren't on Spotify (unreleased tracks, leaks, live versions) into Spotify on all your computers.

Search YouTube inside the app, preview the upload, fix the tags, hit **Download**. Syncify rips the best audio to a 320k mp3, tags it (title, artist, album, year, cover art) and drops it in your music folder, which Spotify shows under Local Files. Sign in with Google on your other computers and the song shows up there too.

## Using it

1. Download `Syncify.exe`, put it anywhere, double-click.
2. **Sign in with Google.**
3. **Pick your music folder** (defaults to `Music\Syncify`).
4. In Spotify: Settings → Your Library → **Show Local Files** → **Add a source** → that folder.

That's it. On first launch Syncify also downloads ffmpeg and Deno (about 130 MB, once) in the background, since yt-dlp needs them.

## How sync works

```
 PC                              Google Drive                          Laptop
┌───────────────────┐      ┌────────────────────────────┐      ┌───────────────────┐
│ Music\Syncify\    │ ───▶ │ Syncify/                   │ ───▶ │ Music\Syncify\    │
│   Song.mp3        │      │   Song.mp3                 │      │   Song.mp3        │
│                   │ ◀─── │   _sync/catalog-<pc>.json  │ ◀─── │                   │
└───────────────────┘      │   _sync/catalog-<laptop>…  │      └───────────────────┘
                           └────────────────────────────┘
```

- Syncify makes a `Syncify` folder in your Drive and only ever sees files it created (`drive.file` permission).
- Each computer writes its own song list to `_sync/`. For every song, the newest change wins. Adds, tag edits (including renames) and deletes all sync. Deleted songs go to a trash folder in app data instead of being wiped.
- If a song's file isn't in Drive yet, the other computer re-rips it from the saved YouTube link.
- Old versions of edited or deleted songs are moved to Drive's trash after a day.
- Syncs on launch, every 10 minutes while open, right after each download, and at Windows login (quietly, no window; on by default, toggle in Settings).
- Your PC can be off. The laptop pulls straight from Drive.

App data (settings, song list, log, trash) lives in `%APPDATA%\Syncify`. The Google login is kept in Windows Credential Manager.

## Building the exe

### One-time Google setup (developer only)

Users never do this. You do it once, and the ID gets baked into the exe.

1. Go to [console.cloud.google.com](https://console.cloud.google.com), create a project called Syncify.
2. **APIs & Services → Library →** enable **Google Drive API**.
3. **APIs & Services → OAuth consent screen (Google Auth Platform):** app name Syncify, your email as support/developer contact, audience **External**. Scopes: `drive.file`, `userinfo.email`, `openid`.
4. **Publish the app** (Audience → *Publish app*). In "Testing" mode Google logs everyone out every 7 days. With only these scopes, publishing shouldn't need a Google review.
5. **Credentials → Create credentials → OAuth client ID → Desktop app.** Download the JSON and save it as `syncify\oauth_client.json`.

For a desktop app this client "secret" isn't really secret (Google expects it to ship inside the app), but it's gitignored anyway.

### Build

Needs Python 3.12 or 3.13 (pywebview's Windows backend lags behind the newest Python). Then double-click `build.bat`. You get `dist\Syncify.exe`.

## Developing

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python run.py --debug
pytest
```

Test two "computers" on one machine without Google by giving each its own app data folder and a shared local folder as the sync hub:
```
:: terminal 1
set SYNCIFY_FOLDER_HUB=C:\temp\hub
set SYNCIFY_HOME=C:\temp\pc
python run.py

:: terminal 2
set SYNCIFY_FOLDER_HUB=C:\temp\hub
set SYNCIFY_HOME=C:\temp\laptop
python run.py
```
Drop `SYNCIFY_FOLDER_HUB` to test the same thing through Google Drive.

The tests cover filename rules, tag guessing and writing, full two-device sync (add, edit/rename, delete, re-rip fallback, name clashes, moving the music folder), and the Google Drive hub against a fake Drive.

## Layout

```
run.py                   entry point
syncify/
  app.py                 window, --sync-only, single-instance lock
  api.py                 methods the UI calls
  core.py                ties everything together, download jobs
  config.py              app data folder + settings
  google_account.py      Google sign-in, token storage
  library.py             mp3 files + manifest
  metadata.py            tag guessing, ID3 read/write, cover art
  youtube.py             yt-dlp search + mp3 rip
  tools_setup.py         first-run ffmpeg/Deno download
  paths.py               safe filenames, stay-inside-the-folder guard
  startup.py             Windows login sync
  instance.py            one Syncify at a time
  sync/engine.py         the sync algorithm
  sync/drive_hub.py      Google Drive storage
  sync/hub.py            Hub interface + FolderHub (local testing)
ui/                      HTML/CSS/JS front end (runs in pywebview)
assets/                  logo (SVG), app icon (.ico/.png)
docs/                    homepage, privacy policy, terms (GitHub Pages)
build.bat                builds dist\Syncify.exe
```

Built exes go in GitHub Releases, not in the repo (`build/`, `dist/` and `*.spec` are gitignored).

## Roadmap

- **Android:** Spotify on Android plays files from phone storage ("Show audio files from this device"). An Android app can sign into the same Google account and pull from the same Drive folder, using the same song-list format.
- Cover art in the Library view from the actual file
- Playlists

## Note

For your own listening. Downloading from YouTube is against YouTube's terms, so don't share the ripped files.
