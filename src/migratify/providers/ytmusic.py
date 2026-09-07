"""YouTube Music as both a source and a destination.

Wraps ``ytmusicapi``, translating its result shapes into neutral models. Most
of the work here is defensive: YouTube Music results are far less regular than
Spotify's. Duration may be a string, a number of seconds, or absent. The
artist field may hold a real artist, an auto-generated ``- Topic`` channel, an
album name, or the string ``"Song"``. Results may be catalog songs or arbitrary
uploads.

Being strict about that here is what lets the scorer stay simple: by the time a
track leaves this module it either has trustworthy fields or admits it does
not.
"""

from __future__ import annotations

import re

from migratify.auth import ytmusic as ytm_auth
from migratify.config import get_logger
from migratify.models import Playlist, Provider, ResultKind, Track
from migratify.providers.base import LIKED, ProviderError, SearchQuery

log = get_logger(__name__)

#: ytmusicapi accepts arbitrarily long lists but the underlying call degrades;
#: batching keeps each request small enough to retry cheaply.
ADD_BATCH = 50

#: YouTube Music addresses the saved library as a playlist with a fixed id.
#: Unlike Spotify's, it reads exactly like any other playlist -- so the only
#: work here is translating the neutral id at the edges.
LIKED_PLAYLIST = "LM"

#: Values that appear in the artist slot but are not artists.
_NON_ARTIST = {"song", "video", "single", "album", "ep", "playlist", ""}

_DURATION = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


def _parse_duration(raw: object) -> int | None:
    """Milliseconds from whatever shape the duration arrived in."""
    if raw is None:
        return None
    if isinstance(raw, int):
        # ytmusicapi exposes duration_seconds on most results.
        return raw * 1000 if raw < 100_000 else raw
    if isinstance(raw, str):
        match = _DURATION.match(raw.strip())
        if match:
            hours, minutes, seconds = match.groups()
            total = int(minutes) * 60 + int(seconds) + (int(hours) * 3600 if hours else 0)
            return total * 1000
    return None


def _pick_cover(thumbnails: list[dict] | None) -> str | None:
    """Highest-resolution thumbnail available.

    Ordering is not guaranteed, so pick by area rather than trusting position.
    """
    if not thumbnails:
        return None
    best = max(thumbnails, key=lambda t: (t.get("width", 0) * t.get("height", 0)))
    return best.get("url")


