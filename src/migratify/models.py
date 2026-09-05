"""Provider-neutral domain models.

The matching engine works exclusively on these types. It has no idea whether a
``Track`` came from Spotify or YouTube Music, which is what lets the same
scorer serve both migration directions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class Provider(str, Enum):
    SPOTIFY = "spotify"
    YTMUSIC = "ytmusic"

    @property
    def label(self) -> str:
        return "Spotify" if self is Provider.SPOTIFY else "YouTube Music"

    @property
    def other(self) -> Provider:
        """The opposite provider -- the default destination for a migration."""
        return Provider.YTMUSIC if self is Provider.SPOTIFY else Provider.SPOTIFY


class ResultKind(str, Enum):
    """What kind of entity a search result is.

    A YouTube Music *song* carries structured artist and album metadata; a
    *video* is a plain upload and may be a cover, a live rip or a lyric video.
    The scorer prefers songs accordingly.
    """

    SONG = "song"
    VIDEO = "video"
    UNKNOWN = "unknown"


class VersionTag(str, Enum):
    """A qualifier extracted from a track title.

    These are the difference between "the song" and "a different recording of
    the song". A mismatch between source and candidate is penalized hard.
    """

    LIVE = "live"
    REMIX = "remix"
    ACOUSTIC = "acoustic"
    REMASTER = "remaster"
    INSTRUMENTAL = "instrumental"
    RADIO_EDIT = "radio_edit"
    EXTENDED = "extended"
    COVER = "cover"
    KARAOKE = "karaoke"
    SPED_UP = "sped_up"
    SLOWED = "slowed"
    DEMO = "demo"


class Decision(str, Enum):
    AUTO = "auto"
    """Confident enough to add without asking."""

    REVIEW = "review"
    """Plausible but ambiguous -- the user picks."""

    MISS = "miss"
    """No candidate cleared the floor."""

    SKIPPED = "skipped"
    """The user explicitly declined every candidate."""


class Track(BaseModel):
    """A track as it exists on some provider."""

    provider: Provider
    id: str
    title: str
    artists: list[str] = Field(default_factory=list)
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    explicit: bool = False
    release_year: int | None = None

    #: Set by providers whose results may not be real songs (YouTube Music).
    kind: ResultKind = ResultKind.SONG

    #: Provider artist/channel IDs, when available. An exact ID match between
    #: source and candidate is far stronger evidence than name similarity.
    artist_ids: list[str] = Field(default_factory=list)

    #: True when the result comes from an official artist channel or a
    #: verified release rather than an arbitrary user upload.
    official: bool = True

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def display(self) -> str:
        artists = ", ".join(self.artists) or "unknown artist"
        return f"{self.title} — {artists}"

    @property
    def duration_s(self) -> float | None:
        return self.duration_ms / 1000 if self.duration_ms else None


class Playlist(BaseModel):
    """A playlist plus the metadata we carry across: name, description, cover."""

    provider: Provider
    id: str
    name: str
    description: str | None = None
    cover_url: str | None = None
    track_count: int | None = None
    owner: str | None = None
    public: bool = False
    url: str | None = None


class ScoreBreakdown(BaseModel):
    """Per-signal detail behind a score.

    Kept alongside every candidate so that reports and the review UI can
    explain *why* a match was accepted or flagged, instead of showing a bare
    number the user has to trust.
    """

    artist: float = 0.0
    title: float = 0.0
    duration: float = 0.0
    album: float = 0.0
    kind: float = 0.0
    version_penalty: float = 0.0
    explicit_penalty: float = 0.0
    artist_vetoed: bool = False
    isrc_exact: bool = False

    def reasons(self) -> list[str]:
        """Short human-readable notes for reports and the review prompt."""
        notes: list[str] = []
        if self.isrc_exact:
            notes.append("exact ISRC match")
        if self.artist_vetoed:
            notes.append("artist mismatch (vetoed)")
        if self.version_penalty:
            notes.append(f"version mismatch ({self.version_penalty:.0f})")
        if self.duration >= 0.95:
            notes.append("duration matches")
        elif self.duration <= 0.2:
            notes.append("duration differs")
        return notes


class Candidate(BaseModel):
    """A destination track scored against a source track."""

    track: Track
    score: float = 0.0
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)

    #: Which query strategy surfaced this candidate -- useful when tuning.
    via: str = ""


class MatchResult(BaseModel):
    """The outcome of matching one source track against the destination."""

    source: Track
    candidates: list[Candidate] = Field(default_factory=list)
    decision: Decision = Decision.MISS
    chosen_id: str | None = None
    resolved_by: str = "auto"
    """``auto``, ``manual`` or ``cache``."""

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def chosen(self) -> Candidate | None:
        if self.chosen_id is None:
            return None
        return next((c for c in self.candidates if c.track.id == self.chosen_id), None)


class RunStatus(str, Enum):
    PLANNED = "planned"
    REVIEWED = "reviewed"
    APPLIED = "applied"
    FAILED = "failed"


class Run(BaseModel):
    """One migration attempt, from planning through to applying."""

    id: str
    source_provider: Provider
    target_provider: Provider
    source_playlist_id: str
    source_playlist_name: str
    target_playlist_id: str | None = None
    status: RunStatus = RunStatus.PLANNED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    total: int = 0
    auto: int = 0
    review: int = 0
    miss: int = 0

    @property
    def direction(self) -> str:
        return f"{self.source_provider.label} → {self.target_provider.label}"
