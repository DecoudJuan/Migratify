"""The golden set.

Every case here is a real way playlist migration goes wrong. The suite exists
to make sure a change to normalization or weights does not quietly reopen one
of them.

If a case legitimately needs to change, change it in the same commit as the
code and say why in the message.
"""

from __future__ import annotations

from factories import make_track
from migratify.matching.normalize import normalize_track
from migratify.matching.score import decide, indistinguishable, rank, score_candidate
from migratify.models import Decision, Provider, ResultKind

MIN = 60_000


def score_of(source, candidate) -> float:
    return score_candidate(normalize_track(source), candidate).score


class TestTheRightSong:
    def test_exact_match_scores_very_high(self) -> None:
        source = make_track("Bohemian Rhapsody", "Queen", album="A Night at the Opera",
                            duration_ms=5 * MIN + 55_000)
        candidate = make_track("Bohemian Rhapsody", "Queen", provider=Provider.YTMUSIC,
                               album="A Night at the Opera", duration_ms=5 * MIN + 55_000)
        assert score_of(source, candidate) >= 95

    def test_survives_a_decorated_youtube_title(self) -> None:
        source = make_track("Blinding Lights", "The Weeknd", duration_ms=200_000)
        candidate = make_track(
            "Blinding Lights (Official Video)", "The Weeknd - Topic",
            provider=Provider.YTMUSIC, duration_ms=200_000,
        )
        assert score_of(source, candidate) >= 88

    def test_survives_a_displaced_feature_credit(self) -> None:
        source = make_track("Cold Heart", ["Elton John", "Dua Lipa"], duration_ms=202_000)
        candidate = make_track(
            "Cold Heart (feat. Dua Lipa)", "Elton John",
            provider=Provider.YTMUSIC, duration_ms=202_000,
        )
        assert score_of(source, candidate) >= 88

    def test_isrc_short_circuits_to_certainty(self) -> None:
        """Same recording by definition, even when the metadata disagrees."""
        source = make_track("Song", "Artist", isrc="USUM71703861", duration_ms=180_000)
        candidate = make_track(
            "Song (2019 Remaster)", "The Artist", isrc="usum71703861",
            provider=Provider.YTMUSIC, duration_ms=181_000,
        )
        assert score_of(source, candidate) == 100


class TestTheWrongSong:
    def test_same_title_different_artist_is_vetoed(self) -> None:
        """The single commonest failure mode in playlist migration."""
        source = make_track("Hurt", "Johnny Cash", duration_ms=216_000)
        candidate = make_track("Hurt", "Nine Inch Nails", provider=Provider.YTMUSIC,
                               duration_ms=373_000)

        result = score_candidate(normalize_track(source), candidate)
        assert result.breakdown.artist_vetoed
        assert result.score <= 40

    def test_a_vetoed_match_cannot_even_reach_review(self, thresholds) -> None:
        source = make_track("Crazy", "Gnarls Barkley", duration_ms=178_000)
        candidates = rank(source, [(make_track("Crazy", "Aerosmith",
                                               provider=Provider.YTMUSIC,
                                               duration_ms=316_000), "song")])
        assert decide(source, candidates, thresholds).decision is Decision.MISS

    def test_live_version_is_penalized_out_of_auto_accept(self, thresholds) -> None:
        source = make_track("Wonderwall", "Oasis", duration_ms=258_000)
        candidate = make_track("Wonderwall - Live at Wembley", "Oasis",
                               provider=Provider.YTMUSIC, duration_ms=262_000)

        result = decide(source, rank(source, [(candidate, "song")]), thresholds)
        assert result.decision is not Decision.AUTO

    def test_karaoke_is_rejected(self, thresholds) -> None:
        source = make_track("Yesterday", "The Beatles", duration_ms=125_000)
        candidate = make_track("Yesterday (Karaoke Version)", "The Beatles",
                               provider=Provider.YTMUSIC, duration_ms=126_000)

        result = decide(source, rank(source, [(candidate, "song")]), thresholds)
        assert result.decision is not Decision.AUTO

    def test_a_cover_at_the_wrong_length_is_caught_by_duration(self) -> None:
        """Duration is the signal neither service can spin."""
        source = make_track("Creep", "Radiohead", duration_ms=238_000)
        cover = make_track("Creep", "Radiohead", provider=Provider.YTMUSIC,
                           duration_ms=310_000, kind=ResultKind.VIDEO, official=False)

        assert score_of(source, cover) < 88

    def test_remix_does_not_match_the_original(self, thresholds) -> None:
        source = make_track("Levels", "Avicii", duration_ms=201_000)
        candidate = make_track("Levels - Skrillex Remix", "Avicii",
                               provider=Provider.YTMUSIC, duration_ms=205_000)

        result = decide(source, rank(source, [(candidate, "song")]), thresholds)
        assert result.decision is not Decision.AUTO

    def test_sped_up_edit_does_not_match(self, thresholds) -> None:
        source = make_track("Say It Right", "Nelly Furtado", duration_ms=189_000)
        candidate = make_track("Say It Right (sped up)", "Nelly Furtado",
                               provider=Provider.YTMUSIC, duration_ms=160_000)

        result = decide(source, rank(source, [(candidate, "song")]), thresholds)
        assert result.decision is not Decision.AUTO


