"""Provider behaviour that is not pure shape parsing.

Offline: the web client is stubbed, so these pin the logic around it rather
than the network.
"""

from __future__ import annotations

import httpx
import pytest

from migratify.models import Playlist, Provider
from migratify.providers.spotify import SpotifyProvider


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
