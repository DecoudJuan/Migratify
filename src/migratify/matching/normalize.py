"""Title and artist normalization.

The central idea: **qualifiers are extracted, not discarded.**

Naive matchers strip everything in brackets and after a dash to get a "clean"
title, which makes `Bohemian Rhapsody` and `Bohemian Rhapsody - Live at Wembley`
look identical. They are not the same recording, and swapping one for the other
is exactly the failure this project exists to prevent.

So we split a title into two things: a comparable core, and a set of
:class:`~migratify.models.VersionTag` values. The core drives similarity, the
tags drive a hard penalty when they disagree.

Featured artists get the opposite treatment: they are moved *into* the artist
set. Spotify lists them in ``artists[]`` while YouTube Music usually buries
them in the title, so pulling them out of the title is what makes the two
services comparable at all.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from migratify.models import Track, VersionTag

# --- version tags -----------------------------------------------------------

#: Ordered so that more specific patterns win. Each maps a regex to the tag it
#: implies. Matched against the *raw* title, case-insensitively.
_VERSION_PATTERNS: list[tuple[re.Pattern[str], VersionTag]] = [
    (re.compile(r"\bkaraoke\b|\bbacking\s+track\b", re.I), VersionTag.KARAOKE),
    (re.compile(r"\binstrumental\b", re.I), VersionTag.INSTRUMENTAL),
    (re.compile(r"\bunplugged\b|\bacoustic\b|\bac[uú]stic[ao]\b", re.I), VersionTag.ACOUSTIC),
    (re.compile(r"\blive\b|\ben\s+vivo\b|\bao\s+vivo\b|\bdirecto\b", re.I), VersionTag.LIVE),
    (re.compile(r"\bremix\b|\brmx\b|\bbootleg\b|\bflip\b", re.I), VersionTag.REMIX),
    (re.compile(r"\bremaster(ed)?\b|\bremasteri[sz]ed\b", re.I), VersionTag.REMASTER),
    (re.compile(r"\bradio\s+edit\b|\bsingle\s+version\b", re.I), VersionTag.RADIO_EDIT),
    (re.compile(r"\bextended\b|\bclub\s+mix\b|\blong\s+version\b", re.I), VersionTag.EXTENDED),
    (re.compile(r"\bsped\s*up\b|\bspeed\s+up\b|\bnightcore\b", re.I), VersionTag.SPED_UP),
    (re.compile(r"\bslowed\b|\breverb\b|\bdaycore\b", re.I), VersionTag.SLOWED),
    (re.compile(r"\bcover\b|\btribute\b|\bmade\s+famous\s+by\b", re.I), VersionTag.COVER),
    (re.compile(r"\bdemo\b", re.I), VersionTag.DEMO),
]

#: Tags whose presence on only one side is a genuine recording difference. A
#: remaster is a different master of the *same* performance, so it is excluded
#: here -- penalizing it would reject correct matches constantly, since
#: services disagree on whether to label remasters at all.
SIGNIFICANT_TAGS = frozenset(
    {
        VersionTag.LIVE,
        VersionTag.REMIX,
        VersionTag.ACOUSTIC,
        VersionTag.INSTRUMENTAL,
        VersionTag.KARAOKE,
        VersionTag.COVER,
        VersionTag.SPED_UP,
        VersionTag.SLOWED,
        VersionTag.DEMO,
        VersionTag.EXTENDED,
    }
)

# --- title surgery ----------------------------------------------------------

#: "feat. X", "ft X", "with X", "con X" -- inside brackets or after a dash.
_FEAT = re.compile(
    r"""
    [\(\[\-\s]*                     # opening bracket, dash or space
    \b(?:feat|ft|featuring|con|com|with|w/)\b\.?\s*
    (?P<artists>[^\)\]\-]+)
    [\)\]]?
    """,
    re.I | re.X,
)

#: Everything a title can be decorated with that says nothing about identity.
_NOISE = re.compile(
    r"""
    \b(?:
        official(?:\s+(?:music\s+)?video|\s+audio|\s+lyric\s+video)?
      | lyrics?(?:\s+video)?
      | video\s+oficial | audio\s+oficial | letra
      | hd | hq | 4k | full\s+album | visualizer
      | explicit | clean\s+version
      | deluxe(?:\s+(?:edition|version))? | bonus\s+track
      | anniversary\s+edition | expanded\s+edition
      | mono | stereo
      | original\s+(?:mix|version|motion\s+picture\s+soundtrack)
      | album\s+version
      | \d{4}\s+remaster(?:ed)?
      | remaster(?:ed)?(?:\s+\d{4})?
      | from\s+["“][^"”]+["”]
      | topic
    )\b
    """,
    re.I | re.X,
)

_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_TRAILING_QUALIFIER = re.compile(r"\s+-\s+.*$")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

#: Suffixes YouTube Music appends to channel names for auto-generated artist
#: channels. Left in place they poison artist comparison on every single track.
_ARTIST_NOISE = re.compile(r"\s*-\s*topic\s*$|\s*\bvevo\b\s*$|\s*official\s*$", re.I)

_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\+|/|;|\bx\b|\band\b|\by\b|\be\b|\bvs\.?\b)\s*", re.I)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def basic_normalize(text: str) -> str:
    """Casefold, drop accents and punctuation, collapse whitespace."""
    text = strip_accents(text).casefold()
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def extract_version_tags(title: str) -> set[VersionTag]:
    """Version qualifiers implied by a raw title."""
    return {tag for pattern, tag in _VERSION_PATTERNS if pattern.search(title)}


def extract_featured(title: str) -> list[str]:
    """Featured artists mentioned in a title.

    ``"Otherside (feat. Dua Lipa & Elton John)"`` yields both names.
    """
    match = _FEAT.search(title)
    if not match:
        return []
    return split_artists(match.group("artists"))


def split_artists(text: str) -> list[str]:
    """Split a combined artist string into individual names.

    Conservative on purpose. Splitting too eagerly manufactures artists that do
    not exist -- "Simon and Garfunkel" must not become two people -- so a
    fragment shorter than two characters is discarded and single-token results
    are returned whole.
    """
    parts = [p.strip() for p in _ARTIST_SPLIT.split(text) if p.strip()]
    return [p for p in parts if len(p) > 1] or ([text.strip()] if text.strip() else [])


def clean_title(title: str) -> str:
    """Reduce a title to its comparable core.

    Featured artists, bracketed decorations and trailing qualifiers all go --
    but only *after* :func:`extract_version_tags` has read what it needs from
    the raw string, so nothing meaningful is lost, only relocated.
    """
    text = _FEAT.sub(" ", title)
    text = _NOISE.sub(" ", text)
    text = _BRACKETS.sub(" ", text)
    text = _TRAILING_QUALIFIER.sub(" ", text)
    cleaned = basic_normalize(text)
    # Falling through to the un-stripped title beats returning an empty string
    # for a title that is entirely bracketed, e.g. "(Don't Fear) The Reaper".
    return cleaned or basic_normalize(title)


def clean_artist(artist: str) -> str:
    return basic_normalize(_ARTIST_NOISE.sub("", artist))


@dataclass
class NormalizedTrack:
    """A track reduced to the form the scorer compares.

    Computed once per track and reused across every candidate, since
    normalization is the expensive part and the comparison is not.
    """

    title: str
    """Comparable core of the title."""

    raw_title: str
    artists: set[str] = field(default_factory=set)
    """All artists, credited and featured, normalized."""

    primary_artist: str = ""
    album: str = ""
    version_tags: set[VersionTag] = field(default_factory=set)
    duration_ms: int | None = None
    isrc: str | None = None
    artist_ids: set[str] = field(default_factory=set)

    @property
    def significant_tags(self) -> set[VersionTag]:
        return self.version_tags & SIGNIFICANT_TAGS

    @property
    def search_artist(self) -> str:
        """Best single artist name to put in a search query."""
        return self.primary_artist

    def query_text(self) -> str:
        return f"{self.title} {self.primary_artist}".strip()


def normalize_track(track: Track) -> NormalizedTrack:
    """Reduce a provider track to its comparable form."""
    featured = extract_featured(track.title)

    artists: set[str] = set()
    for name in list(track.artists) + featured:
        cleaned = clean_artist(name)
        if cleaned:
            artists.add(cleaned)
        # Credited strings are often themselves a combined list, especially on
        # YouTube Music where the whole credit can arrive as one field.
        for part in split_artists(name):
            part_clean = clean_artist(part)
            if part_clean and part_clean != cleaned:
                artists.add(part_clean)

    primary = clean_artist(track.artists[0]) if track.artists else ""

    return NormalizedTrack(
        title=clean_title(track.title),
        raw_title=track.title,
        artists=artists,
        primary_artist=primary,
        album=basic_normalize(track.album or ""),
        version_tags=extract_version_tags(track.title) | extract_version_tags(track.album or ""),
        duration_ms=track.duration_ms,
        isrc=track.isrc,
        artist_ids=set(track.artist_ids),
    )
