"""Who a write lands as.

A playlist created in the wrong Google account or Spotify profile is
indistinguishable from one that was never created: the URL opens to nothing.
So both providers have to be able to name the account, and neither is allowed
to turn a missing name into a failed migration.
"""

from __future__ import annotations

from migratify.providers.base import ProviderError
from migratify.providers.spotify import SpotifyProvider
from migratify.providers.ytmusic import YouTubeMusicProvider


class FakeSpotifyClient:
    def __init__(self, profile: dict | None, *, boom: bool = False):
        self.profile = profile
        self.boom = boom
        self.calls = 0

    def query(self, operation, overrides=None) -> dict:
        self.calls += 1
        if self.boom:
            raise ProviderError("session expired")
        return {"me": {"profile": self.profile or {}}}


def make_spotify(client: FakeSpotifyClient) -> SpotifyProvider:
    provider = object.__new__(SpotifyProvider)
    provider._client = client
    provider._user_id = None
    provider._profile_data = None
    provider._liked = None
    return provider


class FakeYTMClient:
    def __init__(self, info: dict | None, *, boom: bool = False):
        self.info = info
        self.boom = boom

    def get_account_info(self) -> dict | None:
        if self.boom:
            raise RuntimeError("no session")
        return self.info


def make_ytmusic(client: FakeYTMClient) -> YouTubeMusicProvider:
    provider = object.__new__(YouTubeMusicProvider)
    provider._client = client
    return provider


class TestSpotifyAccount:
    def test_it_names_the_display_name_and_the_username(self) -> None:
        provider = make_spotify(
            FakeSpotifyClient({"name": "Juan D", "username": "chiche05-ar"})
        )

        assert provider.account_label() == "Juan D (chiche05-ar)"

    def test_the_profile_is_read_once_for_both_uses(self) -> None:
        """The id for the collection service and the label are one call."""
        client = FakeSpotifyClient(
            {"name": "Juan D", "username": "chiche05-ar", "uri": "spotify:user:chiche05-ar"}
        )
        provider = make_spotify(client)

        provider.account_label()
        assert provider._me() == "chiche05-ar"
        assert client.calls == 1

    def test_a_broken_session_costs_the_label_not_the_run(self) -> None:
        assert make_spotify(FakeSpotifyClient(None, boom=True)).account_label() is None


class TestYouTubeMusicAccount:
    def test_it_names_the_channel_and_its_handle(self) -> None:
        """The handle is what separates two accounts with the same name."""
        provider = make_ytmusic(
            FakeYTMClient({"accountName": "Juan Manuel Decoud", "channelHandle": "@juan-d7c"})
        )

        assert provider.account_label() == "Juan Manuel Decoud (@juan-d7c)"

    def test_half_an_answer_is_still_an_answer(self) -> None:
        provider = make_ytmusic(FakeYTMClient({"accountName": "Juan Manuel Decoud"}))

        assert provider.account_label() == "Juan Manuel Decoud"

    def test_no_answer_is_not_an_error(self) -> None:
        assert make_ytmusic(FakeYTMClient(None, boom=True)).account_label() is None
        assert make_ytmusic(FakeYTMClient({})).account_label() is None
