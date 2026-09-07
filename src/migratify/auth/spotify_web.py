"""Access to the Spotify API the web player itself uses.

Why this exists
---------------

The public Web API (``api.spotify.com/v1``) is unusable for most people:

* Since 2025 Spotify refuses **every** call from a registered app whose owner
  has no Premium subscription — verified, with all scopes granted and a valid
  token, on every endpoint.
* A web-player token does authenticate against it, but is rate-limited into
  uselessness there — repeated 429s with escalating ``Retry-After``. That
  token is minted under Spotify's own shared client, and the public API is not
  where it belongs.

Meanwhile the real web player works perfectly on a free account. It simply
talks to somewhere else: ``api-partner.spotify.com/pathfinder/v2/query`` for
reads, and ``spclient.wg.spotify.com`` for playlist changes. Observed live:
54 of 55 requests returned 200.

So this module talks to the same place, the same way.

How it stays working
--------------------

Pathfinder uses persisted queries: the client sends an ``operationName`` and a
``sha256Hash`` instead of a query document, and those hashes change with every
web-player release. Hardcoding them guarantees breakage on Spotify's schedule.

**So we do not hardcode anything.** We open the real player, watch the
requests it makes, and keep the operation hashes *and* a sample of the
variables it sent. Replaying an operation means taking that captured sample
and overriding only the fields we care about — a URI, an offset, a search
term. We never have to know the schema, and when Spotify ships a new client we
recapture and carry on.

The principle throughout: let their client do the part that keeps changing,
and observe the result.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from migratify.auth import browser
from migratify.config import get_logger, get_settings
from migratify.providers.base import AuthError, ProviderError

log = get_logger(__name__)

PATHFINDER_HOST = "api-partner.spotify.com"
PATHFINDER_PATH = "/pathfinder/v2/query"
SPCLIENT = "https://spclient.wg.spotify.com"

#: Pages to visit while capturing. Each one makes the player issue a different
#: set of operations; together they cover everything Migratify needs.
_CAPTURE_PAGES = (
    ("https://open.spotify.com/collection/playlists", 11_000),
    # A playlist page is the only one that issues the contents read, and it has
    # to be a playlist that exists for everyone -- the library page happens to
    # prefetch one, but only when the sidebar has something to prefetch.
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", 9_000),
    ("https://open.spotify.com/search/mogwai/tracks", 9_000),
)

#: Headers that describe the *transport* of the captured request rather than
#: the session, and which httpx must be free to set itself.
_DROP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "accept-encoding",
    ":method",
    ":path",
    ":authority",
    ":scheme",
}

#: Re-capture rather than trusting a stale snapshot. The bearer token inside
#: the captured headers lasts about an hour.
_MAX_AGE_S = 45 * 60

#: Mutation hashes, which cannot be learned the way read operations are.
#:
#: Reads are observable for free: loading a page makes the player issue them,
#: so we watch and record. A mutation only happens when someone actually
#: changes something, so passively browsing never reveals it -- and capturing
#: one by performing it would mean writing to the user's account just to learn
#: how to write to their account.
#:
#: So these are pinned, which is the one place this module hardcodes anything.
#: They were recorded from a real session by ``scripts/discover_spotify_writes.py``,
#: and when Spotify ships a client that changes them, re-running that script
#: prints the new values. The error path below says so explicitly rather than
#: leaving someone to guess.
KNOWN_MUTATIONS: dict[str, str] = {
    "addToPlaylist": "47b2a1234b17748d332dd0431534f22450e9ecbb3d5ddcdacbd83368636a0990",
}

#: Variable templates for those same operations, for the same reason.
MUTATION_VARIABLES: dict[str, dict[str, Any]] = {
    "addToPlaylist": {
        "playlistItemUris": [],
        "playlistUri": "",
        "newPosition": {"moveType": "BOTTOM_OF_PLAYLIST", "fromUid": None},
    },
}


@dataclass
class WebSession:
    """Everything needed to speak to the internal API as the player does."""

    endpoint: str
    headers: dict[str, str] = field(default_factory=dict)

    #: operationName -> {"sha256": str, "variables": dict}
    operations: dict[str, dict[str, Any]] = field(default_factory=dict)

    captured_at: float = 0.0

    @property
    def stale(self) -> bool:
        return time.time() - self.captured_at > _MAX_AGE_S

    def to_json(self) -> str:
        return json.dumps(
            {
                "endpoint": self.endpoint,
                "headers": self.headers,
                "operations": self.operations,
                "captured_at": self.captured_at,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> WebSession:
        data = json.loads(raw)
        return cls(
            endpoint=data["endpoint"],
            headers=data.get("headers", {}),
            operations=data.get("operations", {}),
            captured_at=data.get("captured_at", 0.0),
        )


def _session_file():
    return get_settings().home / "spotify_web.json"


def _save(session: WebSession) -> None:
    path = _session_file()
    path.write_text(session.to_json(), encoding="utf-8")
    # The captured headers carry a bearer token.
    path.chmod(0o600)


def _load() -> WebSession | None:
    path = _session_file()
    if not path.is_file():
        return None
    try:
        return WebSession.from_json(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def capture() -> WebSession:
    """Watch the real player and record how it asks for things.

    Nothing here is printed or logged beyond operation names -- the captured
    headers contain a live credential and are written straight to the session
    file with owner-only permissions.
    """
    sync_playwright = browser._require_playwright()

    session = WebSession(endpoint=f"https://{PATHFINDER_HOST}{PATHFINDER_PATH}")

    def on_request(request) -> None:
        if PATHFINDER_PATH not in request.url:
            return
        try:
            body = request.post_data_json
        except Exception:
            return
        if not isinstance(body, dict):
            return

        name = body.get("operationName")
        sha = ((body.get("extensions") or {}).get("persistedQuery") or {}).get("sha256Hash")
        if not name or not sha:
            return

        session.endpoint = request.url.split("?")[0]
        # Keep the whole header set minus transport headers, so we do not have
        # to track which ones Spotify starts requiring next.
        session.headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS
        }
        session.operations[name] = {
            "sha256": sha,
            "variables": body.get("variables") or {},
        }

    log.info("Learning how the Spotify web player asks for things...")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(browser.profile_dir()), headless=True, **browser._launch_kwargs()
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.on("request", on_request)
            for url, settle_ms in _CAPTURE_PAGES:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(settle_ms)
        finally:
            context.close()

    if not session.operations:
        raise AuthError(
            "Could not read the Spotify web player session.\n"
            "Your sign-in has probably expired. Run: migratify login spotify"
        )

    session.captured_at = time.time()
    _save(session)
    log.info("Learned %d operations: %s", len(session.operations),
             ", ".join(sorted(session.operations)))
    return session


def get_session(refresh: bool = False) -> WebSession:
    session = _load()
    if refresh or session is None or session.stale or not session.operations:
        return capture()
    return session


def is_connected() -> bool:
    return _load() is not None


def connect(prefer_installed: bool = True, prefer_browser: str | None = None) -> None:
    """Sign in to Spotify, then prove the session actually works.

    Capturing straight after signing in is deliberate: a stored cookie is not
    evidence of a usable session, and finding that out now beats finding out
    halfway through a migration.
    """
    browser.acquire(
        browser.SPOTIFY_DOMAIN,
        browser.SPOTIFY_SESSION_COOKIE,
        browser.SPOTIFY_LOGIN_URL,
        "Spotify",
        prefer_installed=prefer_installed,
        prefer_browser=prefer_browser,
    )
    capture()
    log.info("Spotify session stored.")


def _pinned(operation: str) -> dict[str, Any] | None:
    """The pinned spec for a mutation, if we have one."""
    sha = KNOWN_MUTATIONS.get(operation)
    if sha is None:
        return None
    return {"sha256": sha, "variables": copy.deepcopy(MUTATION_VARIABLES.get(operation, {}))}


class SpotifyWebClient:
    """Replays the player's own operations with our variables."""

    def __init__(self) -> None:
        self._session = get_session()
        self._http = httpx.Client(timeout=30, http2=False)

    # -- pathfinder ----------------------------------------------------------

    def _resolve(self, names: Sequence[str]) -> tuple[str, dict[str, Any]] | None:
        """The first of ``names`` we know how to send, observed or pinned."""
        for name in names:
            spec = self._session.operations.get(name) or _pinned(name)
            if spec is not None:
                return name, spec
        return None

    def query(
        self, operation: str | Sequence[str], overrides: dict[str, Any] | None = None
    ) -> dict:
        """Run a captured GraphQL operation.

        ``operation`` may be several names in preference order. The player
        renames its reads between releases -- a page of playlist contents came
        from ``fetchPlaylistContents`` and now comes from ``fetchPlaylist`` --
        and both spellings take the same variables, so asking for whichever one
        this client actually issues is the same trick as not pinning hashes.

        ``overrides`` are merged onto the variables the player itself sent, so
        we only ever specify what we mean to change. Fields we do not
        understand keep whatever value the real client used, which is what
        makes this survive schema changes.
        """
        names = (operation,) if isinstance(operation, str) else tuple(operation)

        for attempt in range(2):
            resolved = self._resolve(names)
            if resolved is None:
                if attempt == 0:
                    # An operation we have not seen yet -- the player may issue
                    # it only on a page we have not visited this run.
                    self._session = capture()
                    continue
                wanted = " or ".join(repr(name) for name in names)
                raise ProviderError(
                    f"Spotify operation {wanted} was not observed in the web player. "
                    "It may have been renamed in a new release."
                )

            operation, spec = resolved
            variables = copy.deepcopy(spec["variables"])
            variables.update(overrides or {})

            payload = {
                "operationName": operation,
                "variables": variables,
                "extensions": {
                    "persistedQuery": {"version": 1, "sha256Hash": spec["sha256"]}
                },
            }

            response = self._http.post(
                self._session.endpoint, headers=self._session.headers, json=payload
            )

            if response.status_code in (401, 403) and attempt == 0:
                # The captured bearer token aged out; relearn and retry once.
                log.debug("Spotify session expired mid-request, recapturing")
                self._session = capture()
                continue

            if response.status_code >= 400:
                raise ProviderError(
                    f"Spotify {operation} failed ({response.status_code}): "
                    f"{response.text[:300]}"
                )

            body = response.json()
            if body.get("errors"):
                message = body["errors"][0].get("message", "unknown error")
                if attempt == 0 and "persisted" in message.lower():
                    if operation in KNOWN_MUTATIONS:
                        # Recapturing cannot help: a mutation hash is pinned,
                        # not observed. Say what will actually fix it.
                        raise ProviderError(
                            f"Spotify no longer recognizes the {operation!r} request. "
                            "Its web player has changed.\n"
                            "Refresh it by running: "
                            "python scripts/discover_spotify_writes.py"
                        )
                    # A read operation: a new client release renamed or
                    # rehashed it, so relearn and retry.
                    self._session = capture()
                    continue
                raise ProviderError(f"Spotify {operation} returned an error: {message}")

            return body.get("data") or {}

        raise ProviderError(f"Spotify {operation} could not be completed.")

    # -- spclient ------------------------------------------------------------

    def spclient(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Call an spclient endpoint with the captured session headers.

        Playlist changes do not go through pathfinder; they go here.
        """
        headers = {
            k: v
            for k, v in self._session.headers.items()
            # These are pathfinder-specific and confuse spclient.
            if k.lower() not in {"content-type", "accept"}
        }
        headers.setdefault("accept", "application/json")
        if "json" in kwargs:
            headers.setdefault("content-type", "application/json")
        headers |= kwargs.pop("headers", {})

        response = self._http.request(
            method, f"{SPCLIENT}{path}", headers=headers, **kwargs
        )

        if response.status_code in (401, 403):
            self._session = capture()
            headers |= {
                k: v for k, v in self._session.headers.items() if k.lower() == "authorization"
            }
            response = self._http.request(
                method, f"{SPCLIENT}{path}", headers=headers, **kwargs
            )

        return response

    @property
    def operations(self) -> list[str]:
        return sorted(self._session.operations)
