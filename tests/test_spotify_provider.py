"""Provider behaviour that is not pure shape parsing.

Offline: the web client is stubbed, so these pin the logic around it rather
than the network.
"""

from __future__ import annotations

import time

import httpx
import pytest

from migratify.auth.spotify_web import SpotifyWebClient, WebSession
from migratify.models import Playlist, Provider
from migratify.providers.base import ProviderError
from migratify.providers.spotify import PLAYLIST_CONTENTS, SpotifyProvider


class FakeClient:
    """Answers spclient metadata calls, and can be told to misbehave."""

    def __init__(self, lengths: dict[str, int], *, status: int = 200, boom: bool = False):
        self.lengths = lengths
        self.status = status
        self.boom = boom
        self.calls: list[str] = []

    def spclient(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.calls.append(path)
        if self.boom:
            raise httpx.ConnectError("no network")
        playlist_id = path.split("/")[4]
        return httpx.Response(
            self.status,
            json={"length": self.lengths.get(playlist_id), "attributes": {}},
            request=httpx.Request(method, "https://spclient.test" + path),
        )


def make_provider(client: FakeClient) -> SpotifyProvider:
    provider = object.__new__(SpotifyProvider)
    provider._client = client
    provider._user_id = "someone"
    return provider


def make_playlists(*ids: str) -> list[Playlist]:
    return [Playlist(provider=Provider.SPOTIFY, id=pid, name=pid) for pid in ids]


class TestTrackCounts:
    def test_counts_come_from_the_metadata_endpoint(self) -> None:
        client = FakeClient({"aaa": 877, "bbb": 12})
        playlists = make_playlists("aaa", "bbb")

        make_provider(client)._fill_track_counts(playlists)

        assert [p.track_count for p in playlists] == [877, 12]

    def test_it_asks_for_metadata_not_the_playlist_body(self) -> None:
        """The whole point of the endpoint choice: no track list comes back."""
        client = FakeClient({"aaa": 3})

        make_provider(client)._fill_track_counts(make_playlists("aaa"))

        assert client.calls == ["/playlist/v2/playlist/aaa/metadata"]

    @pytest.mark.parametrize(
        "client",
        [FakeClient({}, status=404), FakeClient({}, boom=True)],
        ids=["rejected", "unreachable"],
    )
    def test_a_failure_leaves_the_count_unknown(self, client: FakeClient) -> None:
        """A count is a nicety -- losing it must not lose the listing."""
        playlists = make_playlists("aaa")

        make_provider(client)._fill_track_counts(playlists)

        assert playlists[0].track_count is None


class FakeHttp:
    """Records the pathfinder payloads sent, and answers an empty page."""

    def __init__(self) -> None:
        self.operations: list[str] = []

    def post(self, url: str, headers: dict, json: dict) -> httpx.Response:
        self.operations.append(json["operationName"])
        return httpx.Response(
            200,
            json={"data": {"playlistV2": {"name": "Mix", "content": {
                "items": [], "totalCount": 0}}}},
            request=httpx.Request("POST", url),
        )


def make_client(observed: dict[str, dict]) -> tuple[SpotifyWebClient, FakeHttp]:
    client = object.__new__(SpotifyWebClient)
    client._session = WebSession(
        endpoint="https://pathfinder.test/query",
        headers={},
        operations={
            name: {"sha256": "hash", "variables": variables}
            for name, variables in observed.items()
        },
        captured_at=time.time(),
    )
    client._http = FakeHttp()
    return client, client._http


class TestOperationNames:
    """The player renames its reads; we ask for whichever one it issues.

    ``fetchPlaylistContents`` disappeared from the web player and its work now
    happens under ``fetchPlaylist``. Naming one spelling turned every Spotify
    plan into "operation was not observed", which is the same failure mode as
    pinning a hash would have been.
    """

    def test_it_uses_the_spelling_the_player_actually_issued(self) -> None:
        client, http = make_client({"fetchPlaylist": {"uri": "", "offset": 0, "limit": 25}})

        provider = object.__new__(SpotifyProvider)
        provider._client = client
        provider.get_tracks("abc")

        assert http.operations == ["fetchPlaylist"]

    def test_the_older_spelling_still_wins_when_it_is_there(self) -> None:
        """A client that still issues it is answered on its own terms."""
        client, http = make_client(
            {
                "fetchPlaylistContents": {"uri": "", "offset": 0, "limit": 100},
                "fetchPlaylist": {"uri": "", "offset": 0, "limit": 25},
            }
        )

        client.query(PLAYLIST_CONTENTS, {"uri": "spotify:playlist:abc"})

        assert http.operations == ["fetchPlaylistContents"]

    def test_a_name_we_have_never_seen_still_says_so(self, monkeypatch) -> None:
        """After recapturing once, the error names every spelling tried."""
        client, _ = make_client({"searchTracks": {}})
        monkeypatch.setattr(
            "migratify.auth.spotify_web.capture", lambda: client._session
        )

        with pytest.raises(ProviderError, match=r"fetchPlaylistContents.*fetchPlaylist"):
            client.query(PLAYLIST_CONTENTS, {"uri": "spotify:playlist:abc"})
