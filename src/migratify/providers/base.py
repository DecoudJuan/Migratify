"""The provider contract.

A provider is both a *source* and a *destination*. Implementing this protocol
completely is what earns a service a place in a migration in either direction --
there is no read-only or write-only tier.

Adding a service means implementing this here and registering it in
:mod:`migratify.providers.registry`. It must never require a change to
:mod:`migratify.matching`; if it does, the abstraction has failed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from migratify.models import Playlist, Provider, Track

#: The pseudo-playlist id for a service's own saved-tracks library --
#: "Liked Songs" on Spotify, "Liked Music" on YouTube Music.
#:
#: A saved library is not a playlist: it has no id of its own, it cannot be
#: created, and each service addresses it differently. Giving it one neutral
#: id here means the rest of Migratify -- the matcher, the store, the cache
#: key, the reports -- never learns that it is special. A provider recognizes
#: this value in :meth:`MusicProvider.get_playlist`, :meth:`get_tracks` and
#: :meth:`playlist_url`, and returns it from :meth:`parse_playlist_ref` for
#: whatever its own saved-tracks reference looks like.
#:
#: It is a *source* id only. Nothing writes into a saved library: liked songs
#: migrate into an ordinary playlist on the destination, which is one command
#: to undo. Adding several hundred tracks to someone's library is not.
LIKED = "liked"


class ProviderError(RuntimeError):
    """A provider call failed in a way the user may be able to act on."""


class AuthError(ProviderError):
    """Missing, expired or insufficiently scoped credentials.

    Raised with a message that says what to run, not what broke internally.
    """


class SearchQuery:
    """One attempt at finding a track on a destination.

    Providers emit an ordered list of these per source track. Order is
    priority: the first query that yields a decisive result short-circuits the
    rest, which is how the ISRC path avoids several wasted round trips.
    """

    __slots__ = ("decisive", "label", "result_filter", "text")

    def __init__(
        self,
        text: str,
        label: str,
        *,
        result_filter: str | None = None,
        decisive: bool = False,
    ) -> None:
        self.text = text
        self.label = label
        #: Provider-specific result filter, e.g. "songs" or "videos".
        self.result_filter = result_filter
        #: When True, a hit here is identity rather than similarity (ISRC) and
        #: the remaining queries are skipped.
        self.decisive = decisive

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SearchQuery({self.label!r}, {self.text!r})"


@runtime_checkable
class MusicProvider(Protocol):
    """Read and write access to one music service."""

    name: Provider

    #: False where the service has no API for it. YouTube Music is the case
    #: that matters: ytmusicapi exposes no playlist-cover upload at all.
    supports_cover_upload: bool

    # -- reading -------------------------------------------------------------

    def list_playlists(self, limit: int = 50) -> list[Playlist]:
        """The authenticated account's playlists."""

    def get_playlist(self, playlist_id: str) -> Playlist:
        """Playlist metadata: name, description, cover URL, owner.

        Must accept :data:`LIKED` and answer for the saved-tracks library.
        """

    def get_tracks(self, playlist_id: str) -> list[Track]:
        """Every track in the playlist, in order, paginating as needed.

        Must accept :data:`LIKED` and enumerate the saved-tracks library.
        """

    # -- searching (as a destination) ----------------------------------------

    def build_queries(self, track: Track) -> list[SearchQuery]:
        """Ordered search strategies for finding a track on this service.

        Provider-specific because query syntax is: Spotify understands
        isrc:/track:/artist: field filters, YouTube Music takes free text plus
        a result-type filter.
        """

    def search(self, query: SearchQuery, limit: int = 10) -> list[Track]:
        """Run one query and return results as neutral tracks."""

    # -- writing (as a destination) ------------------------------------------

    def create_playlist(
        self,
        name: str,
        description: str | None = None,
        public: bool = False,
    ) -> str:
        """Create an empty playlist and return its ID."""

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        """Append tracks, batching as the service requires.

        Returns the number actually added.
        """

    def set_cover(self, playlist_id: str, jpeg_bytes: bytes) -> bool:
        """Upload playlist cover art.

        Returns False when the service cannot do it, so callers can report the
        limitation instead of treating it as an error.
        """

    # -- identity ------------------------------------------------------------

    def playlist_url(self, playlist_id: str) -> str:
        """A URL a human can open."""

    @staticmethod
    def parse_playlist_ref(ref: str) -> str | None:
        """Extract a playlist ID from a URL, URI or bare ID.

        Returns :data:`LIKED` for this service's saved-tracks reference, and
        None when the reference clearly does not belong to this provider.
        """
