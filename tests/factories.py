"""Track factories for the offline suite.

Lives outside conftest so the test modules can import it by name; a
relative import from conftest does not work in a non-package test dir.
"""

from __future__ import annotations

from migratify.models import Provider, ResultKind, Track


def make_track(
    title: str,
    artists: list[str] | str,
    *,
    provider: Provider = Provider.SPOTIFY,
    id: str | None = None,
    album: str | None = None,
    duration_ms: int | None = None,
    isrc: str | None = None,
    explicit: bool = False,
    kind: ResultKind = ResultKind.SONG,
    official: bool = True,
    artist_ids: list[str] | None = None,
) -> Track:
    """Build a Track without repeating the boilerplate in every case."""
    if isinstance(artists, str):
        artists = [artists]
    return Track(
        provider=provider,
        id=id or f"{title}-{artists[0] if artists else 'x'}".replace(" ", "_").lower(),
        title=title,
        artists=artists,
        artist_ids=artist_ids or [],
        album=album,
        duration_ms=duration_ms,
        isrc=isrc,
        explicit=explicit,
        kind=kind,
        official=official,
    )
