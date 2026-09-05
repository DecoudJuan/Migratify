"""Parsing for Spotify's internal GraphQL responses.

Kept separate from the provider for one reason: these shapes are the part most
likely to shift under us, and they are pure functions over dictionaries, so
they can be tested against captured fixtures without a network or a session.

Everything here is deliberately forgiving. A field that moves should degrade
into a missing value, not a ``KeyError`` in the middle of migrating a 400-track
playlist. The scorer already knows how to handle an absent duration or album;
it cannot handle a crash.
"""

from __future__ import annotations

from typing import Any

from migratify.models import Playlist, Provider, ResultKind, Track


def dig(data: Any, *path: str | int, default: Any = None) -> Any:
    """Walk a nested structure, returning ``default`` the moment it breaks.

    Accepts integer keys for list indices, so one call can cross the
    dict-and-list mixtures these responses are made of.
    """
    current = data
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, (list, tuple)) or not -len(current) <= key < len(current):
                return default
            current = current[key]
        else:
            if not isinstance(current, dict):
                return default
            current = current.get(key)
        if current is None:
            return default
    return current


def id_from_uri(uri: str | None) -> str | None:
    """``spotify:track:abc`` -> ``abc``."""
    if not uri or ":" not in uri:
        return None
    return uri.rsplit(":", 1)[-1] or None


def best_image(sources: list[dict] | None) -> str | None:
    """Highest-resolution image URL.

    Ordering is not guaranteed, so choose by area rather than position.
    """
    if not sources:
        return None
    best = max(sources, key=lambda s: (s.get("width") or 0) * (s.get("height") or 0))
    return best.get("url")


def parse_track(data: dict | None) -> Track | None:
    """A ``Track`` node from pathfinder into a neutral track.

    Returns None for anything that is not a playable track -- episodes, local
    files and removed entries all appear in real playlists.
    """
    if not isinstance(data, dict):
        return None
    if data.get("__typename") not in (None, "Track"):
        return None

    track_id = id_from_uri(data.get("uri"))
    if not track_id:
        return None

    artists: list[str] = []
    artist_ids: list[str] = []
    for artist in dig(data, "artists", "items", default=[]) or []:
        name = dig(artist, "profile", "name")
        if not name:
            continue
        artists.append(name)
        artist_id = id_from_uri(artist.get("uri"))
        if artist_id:
            artist_ids.append(artist_id)

    # Spotify reports explicitness as a content rating label rather than a flag.
    rating = (dig(data, "contentRating", "label") or "").upper()

    return Track(
        provider=Provider.SPOTIFY,
        id=track_id,
        title=data.get("name") or "",
        artists=artists,
        artist_ids=artist_ids,
        album=dig(data, "albumOfTrack", "name") or dig(data, "album", "name"),
        duration_ms=dig(data, "trackDuration", "totalMilliseconds"),
        # The internal API does not expose ISRCs. The public Web API did, and
        # losing it costs the exact-identity shortcut when Spotify is the
        # destination -- the weighted score carries those matches instead.
        isrc=None,
        explicit=rating == "EXPLICIT",
        kind=ResultKind.SONG,
        official=True,
    )


def parse_playlist_tracks(payload: dict) -> tuple[list[Track], int | None]:
    """Tracks from one page of ``fetchPlaylistContents``."""
    content = dig(payload, "playlistV2", "content", default={}) or {}
    tracks: list[Track] = []

    for item in content.get("items") or []:
        # Playlist rows wrap the track; search rows wrap it differently.
        node = dig(item, "itemV2", "data") or dig(item, "item", "data")
        track = parse_track(node)
        if track is not None:
            tracks.append(track)

    return tracks, content.get("totalCount")


def parse_search_tracks(payload: dict) -> list[Track]:
    """Tracks from a ``searchTracks`` response."""
    items = dig(payload, "searchV2", "tracksV2", "items", default=[]) or []
    tracks: list[Track] = []

    for item in items:
        node = dig(item, "item", "data") or dig(item, "data")
        track = parse_track(node)
        if track is not None:
            tracks.append(track)

    return tracks


def parse_playlist(payload: dict, playlist_id: str) -> Playlist:
    """Playlist metadata from ``fetchPlaylistMetadata`` or ``fetchPlaylist``."""
    node = payload.get("playlistV2") or {}

    images = dig(node, "images", "items", default=[]) or []
    cover = best_image(images[0].get("sources") if images else None)

    return Playlist(
        provider=Provider.SPOTIFY,
        id=playlist_id,
        name=node.get("name") or "Untitled",
        description=node.get("description") or None,
        cover_url=cover,
        track_count=dig(node, "content", "totalCount"),
        owner=dig(node, "ownerV2", "data", "name"),
        url=f"https://open.spotify.com/playlist/{playlist_id}",
    )


def parse_library(payload: dict) -> list[Playlist]:
    """Playlists from ``libraryV3``.

    The library is heterogeneous -- artists, albums and the pseudo-playlist for
    liked songs all live alongside real playlists -- so everything that is not
    a genuine playlist is filtered out here rather than surprising the caller.
    """
    library = dig(payload, "me", "libraryV3", default={}) or {}
    playlists: list[Playlist] = []

    for entry in library.get("items") or []:
        item = entry.get("item") or {}
        data = item.get("data") or {}
        if data.get("__typename") != "Playlist":
            continue

        playlist_id = id_from_uri(item.get("_uri") or data.get("uri"))
        if not playlist_id:
            continue

        images = dig(data, "images", "items", default=[]) or []
        playlists.append(
            Playlist(
                provider=Provider.SPOTIFY,
                id=playlist_id,
                name=data.get("name") or "Untitled",
                description=data.get("description") or None,
                cover_url=best_image(images[0].get("sources") if images else None),
                owner=dig(data, "ownerV2", "data", "name"),
                url=f"https://open.spotify.com/playlist/{playlist_id}",
            )
        )

    return playlists
