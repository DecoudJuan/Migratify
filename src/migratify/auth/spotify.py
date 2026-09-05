"""Spotify OAuth via the Authorization Code flow with PKCE.

PKCE rather than the plain authorization-code flow for one reason: it needs no
client secret. Migratify runs on the user's own machine, where a "secret" is
not secret, so we simply never have one to leak.

The flow:

1. Generate a random verifier and its SHA-256 challenge.
2. Send the user to Spotify with the challenge.
3. Catch the redirect on a loopback server bound to 127.0.0.1.
4. Exchange the code plus the original verifier for tokens.

Only step 3 needs a server, and it shuts down the moment the code arrives.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from migratify.config import SPOTIFY_SCOPES, get_logger, get_settings
from migratify.providers.base import AuthError

log = get_logger(__name__)

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

#: Refresh this many seconds before actual expiry, so a long-running migration
#: never dies mid-flight on a token that expired between two page fetches.
EXPIRY_MARGIN_S = 120

_SUCCESS_PAGE = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>Migratify</title></head>
<body style="font-family:system-ui;text-align:center;padding-top:18vh">
<h1>Spotify connected</h1>
<p>You can close this tab and go back to the terminal.</p>
</body></html>"""

_FAILURE_PAGE = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>Migratify</title></head>
<body style="font-family:system-ui;text-align:center;padding-top:18vh">
<h1>Authorization failed</h1>
<p>Go back to the terminal for details.</p>
</body></html>"""


def _pkce_pair() -> tuple[str, str]:
    """Return a (verifier, challenge) pair for the S256 method."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """Single-shot handler that captures the authorization code."""

    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        params = parse_qs(urlparse(self.path).query)
        _CallbackHandler.result = {k: v[0] for k, v in params.items()}

        ok = "code" in _CallbackHandler.result
        body = _SUCCESS_PAGE if ok else _FAILURE_PAGE
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Silence the default stderr access log."""


class SpotifyToken:
    """An access token that knows how to renew itself."""

    def __init__(self, data: dict, path: Path, client_id: str) -> None:
        self._data = data
        self._path = path
        self._client_id = client_id

    @property
    def access_token(self) -> str:
        if self.expired:
            self.refresh()
        return self._data["access_token"]

    @property
    def expires_at(self) -> float:
        return float(self._data.get("expires_at", 0))

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - EXPIRY_MARGIN_S

    @property
    def scopes(self) -> set[str]:
        return set(self._data.get("scope", "").split())

    def missing_scopes(self) -> set[str]:
        """Scopes we need that this token was not granted.

        Tokens issued before a new capability landed will be missing its
        scope; better to say so plainly than to fail with a 403 mid-migration.
        """
        return set(SPOTIFY_SCOPES) - self.scopes

    def refresh(self) -> None:
        refresh_token = self._data.get("refresh_token")
        if not refresh_token:
            raise AuthError("Spotify session expired. Run: migratify auth spotify")

        response = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise AuthError(
                f"Could not refresh the Spotify session ({response.status_code}).\n"
                "Run: migratify auth spotify"
            )

        payload = response.json()
        # Spotify only sometimes returns a new refresh token; keep the old one
        # when it does not, or the session becomes unrenewable after one hour.
        payload.setdefault("refresh_token", refresh_token)
        self._data = _stamp(payload)
        self.save()

    def save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        self._path.chmod(0o600)


def _stamp(payload: dict) -> dict:
    payload["expires_at"] = time.time() + float(payload.get("expires_in", 3600))
    return payload


def _client_id() -> str:
    client_id = get_settings().spotify_client_id
    if not client_id:
        raise AuthError(
            "No Spotify client ID configured.\n\n"
            "  1. Create an app at https://developer.spotify.com/dashboard\n"
            f"  2. Add this redirect URI to it: {get_settings().spotify_redirect_uri}\n"
            "  3. Copy .env.example to .env and set MIGRATIFY_SPOTIFY_CLIENT_ID\n\n"
            "No client secret is needed -- Migratify uses PKCE."
        )
    return client_id


def load_token() -> SpotifyToken | None:
    """Load the stored token, or None if the user has never authenticated."""
    settings = get_settings()
    if not settings.spotify_token_file.is_file():
        return None
    data = json.loads(settings.spotify_token_file.read_text(encoding="utf-8"))
    return SpotifyToken(data, settings.spotify_token_file, _client_id())


def require_token() -> SpotifyToken:
    token = load_token()
    if token is None:
        raise AuthError("Not connected to Spotify. Run: migratify auth spotify")

    missing = token.missing_scopes()
    if missing:
        raise AuthError(
            "The stored Spotify session is missing permissions: "
            + ", ".join(sorted(missing))
            + "\nRun: migratify auth spotify"
        )
    return token


def authorize(timeout_s: int = 300) -> SpotifyToken:
    """Run the interactive PKCE flow and persist the resulting token."""
    settings = get_settings()
    client_id = _client_id()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    redirect = urlparse(settings.spotify_redirect_uri)
    host = redirect.hostname or "127.0.0.1"
    port = redirect.port or 8888

    try:
        server = HTTPServer((host, port), _CallbackHandler)
    except OSError as exc:
        raise AuthError(
            f"Could not listen on {host}:{port} for the Spotify redirect ({exc}).\n"
            "Something else is using that port, or the redirect URI in your Spotify "
            "app does not match MIGRATIFY_SPOTIFY_REDIRECT_URI."
        ) from exc

    _CallbackHandler.result = {}
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    url = f"{AUTHORIZE_URL}?" + urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": settings.spotify_redirect_uri,
            "scope": " ".join(SPOTIFY_SCOPES),
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
        }
    )

    log.info("Opening your browser to authorize Spotify...")
    log.info("If it does not open, paste this URL yourself:\n%s", url)
    webbrowser.open(url)

    thread.join(timeout=timeout_s)
    server.server_close()

    result = _CallbackHandler.result
    if not result:
        raise AuthError("Timed out waiting for the Spotify redirect.")
    if "error" in result:
        raise AuthError(f"Spotify refused the authorization: {result['error']}")
    if result.get("state") != state:
        # A mismatched state means the redirect did not come from the request
        # we started, so the code cannot be trusted.
        raise AuthError("State mismatch on the Spotify redirect. Try again.")

    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": settings.spotify_redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise AuthError(f"Spotify rejected the token exchange: {response.text}")

    token = SpotifyToken(_stamp(response.json()), settings.spotify_token_file, client_id)
    token.save()
    return token
