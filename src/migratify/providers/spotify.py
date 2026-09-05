"""Spotify as both a source and a destination.

Speaks the API the web player speaks, not the public Web API. See
:mod:`migratify.auth.spotify_web` for why: the public API is closed to free
accounts since 2025, and a web-player token is throttled into uselessness
there. The internal one works, on any account, today.

Reads go through pathfinder GraphQL. Writes go through spclient, which is a
different service with a different protocol -- so they are kept visibly apart
below rather than pretending to be one API.
"""

from __future__ import annotations

import base64

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

        return found[:limit]

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
        """Create a playlist through spclient.

        Two calls, because Spotify splits them: one creates the playlist and
        returns its URI, a second attaches the name and description. The
        second failing is not fatal -- an untitled playlist with the right
        tracks is recoverable, a lost migration is not.
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
                                    "addFirst": True,
                                    "items": [{"attributes": {"formatAttributes": []}}],
                                },
                            }
                        ]
                    }
                ]
            },
        )
        if response.status_code >= 400:
            raise ProviderError(
                f"Spotify refused to create the playlist ({response.status_code}): "
                f"{response.text[:300]}"
            )

        playlist_id = self._extract_created_id(response.json())
        if not playlist_id:
            raise ProviderError(
                "Spotify created something but did not return a playlist ID."
            )

        try:
            self._set_metadata(playlist_id, name, description, public)
        except ProviderError as exc:
            log.warning("Playlist created but naming it failed: %s", exc)

        return playlist_id

    @staticmethod
    def _extract_created_id(payload: dict) -> str | None:
        for key in ("uri", "playlistUri", "resultUri"):
            candidate = shapes.id_from_uri(payload.get(key))
            if candidate:
                return candidate
        # Some responses nest the new URI inside the applied delta.
        for delta in payload.get("deltas") or []:
            for op in delta.get("ops") or []:
                candidate = shapes.id_from_uri(shapes.dig(op, "add", "items", 0, "uri"))
                if candidate:
                    return candidate
        return None

    def _set_metadata(
        self, playlist_id: str, name: str, description: str | None, public: bool
    ) -> None:
        attributes: dict = {"name": name}
        if description:
            # Spotify truncates past 300 characters.
            attributes["description"] = description[:300]

        response = self._client.spclient(
            "POST",
            f"/playlist/v2/playlist/{playlist_id}/changes",
            json={"deltas": [{"ops": [{"kind": "UPDATE_LIST_ATTRIBUTES",
                                       "updateListAttributes": {"newAttributes":
                                                                {"values": attributes}}}]}]},
        )
        if response.status_code >= 400:
            raise ProviderError(
                f"Could not set the playlist name ({response.status_code}): "
                f"{response.text[:200]}"
            )

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        added = 0

        for start in range(0, len(track_ids), ADD_BATCH):
            batch = track_ids[start : start + ADD_BATCH]
            response = self._client.spclient(
                "POST",
                f"/playlist/v2/playlist/{playlist_id}/changes",
                json={
                    "deltas": [
                        {
                            "ops": [
                                {
                                    "kind": "ADD",
                                    "add": {
                                        "addLast": True,
                                        "items": [
                                            {"uri": f"spotify:track:{tid}"} for tid in batch
                                        ],
                                    },
                                }
                            ]
                        }
                    ]
                },
            )
            if response.status_code >= 400:
                raise ProviderError(
                    f"Spotify refused to add tracks ({response.status_code}): "
                    f"{response.text[:300]}"
                )
            added += len(batch)

        return added

    def set_cover(self, playlist_id: str, jpeg_bytes: bytes) -> bool:
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
                "Cover upload refused (%s): %s", response.status_code, response.text[:200]
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
