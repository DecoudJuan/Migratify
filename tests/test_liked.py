"""Liked Songs as a source.

A saved library is not a playlist, and each service hides it somewhere
different. These pin the two things that make it look like one to the rest of
Migratify: the neutral ``LIKED`` id, and an enumeration that comes out in a
defensible order.

Offline -- the provider clients are stubbed.
"""

from __future__ import annotations

import httpx

from migratify.models import Provider
from migratify.providers.base import LIKED
from migratify.providers.spotify import SpotifyProvider
from migratify.providers.spotify_shapes import parse_decorated_tracks
from migratify.providers.ytmusic import LIKED_PLAYLIST, YouTubeMusicProvider


def _node(track_id: str, name: str, artist: str = "Someone") -> dict:
    return {
        "__typename": "Track",
        "uri": f"spotify:track:{track_id}",
        "name": name,
        "artists": {"items": [{"profile": {"name": artist}}]},
        "albumOfTrack": {"name": "An Album"},
        "duration": {"totalMilliseconds": 200_000},
    }


class FakeSpotifyClient:
    """Answers the collection paging call and the track decorator."""

    def __init__(self, items: list[dict], nodes: dict[str, dict] | None = None):
        self.items = items
        self.nodes = nodes or {}
        self.decorate_batches: list[list[str]] = []

    def spclient(self, method: str, path: str, **kwargs) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": self.items, "sync_token": "1"},
            request=httpx.Request(method, "https://spclient.test" + path),
        )

    def query(self, operation: str, overrides: dict | None = None) -> dict:
        assert operation == "decorateContextTracks"
        uris = (overrides or {})["uris"]
        self.decorate_batches.append(list(uris))
        return {
            "tracks": [
                self.nodes[uri] for uri in uris if uri in self.nodes
            ]
        }


def make_spotify(client: FakeSpotifyClient) -> SpotifyProvider:
    provider = object.__new__(SpotifyProvider)
    provider._client = client
    provider._user_id = "someone"
    provider._liked = None
    return provider


class TestLikedReferences:
    def test_spotify_recognises_its_own_saved_library(self) -> None:
        for ref in (
            "liked",
            "Liked Songs",
            "spotify:collection:tracks",
            "https://open.spotify.com/collection/tracks",
        ):
            assert SpotifyProvider.parse_playlist_ref(ref) == LIKED, ref

    def test_ytmusic_recognises_its_own_saved_library(self) -> None:
        for ref in (
            "liked",
            "LM",
            "https://music.youtube.com/playlist?list=LM",
        ):
            assert YouTubeMusicProvider.parse_playlist_ref(ref) == LIKED, ref

    def test_an_ordinary_playlist_is_still_an_ordinary_playlist(self) -> None:
        assert (
            SpotifyProvider.parse_playlist_ref(
                "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
            )
            == "37i9dQZF1DXcBWIGoYBM5M"
        )
        assert (
            YouTubeMusicProvider.parse_playlist_ref(
                "https://music.youtube.com/playlist?list=PLabc123"
            )
            == "PLabc123"
        )

    def test_the_neutral_id_becomes_a_url_a_human_can_open(self) -> None:
        provider = object.__new__(SpotifyProvider)
        assert provider.playlist_url(LIKED).endswith("/collection/tracks")

        ytm = object.__new__(YouTubeMusicProvider)
        assert ytm.playlist_url(LIKED).endswith(f"list={LIKED_PLAYLIST}")


