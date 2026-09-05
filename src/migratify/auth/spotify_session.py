"""Spotify access via a normal web-player sign-in -- no registered app.

Spotify's Web API needs a bearer token, and the official way to get one means
registering an application in the developer dashboard. That is a real cost
paid by every user for something they should not have to think about, so it is
the fallback here, not the default.

The default is simpler: you sign in to ``open.spotify.com`` the way you always
do, and the web player mints a token for itself. We take that token.

**We deliberately do not reimplement the token handshake.** Spotify guards its
``/api/token`` endpoint with a rotating TOTP scheme that changes without
notice; every project that reimplements it breaks on Spotify's schedule.
Instead we let the real web player perform the handshake inside a headless
page and observe the response. When Spotify changes the scheme, their own
player adapts and this keeps working.

Tokens last about an hour. Renewal is a headless page load against the saved
browser profile, which takes a couple of seconds and shows no window.
"""

from __future__ import annotations

import json
import time
from typing import Any

from migratify.auth import browser
from migratify.config import get_logger, get_settings
from migratify.providers.base import AuthError

log = get_logger(__name__)

WEB_PLAYER_URL = "https://open.spotify.com/"
TOKEN_PATH = "/api/token"

#: Renew this far ahead of real expiry so a long migration cannot die between
#: two page fetches.
EXPIRY_MARGIN_S = 120


def _session_file():
    return get_settings().home / "spotify_session.json"


def _load() -> dict[str, Any]:
    path = _session_file()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, Any]) -> None:
    path = _session_file()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    path.chmod(0o600)


def _fresh(data: dict[str, Any]) -> bool:
    return bool(data.get("access_token")) and time.time() < data.get("expires_at", 0) - EXPIRY_MARGIN_S


def _capture_token() -> dict[str, Any]:
    """Load the web player headlessly and grab the token it mints for itself."""
    captured: dict[str, Any] = {}

    def on_response(response) -> None:
        if TOKEN_PATH not in response.url:
            return
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - non-JSON responses are not ours
            return
        if payload.get("accessToken"):
            captured.update(payload)

    browser.headless_visit(WEB_PLAYER_URL, on_response=on_response)

    if not captured:
        raise AuthError(
            "Could not read a Spotify token from your session.\n"
            "Your sign-in has probably expired. Run: migratify login spotify"
        )
    if captured.get("isAnonymous"):
        raise AuthError(
            "Spotify returned an anonymous session -- you are signed out.\n"
            "Run: migratify login spotify"
        )

    expires_ms = captured.get("accessTokenExpirationTimestampMs")
    expires_at = (
        float(expires_ms) / 1000 if expires_ms else time.time() + 3600
    )
    return {
        "access_token": captured["accessToken"],
        "expires_at": expires_at,
        "client_id": captured.get("clientId"),
        "obtained_at": time.time(),
    }


def connect(prefer_installed: bool = True, prefer_browser: str | None = None) -> None:
    """Interactively establish a Spotify session, then verify it yields a token."""
    browser.acquire(
        browser.SPOTIFY_DOMAIN,
        browser.SPOTIFY_SESSION_COOKIE,
        browser.SPOTIFY_LOGIN_URL,
        "Spotify",
        prefer_installed=prefer_installed,
        prefer_browser=prefer_browser,
    )
    # Prove the session actually works before telling the user it is connected.
    _save(_capture_token())
    log.info("Spotify session stored.")


def is_connected() -> bool:
    return bool(_load().get("access_token"))


def access_token() -> str:
    """A valid bearer token, renewing the stored one if it has aged out."""
    data = _load()
    if _fresh(data):
        return data["access_token"]

    if not data:
        raise AuthError("Not signed in to Spotify. Run: migratify login spotify")

    log.debug("Spotify token expired; renewing from the saved session.")
    data = _capture_token()
    _save(data)
    return data["access_token"]


def expires_at() -> float:
    return float(_load().get("expires_at", 0))


def bearer_token() -> str:
    """The token to use, whichever way the user chose to connect.

    The web-player session is preferred because it costs the user nothing to
    set up. The registered-app PKCE flow stays available underneath for when
    an unofficial endpoint inevitably shifts.
    """
    if is_connected():
        return access_token()

    from migratify.auth import spotify as pkce

    token = pkce.load_token()
    if token is None:
        raise AuthError(
            "Not connected to Spotify.\n\n"
            "  migratify login spotify        # just sign in, nothing to register\n"
            "  migratify auth spotify --pkce  # fallback, needs your own Spotify app"
        )
    return token.access_token