class TestPreferences:
    def test_a_catalog_song_outranks_an_equivalent_upload(self) -> None:
        source = make_track("Take On Me", "a-ha", duration_ms=225_000)
        song = make_track("Take On Me", "a-ha", id="song", provider=Provider.YTMUSIC,
                          duration_ms=225_000, kind=ResultKind.SONG,
                          artist_ids=["UC_aha"])
        video = make_track("Take On Me", "a-ha", id="video", provider=Provider.YTMUSIC,
                           duration_ms=225_000, kind=ResultKind.VIDEO, official=False)

        ranked = rank(source, [(video, "video"), (song, "song")])
        assert ranked[0].track.id == "song"

    def test_artist_id_overlap_is_conclusive(self) -> None:
        """Same catalog entity, not a lookalike name."""
        source = make_track("Untitled", "Fugazi", artist_ids=["A1"], duration_ms=180_000)
        candidate = make_track("Untitled", "FUGAZI (Official)", artist_ids=["A1"],
                               provider=Provider.YTMUSIC, duration_ms=180_000)

        assert score_candidate(normalize_track(source), candidate).breakdown.artist == 1.0

    def test_missing_duration_neither_rescues_nor_sinks(self) -> None:
        """YouTube Music omits duration sometimes; absence is not evidence."""
        source = make_track("Song", "Artist", duration_ms=180_000)
        no_duration = make_track("Song", "Artist", provider=Provider.YTMUSIC)

        breakdown = score_candidate(normalize_track(source), no_duration).breakdown
        assert breakdown.duration == 0.5


class TestDecisions:
    def test_a_clear_winner_is_auto_accepted(self, thresholds) -> None:
        source = make_track("Paranoid Android", "Radiohead", album="OK Computer",
                            duration_ms=383_000)
        candidate = make_track("Paranoid Android", "Radiohead", album="OK Computer",
                               provider=Provider.YTMUSIC, duration_ms=383_000)

        result = decide(source, rank(source, [(candidate, "song")]), thresholds)
        assert result.decision is Decision.AUTO
        assert result.chosen_id == candidate.id

    def test_a_near_tie_goes_to_review_rather_than_a_coin_flip(self, thresholds) -> None:
        """Close scores on genuinely different cuts means the scorer cannot
        tell them apart. Taking the higher one would be a guess wearing a
        number.

        Three seconds apart, so these are two different masters rather than
        the same recording listed twice -- see TestDuplicateListings for the
        case that is deliberately *not* a tie.
        """
        source = make_track("Alive", "Pearl Jam", album="Ten", duration_ms=340_000)
        first = make_track("Alive", "Pearl Jam", id="a", album="Ten",
                           provider=Provider.YTMUSIC, duration_ms=340_000)
        second = make_track("Alive", "Pearl Jam", id="b", album="Ten",
                            provider=Provider.YTMUSIC, duration_ms=343_000)

        result = decide(source, rank(source, [(first, "song"), (second, "song")]), thresholds)
        assert result.decision is Decision.REVIEW
        assert result.chosen_id is None

    def test_nothing_found_is_a_miss_not_a_bad_guess(self, thresholds) -> None:
        source = make_track("Something Obscure", "Nobody At All", duration_ms=200_000)
        assert decide(source, [], thresholds).decision is Decision.MISS

    def test_review_keeps_candidates_for_the_user_to_choose_from(self, thresholds) -> None:
        source = make_track("Alive", "Pearl Jam", duration_ms=340_000)
        candidates = [
            (make_track("Alive", "Pearl Jam", id=f"c{i}", provider=Provider.YTMUSIC,
                        duration_ms=340_000 + i * 3_000), "song")
            for i in range(8)
        ]
        result = decide(source, rank(source, candidates), thresholds)
        assert 0 < len(result.candidates) <= thresholds.max_candidates_shown