class TestSpotifyLikedSongs:
    def test_saved_albums_share_the_set_and_must_not_come_through(self) -> None:
        """The collection set is mixed; only the track URIs are liked songs."""
        client = FakeSpotifyClient(
            [
                {"uri": "spotify:album:a1", "added_at": "300"},
                {"uri": "spotify:track:t1", "added_at": "200"},
                {"uri": "spotify:track:t2", "added_at": "100"},
            ]
        )
        assert make_spotify(client)._liked_uris() == [
            "spotify:track:t1",
            "spotify:track:t2",
        ]

    def test_newest_liked_first(self) -> None:
        """The service answers in an order of its own; the player shows newest first."""
        client = FakeSpotifyClient(
            [
                {"uri": "spotify:track:old", "added_at": "100"},
                {"uri": "spotify:track:new", "added_at": "900"},
                {"uri": "spotify:track:mid", "added_at": "500"},
            ]
        )
        assert make_spotify(client)._liked_uris() == [
            "spotify:track:new",
            "spotify:track:mid",
            "spotify:track:old",
        ]

    def test_a_missing_added_at_does_not_raise(self) -> None:
        client = FakeSpotifyClient(
            [
                {"uri": "spotify:track:t1"},
                {"uri": "spotify:track:t2", "added_at": "500"},
            ]
        )
        assert make_spotify(client)._liked_uris() == [
            "spotify:track:t2",
            "spotify:track:t1",
        ]

    def test_metadata_keeps_the_order_we_asked_for(self) -> None:
        """The decorator is not promised to answer in request order."""
        uris = [f"spotify:track:t{n}" for n in range(1, 4)]
        client = FakeSpotifyClient(
            [{"uri": uri, "added_at": str(900 - i)} for i, uri in enumerate(uris)],
            nodes={uri: _node(uri.rsplit(":", 1)[-1], f"Song {uri[-1]}") for uri in uris},
        )
        # Answer in reverse, to prove the caller imposes the order.
        client.query = lambda op, overrides=None: {  # type: ignore[method-assign]
            "tracks": [client.nodes[u] for u in reversed(overrides["uris"])]
        }

        tracks = make_spotify(client)._liked_tracks()
        assert [t.id for t in tracks] == ["t1", "t2", "t3"]

    def test_a_track_that_fails_to_decorate_drops_out(self) -> None:
        """One unreadable entry must not shift everything after it."""
        client = FakeSpotifyClient(
            [
                {"uri": "spotify:track:t1", "added_at": "300"},
                {"uri": "spotify:track:gone", "added_at": "200"},
                {"uri": "spotify:track:t3", "added_at": "100"},
            ],
            nodes={
                "spotify:track:t1": _node("t1", "First"),
                "spotify:track:t3": _node("t3", "Third"),
            },
        )
        assert [t.title for t in make_spotify(client)._liked_tracks()] == ["First", "Third"]

    def test_the_collection_is_read_once_for_the_whole_command(self) -> None:
        """plan asks for the playlist and then its tracks; that is one request."""
        client = FakeSpotifyClient(
            [{"uri": "spotify:track:t1", "added_at": "1"}],
            nodes={"spotify:track:t1": _node("t1", "First")},
        )
        provider = make_spotify(client)
        calls: list[str] = []
        original = client.spclient
        client.spclient = lambda *a, **k: (calls.append(a[1]), original(*a, **k))[1]

        playlist = provider.get_playlist(LIKED)
        tracks = provider.get_tracks(LIKED)

        assert playlist.id == LIKED
        assert playlist.name == "Liked Songs"
        assert playlist.track_count == 1
        assert [t.id for t in tracks] == ["t1"]
        assert len(calls) == 1

    def test_batches_stay_the_size_the_player_uses(self) -> None:
        uris = [f"spotify:track:t{n:03d}" for n in range(120)]
        client = FakeSpotifyClient(
            [{"uri": uri, "added_at": str(1000 - i)} for i, uri in enumerate(uris)],
            nodes={uri: _node(uri.rsplit(":", 1)[-1], "Song") for uri in uris},
        )
        tracks = make_spotify(client)._liked_tracks()

        assert len(tracks) == 120
        assert [len(batch) for batch in client.decorate_batches] == [50, 50, 20]


class TestDecoratedTracks:
    def test_keyed_by_id(self) -> None:
        payload = {"tracks": [_node("t1", "One"), _node("t2", "Two")]}
        parsed = parse_decorated_tracks(payload)
        assert set(parsed) == {"t1", "t2"}
        assert parsed["t1"].provider is Provider.SPOTIFY
        assert parsed["t1"].duration_ms == 200_000

    def test_an_empty_response_is_not_an_error(self) -> None:
        assert parse_decorated_tracks({}) == {}
        assert parse_decorated_tracks({"tracks": None}) == {}


class TestYouTubeMusicLikedSongs:
    def test_the_neutral_id_is_translated_at_the_edge(self) -> None:
        assert YouTubeMusicProvider._native(LIKED) == LIKED_PLAYLIST
        assert YouTubeMusicProvider._native("PLabc") == "PLabc"

    def test_the_neutral_id_comes_back_out(self) -> None:
        """The store and the cache must key on LIKED, not on LM."""
        provider = object.__new__(YouTubeMusicProvider)
        provider._client = type(
            "C", (), {"get_playlist": staticmethod(
                lambda pid, limit=None: {"title": "Liked Music", "trackCount": 850}
            )}
        )()

        playlist = provider.get_playlist(LIKED)
        assert playlist.id == LIKED
        assert playlist.name == "Liked Music"
        assert playlist.track_count == 850
