"""YouTube Music authentication.

Three ways in, in order of how little they ask of the user:

1. **Sign in** (default). Cookies come from a browser you already use, or from
   a login window. Nothing to register, nothing to paste.
2. **Paste headers** (``--paste``). The classic ytmusicapi flow: copy a request
   header block out of devtools. No browser automation needed, so it works in
   environments where a window cannot be opened -- an SSH session, a container.
3. **OAuth** (``--oauth``). Needs a Google Cloud OAuth client of type *TV and
   Limited Input*. The most setup, but fully official and self-renewing.

All three end at the same place: something ``ytmusicapi.YTMusic`` accepts.

Authentication against Google's internal endpoints is cookie-based: given the
``SAPISID`` cookie, ytmusicapi derives the ``Authorization: SAPISIDHASH ...``
header itself on every request, so we only ever need to store the cookie
header, never a derived credential that would go stale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migratify.auth import browser
from migratify.config import get_logger, get_settings
from migratify.providers.base import AuthError

log = get_logger(__name__)

#: Sent alongside the cookie header. YouTube Music rejects requests that do not
#: look like they came from the web client.
BASE_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": "https://music.youtube.com",
    "referer": "https://music.youtube.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "x-goog-authuser": "0",
}


def _browser_file() -> Path:
    return get_settings().ytm_browser_file


def _oauth_file() -> Path:
    return get_settings().ytm_oauth_file


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    path.chmod(0o600)


# --- 1. sign in -------------------------------------------------------------


def connect(prefer_installed: bool = True, prefer_browser: str | None = None) -> None:
    """Establish a session by signing in, and store it as ytmusicapi headers."""
    jar = browser.acquire(
        browser.YTM_DOMAIN,
        browser.YTM_SESSION_COOKIE,
        browser.YTM_LOGIN_URL,
        "YouTube Music",
        prefer_installed=prefer_installed,
        prefer_browser=prefer_browser,
    )
    headers = dict(BASE_HEADERS)
    headers["cookie"] = jar.header()
    _write(_browser_file(), headers)
    log.info("YouTube Music session stored (from %s).", jar.source)


# --- 2. paste headers -------------------------------------------------------


def save_pasted_headers(raw: str) -> None:
    """Store a header block copied out of browser devtools.

    Accepts the raw multi-line ``Name: value`` form that devtools produces on
    copy, so the user can paste without editing anything.
    """
    headers = dict(BASE_HEADERS)
    found_cookie = False

    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        value = value.strip()
        if not value or name.startswith(":"):
            continue
        headers[name] = value
        if name == "cookie":
            found_cookie = True

    if not found_cookie:
        raise AuthError(
            "No Cookie header found in what you pasted.\n\n"
            "In music.youtube.com, open devtools > Network, click any request to "
            "/youtubei/v1/, and copy the full request headers."
        )
    if "SAPISID" not in headers["cookie"]:
        raise AuthError(
            "The pasted Cookie header has no SAPISID, which means it is not a "
            "signed-in session. Make sure you are logged in to YouTube Music."
        )

    _write(_browser_file(), headers)
    log.info("YouTube Music headers stored.")


# --- 3. official OAuth ------------------------------------------------------


def connect_oauth() -> None:
    """Run the ytmusicapi device OAuth flow using your own Google Cloud client."""
    settings = get_settings()
    if not settings.ytm_client_id or not settings.ytm_client_secret:
        raise AuthError(
            "OAuth needs a Google Cloud OAuth client of type 'TV and Limited Input'.\n"
            "Set MIGRATIFY_YTM_CLIENT_ID and MIGRATIFY_YTM_CLIENT_SECRET in .env.\n\n"
            "You almost certainly do not want this. Just run: migratify login ytmusic"
        )
    try:
        from ytmusicapi.setup import setup_oauth
    except ImportError as exc:  # pragma: no cover - ytmusicapi is a hard dependency
        raise AuthError("ytmusicapi is not installed.") from exc

    setup_oauth(
        client_id=settings.ytm_client_id,
        client_secret=settings.ytm_client_secret,
        filepath=str(_oauth_file()),
        open_browser=True,
    )
    _oauth_file().chmod(0o600)
    log.info("YouTube Music OAuth credentials stored.")


# --- resolution -------------------------------------------------------------


def is_connected() -> bool:
    return _browser_file().is_file() or _oauth_file().is_file()


def describe() -> str:
    if _browser_file().is_file():
        return "signed in (browser session)"
    if _oauth_file().is_file():
        return "connected (OAuth)"
    return "not connected"


def build_client():
    """Return an authenticated ``ytmusicapi.YTMusic``.

    Prefers the browser session, since that is the default path and the one
    most users will have. OAuth is used when it is the only thing present.
    """
    from ytmusicapi import YTMusic

    settings = get_settings()

    if _browser_file().is_file():
        return YTMusic(str(_browser_file()))

    if _oauth_file().is_file():
        oauth_credentials = None
        if settings.ytm_client_id and settings.ytm_client_secret:
            from ytmusicapi import OAuthCredentials

            oauth_credentials = OAuthCredentials(
                client_id=settings.ytm_client_id,
                client_secret=settings.ytm_client_secret,
            )
        return YTMusic(str(_oauth_file()), oauth_credentials=oauth_credentials)

    raise AuthError(
        "Not connected to YouTube Music.\n\n"
        "  migratify login ytmusic          # just sign in\n"
        "  migratify auth ytmusic --paste   # paste headers instead\n"
        "  migratify auth ytmusic --oauth   # official, needs a Google Cloud client"
    )
