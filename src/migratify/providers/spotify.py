"""Spotify as both a source and a destination.

A thin, deliberate client over the Web API rather than a wrapper library: we
use a handful of endpoints, we need exact control over pagination and retry,
and the bearer token can come from either of two very different auth paths.

Rate limiting is handled by honouring ``Retry-After`` on 429 rather than by
guessing at a delay. Spotify tells us how long to wait; anything we invent is
either too slow or still too fast.
"""

from __future__ import annotations

import base64
import time

import httpx

from migratify.auth import spotify_session
from migratify.config import get_logger
from migratify.models import Playlist, Provider, ResultKind, Track
from migratify.providers.base import AuthError, ProviderError, SearchQuery

log = get_logger(__name__)

API = "https://api.spotify.com/v1"

#: Hard API limits, not preferences.
TRACKS_PAGE = 100
ADD_BATCH = 100
PLAYLISTS_PAGE = 50

#: Spotify rejects a cover above 256 KB *after* base64 encoding.
MAX_COVER_BYTES = 256 * 1024


class SpotifyProvider:
    name = Provider.SPOTIFY
    supports_cover_upload = True

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=30)
        self._user_id: str | None = None

    # -- plumbing ------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = path if path.startswith("http") else f"{API}{path}"

        for attempt in range(5):
            headers = kwargs.pop("headers", {}) | {
                "Authorization": f"Bearer {spotify_session.bearer_token()}"
            }
            response = self._client.request(method, url, headers=headers, **kwargs)

            if response.status_code == 429:
                # Spotify states the backoff; inventing one is either wasteful
                # or gets us throttled again immediately.
                wait = int(response.headers.get("Retry-After", "2")) + 1
                log.warning("Spotify rate limit hit, waiting %ss", wait)
                time.sleep(wait)
                continue

            if response.status_code == 401:
                if attempt == 0:
                    # The token aged out mid-run; the next loop mints a fresh one.
                    log.debug("Spotify returned 401, refreshing the token")
                    continue
                raise AuthError("Spotify rejected the session. Run: migratify login spotify")

            if response.status_code == 403:
                raise AuthError(
                    "Spotify refused this action (403). The session is missing a "
                    "permission it needs.\nRun: migratify login spotify"
                )

            if response.status_code >= 400:
                raise ProviderError(f"Spotify {method} {path} failed ({response.status_code}): {response.text[:300]}")

            return response

        raise ProviderError("Spotify kept rate limiting the request; giving up.")

    def _get(self, path: str, **params) -> dict:
        return self._request("GET", path, params=params or None).json()

    def _paginate(self, path: str, limit: int, **params) -> list[dict]:
        """Walk a paged collection to the end, following Spotify's own cursor."""
        items: list[dict] = []
        page = self._get(path, limit=limit, **params)
        while True:
            items.extend(page.get("items", []))
            next_url = page.get("next")
            if not next_url:
                return items
            page = self._request("GET", next_url).json()

    def _me(self) -> str:
        if self._user_id is None:
            self._user_id = self._get("/me")["id"]
        return self._user_id

    # -- parsing -------------------------------------------------------------

    @staticmethod
    def _to_track(raw: dict) -> Track | None:
        # Local files and removed tracks come back as null or without an ID.
        if not raw or not raw.get("id"):
            return None

        album = raw.get("album") or {}
        release = album.get("release_date") or ""

        return Track(
            provider=Provider.SPOTIFY,
            id=raw["id"],
            title=raw.get("name", ""),
            artists=[a["name"] for a in raw.get("artists", []) if a.get("name")],
            artist_ids=[a["id"] for a in raw.get("artists", []) if a.get("id")],
            album=album.get("name"),
            duration_ms=raw.get("duration_ms"),
            isrc=(raw.get("external_ids") or {}).get("isrc"),
            explicit=bool(raw.get("explicit")),
            release_year=int(release[:4]) if release[:4].isdigit() else None,
            kind=ResultKind.SONG,
            official=True,
        )

    @staticmethod
    def _to_playlist(raw: dict) -> Playlist:
        images = raw.get("images") or []
        return Playlist(
            provider=Provider.SPOTIFY,
            id=raw["id"],
            name=raw.get("name", "Untitled"),
            description=raw.get("description") or None,
            # Spotify returns images widest-first.
            cover_url=images[0]["url"] if images else None,
            track_count=(raw.get("tracks") or {}).get("total"),
            owner=(raw.get("owner") or {}).get("display_name"),
            public=bool(raw.get("public")),
            url=(raw.get("external_urls") or {}).get("spotify"),
        )

    # -- reading -------------------------------------------------------------

    def list_playlists(self, limit: int = 50) -> list[Playlist]:
        items = self._paginate("/me/playlists", limit=min(limit, PLAYLISTS_PAGE))
        return [self._to_playlist(item) for item in items if item]

    def get_playlist(self, playlist_id: str) -> Playlist:
        return self._to_playlist(self._get(f"/playlists/{playlist_id}"))

    def get_tracks(self, playlist_id: str) -> list[Track]:
        items = self._paginate(
            f"/playlists/{playlist_id}/tracks",
            limit=TRACKS_PAGE,
            additional_types="track",
        )
        tracks = []
        for item in items:
            track = self._to_track(item.get("track") or {})
            if track is not None:
                tracks.append(track)
        return tracks

    # -- searching -----------------------------------------------------------

    def build_queries(self, track: Track) -> list[SearchQuery]:
        """Field-filtered queries first, free text as the safety net.

        Spotify's field filters are precise but brittle: a title whose
        punctuation differs slightly can return nothing at all. So the strict
        queries run first for their precision, and an unfiltered query follows
        to catch what they miss.
        """
        from migratify.matching.normalize import normalize_track

        norm = normalize_track(track)
        queries: list[SearchQuery] = []

        if track.isrc:
            # Recording identity. A hit here needs no further searching.
            queries.append(SearchQuery(f"isrc:{track.isrc}", "isrc", decisive=True))

        if norm.title and norm.primary_artist:
            queries.append(
                SearchQuery(
                    f'track:"{norm.title}" artist:"{norm.primary_artist}"',
                    "fielded",
                )
            )
            queries.append(SearchQuery(f"{norm.title} {norm.primary_artist}", "free-text"))

        if norm.album and norm.primary_artist:
            queries.append(
                SearchQuery(
                    f'album:"{norm.album}" artist:"{norm.primary_artist}" {norm.title}',
                    "album-scoped",
                )
            )

        if not queries:
            queries.append(SearchQuery(track.title, "title-only"))

        return queries

    def search(self, query: SearchQuery, limit: int = 10) -> list[Track]:
        payload = self._get("/search", q=query.text, type="track", limit=limit)
        items = (payload.get("tracks") or {}).get("items") or []
        return [t for t in (self._to_track(item) for item in items) if t is not None]

    # -- writing -------------------------------------------------------------

    def create_playlist(
        self,
        name: str,
        description: str | None = None,
        public: bool = False,
    ) -> str:
        body: dict = {"name": name, "public": public}
        if description:
            # Spotify silently truncates past 300 characters.
            body["description"] = description[:300]
        response = self._request("POST", f"/users/{self._me()}/playlists", json=body)
        return response.json()["id"]

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        added = 0
        for start in range(0, len(track_ids), ADD_BATCH):
            batch = track_ids[start : start + ADD_BATCH]
            self._request(
                "POST",
                f"/playlists/{playlist_id}/tracks",
                json={"uris": [f"spotify:track:{tid}" for tid in batch]},
            )
            added += len(batch)
        return added

    def set_cover(self, playlist_id: str, jpeg_bytes: bytes) -> bool:
        encoded = base64.b64encode(jpeg_bytes)
        if len(encoded) > MAX_COVER_BYTES:
            # The caller is expected to have compressed it already; refusing is
            # better than a 413 the user cannot interpret.
            raise ProviderError(
                f"Cover is {len(encoded) // 1024} KB base64-encoded; Spotify allows 256 KB."
            )
        self._request(
            "PUT",
            f"/playlists/{playlist_id}/images",
            content=encoded,
            headers={"Content-Type": "image/jpeg"},
        )
        return True

    # -- identity ------------------------------------------------------------

    def playlist_url(self, playlist_id: str) -> str:
        return f"https://open.spotify.com/playlist/{playlist_id}"

    @staticmethod
    def parse_playlist_ref(ref: str) -> str | None:
        ref = ref.strip()
        if "open.spotify.com" in ref and "/playlist/" in ref:
            tail = ref.split("/playlist/", 1)[1]
            return tail.split("?", 1)[0].split("/", 1)[0] or None
        if ref.startswith("spotify:playlist:"):
            return ref.split(":", 2)[2] or None
        # A bare base62 ID.
        if ref.isalnum() and len(ref) == 22:
            return ref
        return None
