"""Spotify as both a source and a destination.

Speaks the API the web player speaks, not the public Web API. See
:mod:`migratify.auth.spotify_web` for why: the public API is closed to free
accounts since 2025, and a web-player token is throttled into uselessness
there. The internal one works, on any account, today.

Reads go through pathfinder GraphQL. Writes are split across both services in
a way that is not guessable and had to be observed: creating a playlist and
setting its attributes are spclient calls, while *adding tracks* is a
pathfinder mutation. ``scripts/discover_spotify_writes.py`` is what recorded
that, and is what to re-run if it ever changes.
"""

from __future__ import annotations

import base64
import time
from concurrent.futures import ThreadPoolExecutor

from migratify.auth.spotify_web import SpotifyWebClient
from migratify.config import get_logger
from migratify.models import Playlist, Provider, Track
from migratify.providers import spotify_shapes as shapes
from migratify.providers.base import ProviderError, SearchQuery

log = get_logger(__name__)

#: Pathfinder paginates; these are what the player itself uses.
TRACKS_PAGE = 100
LIBRARY_PAGE = 50

#: spclient accepts large change batches, but a smaller one is cheaper to
#: retry and gives better progress reporting on a long playlist.
ADD_BATCH = 100

#: One small spclient call per playlist, so a modest pool keeps a listing
#: quick without hammering the endpoint we also write playlists through.
COUNT_WORKERS = 6

#: Spotify rejects a cover above 256 KB once base64-encoded.
MAX_COVER_BYTES = 256 * 1024


