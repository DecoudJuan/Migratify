"""Spotify error explanation.

A 403 from Spotify means one of two unrelated things, and guessing wrong sends
the user down entirely the wrong path. These tests pin the distinction, and
the parsing quirk that makes the important case easy to lose.
"""

from __future__ import annotations

import httpx

from migratify.providers.spotify import _explain_403, _spotify_message

PREMIUM_BODY = (
    "Active premium subscription required for the owner of the app. "
    "When the subscription status changes, it can take a few hours before "
    "requests are allowed again."
)


def response(status: int = 403, **kwargs) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "https://api.spotify.com/v1/me"), **kwargs)


class TestMessageExtraction:
    def test_reads_the_standard_json_error(self) -> None:
        assert (
            _spotify_message(response(json={"error": {"message": "Invalid token"}}))
            == "Invalid token"
        )

    def test_reads_a_plain_text_body(self) -> None:
        """The Premium gate replies with a bare sentence and no JSON at all.

        This is the single most important 403 to explain well, so a JSON-only
        parser would drop exactly the message the user most needs.
        """
        assert _spotify_message(response(text=PREMIUM_BODY)) == PREMIUM_BODY

    def test_survives_an_empty_body(self) -> None:
        assert _spotify_message(response(text="")) == ""

    def test_survives_an_unexpected_shape(self) -> None:
        assert _spotify_message(response(json={"unexpected": True})) is not None


class TestExplanation:
    def test_premium_gate_is_named_and_routed_to_the_sign_in_path(self) -> None:
        explanation = _explain_403(response(text=PREMIUM_BODY))

        assert "premium" in explanation.lower()
        # Re-authenticating cannot fix this, so the message must not suggest it.
        assert "migratify login spotify" in explanation
        assert "--pkce" not in explanation

    def test_premium_gate_says_it_is_not_the_session(self) -> None:
        """The user must not go hunting for a bug in their own credentials."""
        explanation = _explain_403(response(text=PREMIUM_BODY)).lower()
        assert "not a problem with your session" in explanation

    def test_an_ordinary_403_carries_spotifys_own_words(self) -> None:
        explanation = _explain_403(
            response(json={"error": {"message": "Insufficient client scope"}})
        )
        assert "Insufficient client scope" in explanation
        assert "premium" not in explanation.lower()

    def test_a_silent_403_still_produces_an_action(self) -> None:
        assert "migratify login spotify" in _explain_403(response(text=""))
