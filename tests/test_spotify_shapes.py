"""Parsing of Spotify's internal GraphQL responses.

These fixtures are trimmed from real responses. They exist because this is the
layer most likely to drift under us, and because drift here is silent: a field
that moves does not raise, it just produces a track with a missing value, and
the damage shows up much later as bad matching.
"""

from __future__ import annotations

from migratify.models import Provider
from migratify.providers.spotify_shapes import (
    best_image,
    dig,
    id_from_uri,
    parse_library,
    parse_playlist,
    parse_playlist_tracks,
    parse_search_tracks,
    parse_track,
)

# Playlist contents name the field trackDuration...
PLAYLIST_TRACK = {
    "__typename": "Track",
    "uri": "spotify:track:7kWPh7dvoyJ0frnnZzxZPj",
    "name": "Hurt",
    "trackDuration": {"totalMilliseconds": 216533},
    "contentRating": {"label": "NONE"},
    "artists": {
        "items": [
            {"profile": {"name": "Johnny Cash"}, "uri": "spotify:artist:6kACVPfCOnqzgfEF5ryl0x"}
        ]
    },
    "albumOfTrack": {"name": "American IV: The Man Comes Around"},
}

# ...while search results name it duration. Same value, same meaning.
SEARCH_TRACK = {
    "__typename": "Track",
    "uri": "spotify:track:7kWPh7dvoyJ0frnnZzxZPj",
    "name": "Hurt",
    "duration": {"totalMilliseconds": 216533},
    "contentRating": {"label": "EXPLICIT"},
    "artists": {
        "items": [
            {"profile": {"name": "Johnny Cash"}, "uri": "spotify:artist:6kACVPfCOnqzgfEF5ryl0x"}
        ]
    },
    "albumOfTrack": {"name": "American IV: The Man Comes Around"},
}


class TestDig:
    def test_walks_dicts(self) -> None:
        assert dig({"a": {"b": {"c": 1}}}, "a", "b", "c") == 1

    def test_crosses_lists_by_index(self) -> None:
        assert dig({"a": [{"b": 2}]}, "a", 0, "b") == 2

    def test_returns_default_the_moment_it_breaks(self) -> None:
        assert dig({"a": None}, "a", "b", default="x") == "x"
        assert dig({"a": []}, "a", 3, default="x") == "x"
        assert dig("not a dict", "a", default="x") == "x"


class TestDuration:
    """The bug that made this file necessary."""

    def test_reads_the_playlist_field_name(self) -> None:
        assert parse_track(PLAYLIST_TRACK).duration_ms == 216533

    def test_reads_the_search_field_name(self) -> None:
        """Search results use `duration`, not `trackDuration`.

        Reading only one name left every search candidate with no length,
        which disables the strongest signal the scorer has. It pushed 23 of 24
        tracks into review before anyone noticed, because nothing errored.
        """
        assert parse_track(SEARCH_TRACK).duration_ms == 216533

    def test_absent_duration_is_none_not_zero(self) -> None:
        """None means unknown; zero would be a claim about length."""
        assert parse_track({"uri": "spotify:track:x", "name": "n"}).duration_ms is None


class TestParseTrack:
    def test_extracts_artists_and_ids(self) -> None:
        track = parse_track(PLAYLIST_TRACK)
        assert track.artists == ["Johnny Cash"]
        assert track.artist_ids == ["6kACVPfCOnqzgfEF5ryl0x"]

    def test_id_comes_from_the_uri(self) -> None:
        assert parse_track(PLAYLIST_TRACK).id == "7kWPh7dvoyJ0frnnZzxZPj"
        assert parse_track(PLAYLIST_TRACK).provider is Provider.SPOTIFY

    def test_explicit_is_read_from_the_content_rating(self) -> None:
        assert parse_track(SEARCH_TRACK).explicit is True
        assert parse_track(PLAYLIST_TRACK).explicit is False

    def test_rejects_non_tracks(self) -> None:
        """Episodes and local files sit in real playlists."""
        assert parse_track({"__typename": "Episode", "uri": "spotify:episode:x"}) is None
        assert parse_track({"__typename": "Track", "name": "no uri"}) is None
        assert parse_track(None) is None


class TestCollections:
    def test_playlist_tracks_and_total(self) -> None:
        payload = {
            "playlistV2": {
                "content": {"totalCount": 42, "items": [{"itemV2": {"data": PLAYLIST_TRACK}}]}
            }
        }
        tracks, total = parse_playlist_tracks(payload)
        assert total == 42
        assert [t.title for t in tracks] == ["Hurt"]

    def test_search_tracks(self) -> None:
        payload = {"searchV2": {"tracksV2": {"items": [{"item": {"data": SEARCH_TRACK}}]}}}
        assert [t.title for t in parse_search_tracks(payload)] == ["Hurt"]

    def test_library_keeps_only_real_playlists(self) -> None:
        """Artists, albums and the liked-songs pseudo-playlist live here too."""
        payload = {
            "me": {
                "libraryV3": {
                    "items": [
                        {"item": {"_uri": "spotify:playlist:abc",
                                  "data": {"__typename": "Playlist", "name": "Mine"}}},
                        {"item": {"_uri": "spotify:artist:xyz",
                                  "data": {"__typename": "Artist", "name": "Someone"}}},
                        {"item": {"_uri": "spotify:collection:tracks",
                                  "data": {"__typename": "PseudoPlaylist", "name": "Liked"}}},
                    ]
                }
            }
        }
        playlists = parse_library(payload)
        assert [p.name for p in playlists] == ["Mine"]

    def test_playlist_metadata(self) -> None:
        payload = {
            "playlistV2": {
                "name": "Test Bench",
                "description": "hard cases",
                "content": {"totalCount": 24},
                "ownerV2": {"data": {"name": "Juan"}},
                "images": {"items": [{"sources": [
                    {"url": "small.jpg", "width": 64, "height": 64},
                    {"url": "big.jpg", "width": 640, "height": 640},
                ]}]},
            }
        }
        playlist = parse_playlist(payload, "abc")
        assert playlist.name == "Test Bench"
        assert playlist.track_count == 24
        assert playlist.owner == "Juan"
        assert playlist.cover_url == "big.jpg"

    def test_missing_fields_degrade_rather_than_raise(self) -> None:
        """A moved field must not crash a migration 400 tracks in."""
        assert parse_playlist({}, "abc").name == "Untitled"
        assert parse_playlist_tracks({}) == ([], None)
        assert parse_search_tracks({}) == []
        assert parse_library({}) == []


class TestHelpers:
    def test_id_from_uri(self) -> None:
        assert id_from_uri("spotify:track:abc") == "abc"
        assert id_from_uri("nonsense") is None
        assert id_from_uri(None) is None

    def test_best_image_picks_by_area_not_position(self) -> None:
        """Source ordering is not guaranteed."""
        sources = [
            {"url": "small.jpg", "width": 64, "height": 64},
            {"url": "big.jpg", "width": 640, "height": 640},
            {"url": "mid.jpg", "width": 300, "height": 300},
        ]
        assert best_image(sources) == "big.jpg"
        assert best_image([]) is None
        assert best_image(None) is None