class YouTubeMusicProvider:
    name = Provider.YTMUSIC

    #: ytmusicapi exposes no endpoint for playlist cover upload, and neither
    #: does YouTube Music itself outside its own web client. This is a
    #: platform limit, not an omission -- see CLAUDE.md.
    supports_cover_upload = False

    def __init__(self) -> None:
        self._client = ytm_auth.build_client()

    # -- parsing -------------------------------------------------------------

    @classmethod
    def _to_track(cls, raw: dict) -> Track | None:
        video_id = raw.get("videoId")
        if not video_id:
            # Unavailable or region-blocked entries still appear in playlists.
            return None

        artists: list[str] = []
        artist_ids: list[str] = []
        for artist in raw.get("artists") or []:
            name = (artist.get("name") or "").strip()
            if name.casefold() in _NON_ARTIST:
                continue
            artists.append(name)
            if artist.get("id"):
                artist_ids.append(artist["id"])

        album = raw.get("album")
        album_name = album.get("name") if isinstance(album, dict) else album

        result_type = (raw.get("resultType") or raw.get("videoType") or "").lower()
        if "song" in result_type or raw.get("category") == "Songs":
            kind = ResultKind.SONG
        elif "video" in result_type:
            kind = ResultKind.VIDEO
        else:
            # Playlist entries carry no result type. An entry with a real album
            # and a real artist is a catalog song; anything else is an upload.
            kind = ResultKind.SONG if (album_name and artists) else ResultKind.UNKNOWN

        return Track(
            provider=Provider.YTMUSIC,
            id=video_id,
            title=raw.get("title", ""),
            artists=artists,
            artist_ids=artist_ids,
            album=album_name,
            duration_ms=_parse_duration(raw.get("duration_seconds") or raw.get("duration")),
            explicit=bool(raw.get("isExplicit")),
            kind=kind,
            # An artist ID means the credit resolves to a real catalog artist
            # rather than an arbitrary uploader.
            official=bool(artist_ids),
        )

    # -- reading -------------------------------------------------------------

    def _liked_playlist(self) -> Playlist | None:
        """Liked Music as a listing row.

        ``get_library_playlists`` does not include it, and a listing that
        leaves it out hides the one library most people want to migrate. It is
        a nicety though, so a failure here costs the row and not the listing.
        """
        try:
            return self.get_playlist(LIKED)
        except ProviderError:
            log.debug("Could not read Liked Music for the listing", exc_info=True)
            return None

    def list_playlists(self, limit: int = 50) -> list[Playlist]:
        playlists = []

        liked = self._liked_playlist()
        if liked is not None:
            playlists.append(liked)

        for raw in self._client.get_library_playlists(limit=limit):
            playlists.append(
                Playlist(
                    provider=Provider.YTMUSIC,
                    id=raw.get("playlistId", ""),
                    name=raw.get("title", "Untitled"),
                    description=raw.get("description"),
                    cover_url=_pick_cover(raw.get("thumbnails")),
                    track_count=raw.get("count"),
                    url=self.playlist_url(raw.get("playlistId", "")),
                )
            )
        return [p for p in playlists if p.id]

    @staticmethod
    def _native(playlist_id: str) -> str:
        return LIKED_PLAYLIST if playlist_id == LIKED else playlist_id

    def _fetch(self, playlist_id: str, limit: int | None = None) -> dict:
        try:
            return self._client.get_playlist(self._native(playlist_id), limit=limit)
        except Exception as exc:
            raise ProviderError(
                f"Could not read YouTube Music playlist {playlist_id}: {exc}"
            ) from exc

    def get_playlist(self, playlist_id: str) -> Playlist:
        raw = self._fetch(playlist_id, limit=1)
        return Playlist(
            provider=Provider.YTMUSIC,
            # The neutral id goes back out, not the native one, so the store
            # and the match cache key on the same thing in both directions.
            id=playlist_id,
            name=raw.get("title", "Untitled"),
            description=raw.get("description"),
            cover_url=_pick_cover(raw.get("thumbnails")),
            track_count=raw.get("trackCount"),
            owner=(raw.get("author") or {}).get("name"),
            public=raw.get("privacy") == "PUBLIC",
            url=self.playlist_url(playlist_id),
        )

    def get_tracks(self, playlist_id: str) -> list[Track]:
        # limit=None makes ytmusicapi follow continuations to the end.
        raw = self._fetch(playlist_id, limit=None)
        tracks = []
        for item in raw.get("tracks") or []:
            track = self._to_track(item)
            if track is not None:
                tracks.append(track)
        return tracks

    # -- searching -----------------------------------------------------------

    def build_queries(self, track: Track) -> list[SearchQuery]:
        """Songs first, videos as a fallback.

        Both orderings of title and artist are tried because YouTube Music's
        ranking is sensitive to word order in a way Spotify's is not, and the
        weaker ordering sometimes surfaces the only correct result.

        The video pass exists for tracks that were never released to the music
        catalog and live on YouTube only. Those results are scored down by
        design, but a scored-down real match beats no match at all.
        """
        from migratify.matching.normalize import normalize_track

        norm = normalize_track(track)
        title, artist = norm.title, norm.primary_artist
        queries: list[SearchQuery] = []

        if title and artist:
            queries.append(SearchQuery(f"{title} {artist}", "song", result_filter="songs"))
            queries.append(SearchQuery(f"{artist} {title}", "song-reversed", result_filter="songs"))

        all_artists = " ".join(sorted(norm.artists))
        if all_artists and all_artists != artist:
            queries.append(
                SearchQuery(f"{track.title} {all_artists}", "song-full", result_filter="songs")
            )

        if title:
            queries.append(
                SearchQuery(f"{title} {artist}".strip(), "video", result_filter="videos")
            )

        if norm.album and artist:
            queries.append(
                SearchQuery(f"{norm.album} {artist} {title}", "album-scoped", result_filter="songs")
            )

        return queries or [SearchQuery(track.title, "title-only", result_filter="songs")]

    def search(self, query: SearchQuery, limit: int = 10) -> list[Track]:
        try:
            results = self._client.search(
                query.text,
                filter=query.result_filter,
                limit=limit,
                ignore_spelling=True,
            )
        except Exception as exc:
            log.debug("YouTube Music search failed for %r: %s", query.text, exc)
            return []

        tracks = []
        for item in results[:limit]:
            track = self._to_track(item)
            if track is not None:
                tracks.append(track)
        return tracks

    # -- writing -------------------------------------------------------------

    def create_playlist(
        self,
        name: str,
        description: str | None = None,
        public: bool = False,
    ) -> str:
        result = self._client.create_playlist(
            title=name,
            description=description or "",
            privacy_status="PUBLIC" if public else "PRIVATE",
        )
        # On failure ytmusicapi returns the raw response dict instead of an ID.
        if not isinstance(result, str):
            raise ProviderError(f"YouTube Music refused to create the playlist: {result}")
        return result

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        added = 0
        for start in range(0, len(track_ids), ADD_BATCH):
            batch = track_ids[start : start + ADD_BATCH]
            try:
                self._client.add_playlist_items(
                    playlist_id,
                    batch,
                    # We have already decided these are the right tracks; let a
                    # playlist legitimately contain the same song twice.
                    duplicates=True,
                )
            except Exception as exc:
                raise ProviderError(
                    f"Failed adding {len(batch)} tracks to YouTube Music: {exc}"
                ) from exc
            added += len(batch)
        return added

    def set_cover(self, playlist_id: str, jpeg_bytes: bytes) -> bool:
        """Always False -- YouTube Music has no cover upload endpoint.

        Reported rather than raised, so the caller can tell the user where the
        downloaded image is instead of failing an otherwise successful
        migration.
        """
        return False

    # -- identity ------------------------------------------------------------

    def playlist_url(self, playlist_id: str) -> str:
        return f"https://music.youtube.com/playlist?list={self._native(playlist_id)}"

    def account_label(self) -> str | None:
        """Channel name and handle.

        The handle is the part that settles it: several Google accounts, and
        every brand account under one of them, can carry the same display
        name, and a playlist written to the wrong one of those is invisible
        from the right one.

        Whatever goes wrong here is swallowed -- this is a label, not a step.
        """
        try:
            info = self._client.get_account_info() or {}
        except Exception:
            log.debug("No YouTube Music account info", exc_info=True)
            return None
        name = info.get("accountName")
        handle = info.get("channelHandle")
        if name and handle:
            return f"{name} ({handle})"
        return name or handle or None

    @staticmethod
    def parse_playlist_ref(ref: str) -> str | None:
        ref = ref.strip()
        if ref.casefold() in {"liked", "liked songs", "liked-songs", "liked_songs"}:
            return LIKED
        if ref == LIKED_PLAYLIST:
            return LIKED
        if "list=" in ref:
            tail = ref.split("list=", 1)[1].split("&", 1)[0]
            if not tail:
                return None
            return LIKED if tail == LIKED_PLAYLIST else tail
        # YouTube playlist IDs are prefixed by kind: PL user, VL library,
        # OLAK5uy auto-generated album, RDCLAK5uy radio.
        if re.fullmatch(r"(?:PL|VL|OLAK5uy_|RDCLAK5uy_|LM)[A-Za-z0-9_-]+", ref):
            return ref
        return None
