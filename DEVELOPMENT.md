# Developing Syncify

Notes for building and working on Syncify. The [README](README.md) is the user-facing page.

## Building from source

Requires **Python 3.12 or 3.13** (pywebview's Windows backend lags behind the newest Python).

```bat
git clone https://github.com/RitvikNeerattil/Syncify
cd Syncify
build.bat
```

`build.bat` sets up a virtual environment, installs dependencies and produces `dist\Syncify.exe`.

<details>
<summary><b>One-time Google setup (to enable sign-in in your own build)</b></summary>

1. In the [Google Cloud Console](https://console.cloud.google.com), create a project and enable the **Google Drive API**.
2. Set up the OAuth consent screen (Google Auth Platform): audience **External**, scopes `drive.file`, `userinfo.email`, `openid`, plus homepage and privacy policy links. Publish it so tokens don't expire every 7 days.
3. Create an OAuth client of type **Desktop app**, download the JSON and save it as `syncify/oauth_client.json`. It's gitignored and baked into the exe at build time.

Without it the app still builds and works, just without sign-in and sync.
</details>

### Developing

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python run.py --debug
pytest
```

To try sync between two instances on one machine without Google, give each its own app data folder and a shared local hub:

```bat
:: terminal 1
set SYNCIFY_FOLDER_HUB=C:\temp\hub
set SYNCIFY_HOME=C:\temp\pc
python run.py

:: terminal 2
set SYNCIFY_FOLDER_HUB=C:\temp\hub
set SYNCIFY_HOME=C:\temp\laptop
python run.py
```

| Flag | Does |
|---|---|
| `--debug` | Enables devtools and verbose logging |
| `--sync-only` | Syncs and exits without a window (used at Windows login) |

## Project layout

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
  musicbrainz.py         "is this an artist name?" lookup for Song - Artist titles
  youtube.py             yt-dlp search + mp3 rip
  tools_setup.py         first-run ffmpeg/Deno download
  paths.py               safe filenames, stay-inside-the-folder guard
  startup.py             Windows login sync
  instance.py            one Syncify at a time
  sync/engine.py         the sync algorithm
  sync/drive_hub.py      Google Drive storage
  sync/hub.py            Hub interface + FolderHub (local testing)
ui/                      HTML/CSS/JS front end (runs in pywebview)
assets/                  logo (SVG) and app icon
docs/                    website, privacy policy, terms (GitHub Pages)
tests/                   pytest suite
```