class SpotifyProvider:
    name = Provider.SPOTIFY
    supports_cover_upload = True

    def __init__(self) -> None:
        self._client = SpotifyWebClient()
        self._user_id: str | None = None

    # -- identity ------------------------------------------------------------

    def _me(self) -> str:
        if self._user_id is None:
            data = self._client.query("profileAttributes")
            uri = shapes.dig(data, "me", "profile", "uri")
            self._user_id = shapes.id_from_uri(uri)
            if not self._user_id:
                raise ProviderError(
                    "Could not read your Spotify user ID. Run: migratify login spotify"
                )
        return self._user_id

    # -- reading -------------------------------------------------------------

    def list_playlists(self, limit: int = 50) -> list[Playlist]:
        found: list[Playlist] = []
        offset = 0

        while len(found) < limit:
            page = self._client.query(
                "libraryV3", {"limit": min(LIBRARY_PAGE, limit), "offset": offset}
            )
            batch = shapes.parse_library(page)
            raw_count = len(shapes.dig(page, "me", "libraryV3", "items", default=[]) or [])
            found.extend(batch)

            # Page on the raw count, not the filtered one: a page made entirely
            # of artists and albums yields no playlists but is not the end.
            if raw_count == 0:
                break
            offset += raw_count

        listing = found[:limit]
        self._fill_track_counts(listing)
        return listing

    def _fill_track_counts(self, playlists: list[Playlist]) -> None:
        """Attach a track count to each playlist in a listing.

        The library does not carry one. Checked live against a real account:
        ``libraryV3`` returns no count, the batch entity decorator
        (``fetchEntitiesForRecentlyPlayed``) returns the same fields and no
        count either, and spclient has no multi-playlist metadata endpoint --
        every plausible spelling 404s. Its rootlist does report a ``length``,
        but that is how many playlists there are, not how many tracks.

        What does exist is a per-playlist ``/metadata``, which answers with the
        length and *not* the track list: ~1.6 KB against the ~130 KB the full
        playlist body costs. So it is one of those each, in a small pool,
        rather than one heavyweight read each.

        A count is a nicety, so anything that goes wrong leaves it unknown --
        the listing still prints, with a ``?``.
        """

        def fetch(playlist: Playlist) -> None:
            try:
                response = self._client.spclient(
                    "GET", f"/playlist/v2/playlist/{playlist.id}/metadata"
                )
                if response.status_code < 400:
                    playlist.track_count = response.json().get("length")
            except Exception:
                log.debug("No track count for %s", playlist.id, exc_info=True)

        with ThreadPoolExecutor(max_workers=COUNT_WORKERS) as pool:
            list(pool.map(fetch, playlists))

    def get_playlist(self, playlist_id: str) -> Playlist:
        data = self._client.query(
            "fetchPlaylistMetadata", {"uri": f"spotify:playlist:{playlist_id}"}
        )
        return shapes.parse_playlist(data, playlist_id)

    def get_tracks(self, playlist_id: str) -> list[Track]:
        uri = f"spotify:playlist:{playlist_id}"
        tracks: list[Track] = []
        offset = 0

        while True:
            page = self._client.query(
                "fetchPlaylistContents",
                {"uri": uri, "offset": offset, "limit": TRACKS_PAGE},
            )
            batch, total = shapes.parse_playlist_tracks(page)
            tracks.extend(batch)

            offset += TRACKS_PAGE
            if total is None or offset >= total:
                break

        return tracks

    # -- searching -----------------------------------------------------------

    def build_queries(self, track: Track) -> list[SearchQuery]:
        """Free-text queries only.

        The public API's ``track:``/``artist:``/``isrc:`` field filters do not
        exist here -- the internal search takes a plain term, the same one the
        search box sends. So precision has to come from the scorer rather than
        from the query, which is what it was built for anyway.
        """
        from migratify.matching.normalize import normalize_track

        norm = normalize_track(track)
        queries: list[SearchQuery] = []

        if norm.title and norm.primary_artist:
            queries.append(SearchQuery(f"{norm.title} {norm.primary_artist}", "title-artist"))
            queries.append(SearchQuery(f"{norm.primary_artist} {norm.title}", "artist-title"))

        all_artists = " ".join(sorted(norm.artists))
        if all_artists and all_artists != norm.primary_artist:
            queries.append(SearchQuery(f"{track.title} {all_artists}", "full-credit"))

        if norm.album and norm.primary_artist:
            queries.append(
                SearchQuery(f"{norm.title} {norm.album} {norm.primary_artist}", "album-scoped")
            )

        return queries or [SearchQuery(track.title, "title-only")]

    def search(self, query: SearchQuery, limit: int = 10) -> list[Track]:
        data = self._client.query(
            "searchTracks", {"searchTerm": query.text, "offset": 0, "limit": limit}
        )
        return shapes.parse_search_tracks(data)[:limit]

    # -- writing -------------------------------------------------------------

    def create_playlist(
        self,
        name: str,
        description: str | None = None,
        public: bool = False,
    ) -> str:
        """Create a playlist, exactly the way the web player does.

        Three calls, because Spotify genuinely splits them:

        1. ``POST /playlist/v2/playlist`` creates it and returns its URI.
           A playlist created this way exists but is *not* in your library.
        2. Adding it to the rootlist is what makes it appear there.
        3. A separate change sets the description -- creation only accepts a
           name.

        Steps 2 and 3 are reported but not fatal. A playlist holding the right
        tracks is recoverable by hand; losing a completed migration is not.
        """
        response = self._client.spclient(
            "POST",
            "/playlist/v2/playlist",
            json={
                "ops": [
                    {
                        "kind": "UPDATE_LIST_ATTRIBUTES",
                        "updateListAttributes": {"newAttributes": {"values": {"name": name}}},
                    }
                ]
            },
        )
        if response.status_code >= 400:
            raise ProviderError(
                f"Spotify refused to create the playlist ({response.status_code}): "
                f"{response.text[:300]}"
            )

        playlist_id = shapes.id_from_uri((response.json() or {}).get("uri"))
        if not playlist_id:
            raise ProviderError("Spotify created a playlist but returned no URI for it.")

        try:
            self._add_to_library(playlist_id)
        except ProviderError as exc:
            log.warning("Playlist created but not added to your library: %s", exc)

        if description:
            try:
                # Name goes with it: a description-only update is rejected with
                # a bare 400, and the player never sends one on its own.
                self._set_attributes(
                    playlist_id, {"name": name, "description": description[:300]}
                )
            except ProviderError as exc:
                log.warning("Playlist created but the description was not set: %s", exc)

        return playlist_id

    def _add_to_library(self, playlist_id: str) -> None:
        """Put a newly created playlist in the user's library.

        Without this the playlist exists and is reachable by URL, but never
        shows up in the sidebar -- which looks exactly like a failed migration.
        """
        response = self._client.spclient(
            "POST",
            f"/playlist/v2/user/{self._me()}/rootlist/changes",
            json={
                "deltas": [
                    {
                        "ops": [
                            {
                                "kind": "ADD",
                                "add": {
                                    "items": [
                                        {
                                            "uri": f"spotify:playlist:{playlist_id}",
                                            "attributes": {
                                                "timestamp": str(int(time.time() * 1000))
                                            },
                                        }
                                    ],
                                    "addFirst": True,
                                },
                            }
                        ],
                        "info": {"source": {"client": "WEBPLAYER"}},
                    }
                ]
            },
        )
        if response.status_code >= 400:
            raise ProviderError(f"{response.status_code}: {response.text[:200]}")

    def _set_attributes(self, playlist_id: str, values: dict[str, str]) -> None:
        response = self._client.spclient(
            "POST",
            f"/playlist/v2/playlist/{playlist_id}/changes",
            json={
                "deltas": [
                    {
                        "ops": [
                            {
                                "kind": "UPDATE_LIST_ATTRIBUTES",
                                "updateListAttributes": {"newAttributes": {"values": values}},
                            }
                        ],
                        "info": {"source": {"client": "WEBPLAYER"}},
                    }
                ]
            },
        )
        if response.status_code >= 400:
            raise ProviderError(f"{response.status_code}: {response.text[:200]}")

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        """Append tracks via the pathfinder ``addToPlaylist`` mutation.

        Adding goes through GraphQL rather than spclient -- the two services
        split the work differently from how the read side does, which is why
        this was worth observing rather than guessing.

        ``BOTTOM_OF_PLAYLIST`` matters for correctness, not preference: the
        player itself inserts at the top, which would reverse a batched
        migration and silently scramble the playlist order.
        """
        added = 0

        for start in range(0, len(track_ids), ADD_BATCH):
            batch = track_ids[start : start + ADD_BATCH]
            self._client.query(
                "addToPlaylist",
                {
                    "playlistItemUris": [f"spotify:track:{tid}" for tid in batch],
                    "playlistUri": f"spotify:playlist:{playlist_id}",
                    "newPosition": {"moveType": "BOTTOM_OF_PLAYLIST", "fromUid": None},
                },
            )
            added += len(batch)

        return added

    def set_cover(self, playlist_id: str, jpeg_bytes: bytes) -> bool:
        """Upload playlist artwork.

        Unlike the rest of the write path, this endpoint has not been observed
        from a real session yet -- the discovery run covered creating, naming
        and adding, not setting an image -- so the address here is an educated
        guess and currently answers 404.

        It fails loudly in the log and returns False rather than raising, so a
        cover never costs an otherwise completed migration. To fix it properly,
        re-run scripts/discover_spotify_writes.py and change a playlist image
        while it watches.
        """
        encoded = base64.b64encode(jpeg_bytes)
        if len(encoded) > MAX_COVER_BYTES:
            raise ProviderError(
                f"Cover is {len(encoded) // 1024} KB base64-encoded; Spotify allows 256 KB."
            )

        response = self._client.spclient(
            "PUT",
            f"/playlist-image/v1/playlist/{playlist_id}",
            content=encoded,
            headers={"content-type": "image/jpeg"},
        )
        if response.status_code >= 400:
            log.warning(
                "Spotify refused the cover upload (%s). The endpoint is not yet "
                "confirmed; re-run scripts/discover_spotify_writes.py and change a "
                "playlist image while it watches.",
                response.status_code,
            )
            return False
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
        if ref.isalnum() and len(ref) == 22:
            return ref
        return None
