"""Entry point: opens the window, or runs a headless sync with --sync-only."""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from . import instance, startup
from .config import AppDirs
from .core import Syncify


def _ui_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "ui"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="syncify")
    p.add_argument("--sync-only", action="store_true", help="sync and exit, no window (used at Windows login)")
    p.add_argument("--debug", action="store_true", help="enable devtools + verbose logs")
    args = p.parse_args(argv)

    dirs = AppDirs()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(dirs.log_file, encoding="utf-8")],
    )
    lock = instance.acquire(dirs.app / "instance.lock")
    if lock is None:
        # Already running. The open window does its own syncing, so there's nothing to do.
        logging.info("another Syncify is running, exiting")
        if not args.sync_only and sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, "Syncify is already open (check your taskbar).", "Syncify", 0x40)
        return 0

    app = Syncify(dirs)

    if args.sync_only:
        result = app.sync_now()
        logging.info("login sync: %s", result["message"])
        return 0 if result.get("ok") is not False else 1

    # Keep the login-sync entry pointing at wherever the exe lives now; turn it on the first time.
    if startup.supported():
        try:
            if not app.settings.startup_default_applied:
                startup.set_enabled(True)
                app.settings.startup_default_applied = True
                app.dirs.save_settings(app.settings)
            elif startup.is_enabled():
                startup.set_enabled(True)
        except OSError:
            logging.exception("couldn't update login sync entry")

    app.tool_setup.start()  # fetch ffmpeg/Deno in the background on first launch

    import webview  # imported late so --sync-only works without a GUI

    from .api import Api

    api = Api(app)
    window = webview.create_window(
        "Syncify", str(_ui_dir() / "index.html"), js_api=api,
        width=1180, height=780, min_size=(860, 560), background_color="#0f1115",
    )
    api._attach(window)

    def background_sync():
        stop = threading.Event()
        window.events.closed += stop.set
        app.sync_now()  # pull anything new from other devices on launch
        while not stop.wait(max(1, app.settings.auto_sync_minutes) * 60):
            app.sync_now()

    # http_server=True serves the UI from http://127.0.0.1 so YouTube preview embeds work.
    webview.start(background_sync, http_server=True, debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
