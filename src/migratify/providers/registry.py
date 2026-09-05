"""Provider lookup and migration-direction resolution.

The user should be able to type a playlist URL and nothing else. This module
turns that URL into a source, a destination and a playlist ID.
"""

from __future__ import annotations

import re

from migratify.models import Provider
from migratify.providers.base import MusicProvider, ProviderError

_SPOTIFY_HINTS = re.compile(r"open\.spotify\.com|spotify:", re.IGNORECASE)
_YTM_HINTS = re.compile(r"music\.youtube\.com|youtube\.com|youtu\.be", re.IGNORECASE)


def get_provider(provider: Provider) -> MusicProvider:
    """Instantiate a provider, authenticating from stored credentials.

    Imported lazily so that authenticating one service does not require
    credentials for the other to already exist.
    """
    if provider is Provider.SPOTIFY:
        from migratify.providers.spotify import SpotifyProvider

        return SpotifyProvider()
    if provider is Provider.YTMUSIC:
        from migratify.providers.ytmusic import YouTubeMusicProvider

        return YouTubeMusicProvider()
    raise ProviderError(f"Unknown provider: {provider}")


def detect_provider(ref: str) -> Provider | None:
    """Guess which service a playlist reference belongs to.

    Returns None for a bare ID, where the caller must fall back to an explicit
    --from/--to.
    """
    if _SPOTIFY_HINTS.search(ref):
        return Provider.SPOTIFY
    if _YTM_HINTS.search(ref):
        return Provider.YTMUSIC
    return None


def resolve_direction(
    ref: str,
    source: Provider | None = None,
    target: Provider | None = None,
) -> tuple[Provider, Provider]:
    """Work out which way the migration runs.

    A URL is enough on its own: the service it points at is the source, and the
    other one is the destination. Explicit flags win over detection, and a bare
    ID with no flags is ambiguous by definition -- we ask rather than guess.
    """
    detected = detect_provider(ref)

    if source is None:
        if target is not None and detected is target:
            raise ProviderError(
                f"Source and destination are both {target.label}. "
                "Migratify moves playlists between services, not within one."
            )
        source = detected or (target.other if target else None)

    if source is None:
        raise ProviderError(
            "Could not tell which service this playlist belongs to.\n"
            "Pass a full URL, or say so explicitly with --from spotify|ytmusic."
        )

    if target is None:
        target = source.other

    if source is target:
        raise ProviderError(
            f"Source and destination are both {source.label}. "
            "Migratify moves playlists between services, not within one."
        )

    return source, target


def parse_ref(ref: str, provider: Provider) -> str:
    """Extract the playlist ID from a reference for a given provider."""
    parsed = get_provider(provider).parse_playlist_ref(ref)
    if not parsed:
        raise ProviderError(f"Could not read a {provider.label} playlist ID from {ref!r}.")
    return parsed
