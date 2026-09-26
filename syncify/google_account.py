"""'Sign in with Google' for sync.

The OAuth client ID is created once by the developer (see README, "Google
setup") and shipped inside the exe as syncify/oauth_client.json. Users never
see any of that; they just click Sign in and approve in their browser.

Scope is `drive.file`: Syncify can only see files it created itself, never the
rest of the user's Drive.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from .config import AppDirs, Settings

log = logging.getLogger("syncify.google")

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive.file",
]
_KEYRING_SERVICE = "Syncify"
_KEYRING_USER = "google-refresh-token"
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google reorders scopes in its reply

_SUCCESS_PAGE = (
    "<html><body style='font-family:Segoe UI,sans-serif;background:#0f1115;color:#e8eaef;"
    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
    "<div style='text-align:center'><h2>Signed in to Syncify</h2><p>You can close this tab.</p></div></body></html>"
)


def client_config_path() -> Path:
    if os.environ.get("SYNCIFY_OAUTH_CLIENT"):
        return Path(os.environ["SYNCIFY_OAUTH_CLIENT"])
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "syncify" / "oauth_client.json"


class GoogleAccount:
    def __init__(self, dirs: AppDirs, settings: Settings):
        self.dirs = dirs
        self.settings = settings
        self._creds = None

    # ---------- state ----------
    def configured(self) -> bool:
        return client_config_path().exists()

    def signed_in(self) -> bool:
        return bool(self._refresh_token())

    @property
    def email(self) -> str:
        return self.settings.account_email

    # ---------- sign in / out ----------
    def sign_in(self) -> str:
        """Opens the browser for Google's consent screen, blocks until done. Returns the email."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.configured():
            raise RuntimeError("This build has no Google client ID (syncify/oauth_client.json). See README.")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_config_path()), SCOPES)
        creds = flow.run_local_server(
            port=0, open_browser=True, timeout_seconds=300, prompt="consent",
            success_message=_SUCCESS_PAGE, authorization_prompt_message="",
        )
        if not creds.refresh_token:
            raise RuntimeError("Google didn't return a refresh token, try signing in again")
        self._store_refresh_token(creds.refresh_token)
        self._creds = creds
        email = self.session().get("https://www.googleapis.com/oauth2/v3/userinfo").json().get("email", "")
        self.settings.account_email = email
        self.dirs.save_settings(self.settings)
        return email

    def sign_out(self) -> None:
        token = self._refresh_token()
        if token:
            try:
                import requests

                requests.post("https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=10)
            except Exception:
                pass
        self._delete_refresh_token()
        self._creds = None
        self.settings.account_email = ""
        self.dirs.save_settings(self.settings)

    # ---------- use ----------
    def session(self):
        """A requests.Session that adds (and refreshes) the Google access token."""
        from google.auth.transport.requests import AuthorizedSession

        if self._creds is None:
            token = self._refresh_token()
            if not token:
                raise RuntimeError("Not signed in to Google")
            from google.oauth2.credentials import Credentials

            client = _client_info()
            self._creds = Credentials(
                token=None, refresh_token=token, token_uri=client["token_uri"],
                client_id=client["client_id"], client_secret=client.get("client_secret"), scopes=SCOPES,
            )
        return AuthorizedSession(self._creds)

    # ---------- token storage (Windows Credential Manager, file fallback) ----------
    @property
    def _kr_user(self) -> str:
        return f"{_KEYRING_USER}-{self.settings.device_id}"

    @property
    def _token_file(self) -> Path:
        return self.dirs.app / "google_token.json"

    def _refresh_token(self) -> str | None:
        try:
            import keyring

            t = keyring.get_password(_KEYRING_SERVICE, self._kr_user)
            if t:
                return t
        except Exception:
            pass
        if self._token_file.exists():
            return json.loads(self._token_file.read_text("utf-8")).get("refresh_token")
        return None

    def _store_refresh_token(self, token: str) -> None:
        try:
            import keyring

            keyring.set_password(_KEYRING_SERVICE, self._kr_user, token)
            if self._token_file.exists():
                self._token_file.unlink()
            return
        except Exception as e:
            log.info("keyring unavailable (%s), storing token in app data", e)
        self._token_file.write_text(json.dumps({"refresh_token": token}), "utf-8")

    def _delete_refresh_token(self) -> None:
        try:
            import keyring

            keyring.delete_password(_KEYRING_SERVICE, self._kr_user)
        except Exception:
            pass
        if self._token_file.exists():
            self._token_file.unlink()


def _client_info() -> dict:
    data = json.loads(client_config_path().read_text("utf-8"))
    return data.get("installed") or data.get("web") or data