class TestDuplicateListings:
    """Catalogs list the same recording many times.

    The album cut, the single, the greatest-hits entry and several regional
    releases are the same audio and all surface in one search, scoring within a
    point of each other. Treating that as a tie buries the genuinely ambiguous
    cases in noise and trains the user to approve without looking.
    """

    def test_same_recording_listed_twice_is_auto_accepted(self, thresholds) -> None:
        source = make_track("Hurt", "Johnny Cash", album="American IV", duration_ms=217_000)
        album_cut = make_track("Hurt", "Johnny Cash", id="a", album="American IV",
                               provider=Provider.YTMUSIC, duration_ms=217_000)
        compilation = make_track("Hurt", "Johnny Cash", id="b", album="The Legend",
                                 provider=Provider.YTMUSIC, duration_ms=217_400)

        result = decide(source, rank(source, [(album_cut, "song"), (compilation, "song")]),
                        thresholds)
        assert result.decision is Decision.AUTO
        assert result.chosen_id is not None

    def test_a_different_length_is_still_a_tie(self, thresholds) -> None:
        """Three seconds apart is a different cut, not a duplicate listing."""
        source = make_track("Crazy", "Gnarls Barkley", duration_ms=178_000)
        album = make_track("Crazy", "Gnarls Barkley", id="a",
                           provider=Provider.YTMUSIC, duration_ms=178_000)
        single = make_track("Crazy", "Gnarls Barkley", id="b",
                            provider=Provider.YTMUSIC, duration_ms=181_000)

        assert not indistinguishable(
            *rank(source, [(album, "song"), (single, "song")])[:2]
        )

    def test_a_different_version_is_still_a_tie(self, thresholds) -> None:
        source = make_track("Alive", "Pearl Jam", duration_ms=340_000)
        studio = make_track("Alive", "Pearl Jam", id="a",
                            provider=Provider.YTMUSIC, duration_ms=340_000)
        live = make_track("Alive (Live)", "Pearl Jam", id="b",
                          provider=Provider.YTMUSIC, duration_ms=340_500)

        ranked = rank(source, [(studio, "song"), (live, "song")])
        assert not indistinguishable(ranked[0], ranked[1])

    def test_a_different_artist_is_still_a_tie(self, thresholds) -> None:
        """The whole point is to collapse duplicates, never different artists."""
        source = make_track("Hurt", "Johnny Cash", duration_ms=217_000)
        cash = make_track("Hurt", "Johnny Cash", id="a",
                          provider=Provider.YTMUSIC, duration_ms=217_000)
        other = make_track("Hurt", "Nine Inch Nails", id="b",
                           provider=Provider.YTMUSIC, duration_ms=217_000)

        ranked = rank(source, [(cash, "song"), (other, "song")])
        assert not indistinguishable(ranked[0], ranked[1])

    def test_unknown_length_is_never_called_a_duplicate(self, thresholds) -> None:
        source = make_track("Song", "Artist", duration_ms=200_000)
        first = make_track("Song", "Artist", id="a", provider=Provider.YTMUSIC)
        second = make_track("Song", "Artist", id="b", provider=Provider.YTMUSIC)

        ranked = rank(source, [(first, "song"), (second, "song")])
        assert not indistinguishable(ranked[0], ranked[1])
