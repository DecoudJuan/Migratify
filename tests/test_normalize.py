"""Normalization behaviour.

These assertions are the contract the scorer is built on. If one of them has to
change, the scoring weights probably need revisiting too.
"""

from __future__ import annotations

import pytest

from factories import make_track
from migratify.matching.normalize import (
    clean_artist,
    clean_title,
    extract_featured,
    extract_version_tags,
    normalize_track,
    split_artists,
)
from migratify.models import VersionTag


class TestCleanTitle:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Bohemian Rhapsody", "bohemian rhapsody"),
            ("Bohemian Rhapsody - Remastered 2011", "bohemian rhapsody"),
            ("Blinding Lights (Official Video)", "blinding lights"),
            ("Levitating (feat. DaBaby)", "levitating"),
            ("Shape of You [Official Music Video]", "shape of you"),
            ("Nothing Else Matters (Remastered)", "nothing else matters"),
            ("Take Five - Live", "take five"),
            ("Corazón Espinado", "corazon espinado"),
            ("Sk8er Boi", "sk8er boi"),
        ],
    )
    def test_reduces_to_comparable_core(self, raw: str, expected: str) -> None:
        assert clean_title(raw) == expected

    def test_fully_bracketed_title_survives(self) -> None:
        """A title that is entirely bracketed must not normalize to nothing.

        An empty core would match every other track equally well, which is the
        worst possible failure mode for a matcher.
        """
        assert clean_title("(Don't Fear) The Reaper").strip()

    def test_case_and_accents_do_not_matter(self) -> None:
        assert clean_title("DÉJÀ VU") == clean_title("deja vu")


class TestVersionTags:
    @pytest.mark.parametrize(
        ("raw", "tag"),
        [
            ("Wonderwall - Live at Wembley", VersionTag.LIVE),
            ("Wonderwall (Acoustic)", VersionTag.ACOUSTIC),
            ("Levels - Skrillex Remix", VersionTag.REMIX),
            ("Yesterday (Karaoke Version)", VersionTag.KARAOKE),
            ("Clocks - Instrumental", VersionTag.INSTRUMENTAL),
            ("Say It Right (sped up)", VersionTag.SPED_UP),
            ("Cruel Summer (slowed + reverb)", VersionTag.SLOWED),
            ("Layla - Radio Edit", VersionTag.RADIO_EDIT),
            ("Creep (Cover)", VersionTag.COVER),
            ("En Vivo en el Luna Park", VersionTag.LIVE),
        ],
    )
    def test_detects_qualifier(self, raw: str, tag: VersionTag) -> None:
        assert tag in extract_version_tags(raw)

    def test_plain_title_has_no_tags(self) -> None:
        assert extract_version_tags("Smells Like Teen Spirit") == set()

    def test_remaster_is_detected_but_not_significant(self) -> None:
        """Remasters are labelled inconsistently across services.

        We still detect the tag, but it must not be significant -- penalizing
        it would reject correct matches constantly.
        """
        track = make_track("Come Together - Remastered 2009", "The Beatles")
        norm = normalize_track(track)
        assert VersionTag.REMASTER in norm.version_tags
        assert VersionTag.REMASTER not in norm.significant_tags


class TestArtists:
    def test_featured_artists_are_extracted_from_the_title(self) -> None:
        assert "dua lipa" in [a.casefold() for a in extract_featured("Cold Heart (feat. Dua Lipa)")]

    def test_topic_channel_suffix_is_stripped(self) -> None:
        """YouTube Music appends this to every auto-generated artist channel."""
        assert clean_artist("Radiohead - Topic") == "radiohead"

    def test_vevo_suffix_is_stripped(self) -> None:
        assert clean_artist("ArianaGrandeVevo") == "arianagrandevevo"
        assert clean_artist("Coldplay VEVO") == "coldplay"

    def test_splits_a_combined_credit(self) -> None:
        assert set(split_artists("Calvin Harris & Dua Lipa")) == {"Calvin Harris", "Dua Lipa"}

    def test_does_not_invent_artists(self) -> None:
        """Splitting too eagerly manufactures people who do not exist."""
        assert split_artists("Florence + The Machine") != []

    def test_featured_artist_lands_in_the_artist_set(self) -> None:
        """Spotify puts features in artists[]; YouTube Music buries them in the
        title. Relocating them is what makes the two comparable."""
        spotify_side = make_track("Cold Heart", ["Elton John", "Dua Lipa"])
        ytm_side = make_track("Cold Heart (feat. Dua Lipa)", ["Elton John"])

        assert normalize_track(spotify_side).artists == normalize_track(ytm_side).artists
