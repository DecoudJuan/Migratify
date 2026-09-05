"""Sync: re-running a migration and adding only what is new.

The whole feature is two store queries -- which destination playlist a source
playlist was migrated into, and which of its tracks actually got there. These
pin the semantics that make a second run safe to type: it must never duplicate
a track, never create a second playlist, and never treat a destination it has
not migrated to yet as if it were already up to date.
"""

from __future__ import annotations

import uuid

import pytest

from factories import make_track
from migratify.config import Thresholds
from migratify.matching.search import Matcher
from migratify.models import Provider, Run, RunStatus, Track
from migratify.store import Store
from test_pipeline import FakeProvider


@pytest.fixture
def store(tmp_path) -> Store:
    with Store(tmp_path / "sync.db") as store:
        yield store


def _run(
    target: Provider = Provider.YTMUSIC,
    playlist_id: str = "src",
    target_playlist_id: str | None = None,
) -> Run:
    return Run(
        id=uuid.uuid4().hex[:12],
        source_provider=Provider.SPOTIFY,
        target_provider=target,
        source_playlist_id=playlist_id,
        source_playlist_name="Test Playlist",
        target_playlist_id=target_playlist_id,
    )


@pytest.fixture
def catalog() -> list[Track]:
    return [
        make_track("Paranoid Android", "Radiohead", id="y1", provider=Provider.YTMUSIC,
                   album="OK Computer", duration_ms=383_000),
        make_track("Karma Police", "Radiohead", id="y2", provider=Provider.YTMUSIC,
                   album="OK Computer", duration_ms=264_000),
        make_track("No Surprises", "Radiohead", id="y3", provider=Provider.YTMUSIC,
                   album="OK Computer", duration_ms=229_000),
    ]


class TestFindingTheLink:
    def test_no_link_until_something_was_created(self, store) -> None:
        """A planned-but-never-applied run has nothing to add to."""
        store.create_run(_run())
        assert store.find_link(Provider.SPOTIFY, "src", Provider.YTMUSIC) is None

    def test_the_link_is_the_playlist_the_run_created(self, store) -> None:
        store.create_run(_run(target_playlist_id="PL_created"))
        link = store.find_link(Provider.SPOTIFY, "src", Provider.YTMUSIC)
        assert link is not None
        assert link.target_playlist_id == "PL_created"

    def test_a_link_belongs_to_one_destination_service(self, store) -> None:
        """The case a third provider makes real: already on one service, not on another.

        A playlist carried to YouTube Music needs only its new tracks there,
        and still needs the whole of itself anywhere it has never been.
        """
        store.create_run(_run(target=Provider.YTMUSIC, target_playlist_id="PL_ytm"))

        assert store.find_link(
            Provider.SPOTIFY, "src", Provider.YTMUSIC
        ).target_playlist_id == "PL_ytm"
        assert store.find_link(Provider.SPOTIFY, "src", Provider.SPOTIFY) is None

    def test_a_link_belongs_to_one_source_playlist(self, store) -> None:
        store.create_run(_run(playlist_id="src", target_playlist_id="PL_a"))
        assert store.find_link(Provider.SPOTIFY, "other", Provider.YTMUSIC) is None

    def test_a_failed_run_still_counts_as_a_link(self, store) -> None:
        """It created the playlist and left tracks in it before it broke."""
        run = _run(target_playlist_id="PL_half")
        run.status = RunStatus.FAILED
        store.create_run(run)

        assert store.find_link(
            Provider.SPOTIFY, "src", Provider.YTMUSIC
        ).target_playlist_id == "PL_half"


class TestWhatAlreadyGotThere:
    def _seed(self, store, catalog, sources, target_playlist_id="PL_created"):
        run = store.create_run(_run(target_playlist_id=target_playlist_id))
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        for position, track in enumerate(sources, start=1):
            store.save_result(run.id, position, matcher.match(track))
        return run

    def test_only_written_tracks_count(self, store, catalog) -> None:
        """Matched is not migrated; only apply makes a track real."""
        sources = [
            make_track("Paranoid Android", "Radiohead", id="s1", album="OK Computer",
                       duration_ms=383_000),
            make_track("Karma Police", "Radiohead", id="s2", album="OK Computer",
                       duration_ms=264_000),
        ]
        run = self._seed(store, catalog, sources)

        assert store.written_source_ids(
            Provider.SPOTIFY, "src", Provider.YTMUSIC, "PL_created"
        ) == set()

        store.mark_written(run.id, ["y1"])
        assert store.written_source_ids(
            Provider.SPOTIFY, "src", Provider.YTMUSIC, "PL_created"
        ) == {"s1"}

    def test_it_reads_across_every_run_that_filled_the_playlist(self, store, catalog) -> None:
        """A migration split over two attempts still knows all of what it did."""
        first = self._seed(
            store, catalog,
            [make_track("Paranoid Android", "Radiohead", id="s1", album="OK Computer",
                        duration_ms=383_000)],
        )
        store.mark_written(first.id, ["y1"])

        second = self._seed(
            store, catalog,
            [make_track("Karma Police", "Radiohead", id="s2", album="OK Computer",
                        duration_ms=264_000)],
        )
        store.mark_written(second.id, ["y2"])

        assert store.written_source_ids(
            Provider.SPOTIFY, "src", Provider.YTMUSIC, "PL_created"
        ) == {"s1", "s2"}

    def test_another_playlist_is_not_counted(self, store, catalog) -> None:
        run = self._seed(
            store, catalog,
            [make_track("Paranoid Android", "Radiohead", id="s1", album="OK Computer",
                        duration_ms=383_000)],
        )
        store.mark_written(run.id, ["y1"])

        assert store.written_source_ids(
            Provider.SPOTIFY, "src", Provider.YTMUSIC, "PL_somewhere_else"
        ) == set()


class TestSyncingASecondTime:
    def test_only_the_new_track_is_matched_and_added(self, store, catalog) -> None:
        original = [
            make_track("Paranoid Android", "Radiohead", id="s1", album="OK Computer",
                       duration_ms=383_000),
            make_track("Karma Police", "Radiohead", id="s2", album="OK Computer",
                       duration_ms=264_000),
        ]
        destination = FakeProvider(catalog)

        # First migration: everything, into a playlist it creates.
        first = store.create_run(_run())
        matcher = Matcher(destination, Thresholds())
        for position, track in enumerate(original, start=1):
            store.save_result(first.id, position, matcher.match(track))
        first.target_playlist_id = destination.create_playlist("Test Playlist")
        store.update_run(first)
        ids = [r.chosen_id for r in store.unwritten(first.id) if r.chosen_id]
        destination.add_tracks(first.target_playlist_id, ids)
        store.mark_written(first.id, ids)
        first.status = RunStatus.APPLIED
        store.update_run(first)

        assert destination.added == ["y1", "y2"]

        # The source playlist gains a track.
        grown = [
            *original,
            make_track("No Surprises", "Radiohead", id="s3", album="OK Computer",
                       duration_ms=229_000),
        ]

        # Second pass, as a sync.
        link = store.find_link(Provider.SPOTIFY, "src", Provider.YTMUSIC)
        already = store.written_source_ids(
            Provider.SPOTIFY, "src", Provider.YTMUSIC, link.target_playlist_id
        )
        fresh = [track for track in grown if track.id not in already]
        assert [t.id for t in fresh] == ["s3"]

        second = _run(target_playlist_id=link.target_playlist_id)
        store.create_run(second)
        matcher = Matcher(destination, Thresholds(), cache=store.cache_lookup(Provider.YTMUSIC))
        for position, track in enumerate(fresh, start=1):
            store.save_result(second.id, position, matcher.match(track))

        new_ids = [r.chosen_id for r in store.unwritten(second.id) if r.chosen_id]
        destination.add_tracks(second.target_playlist_id, new_ids)
        store.mark_written(second.id, new_ids)

        # One playlist, each track once.
        assert len(destination.created) == 1
        assert destination.added == ["y1", "y2", "y3"]

    def test_nothing_new_means_nothing_to_do(self, store, catalog) -> None:
        sources = [
            make_track("Paranoid Android", "Radiohead", id="s1", album="OK Computer",
                       duration_ms=383_000),
        ]
        run = store.create_run(_run(target_playlist_id="PL_created"))
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        store.save_result(run.id, 1, matcher.match(sources[0]))
        store.mark_written(run.id, ["y1"])

        already = store.written_source_ids(
            Provider.SPOTIFY, "src", Provider.YTMUSIC, "PL_created"
        )
        assert [t for t in sources if t.id not in already] == []

    def test_a_track_left_in_review_is_offered_again(self, store, catalog) -> None:
        """Unresolved is not migrated. Catalogs change, and so do minds."""
        ambiguous = make_track("Creep", "Radiohead", id="s9", duration_ms=238_000)
        run = store.create_run(_run(target_playlist_id="PL_created"))
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        store.save_result(run.id, 1, matcher.match(ambiguous))

        already = store.written_source_ids(
            Provider.SPOTIFY, "src", Provider.YTMUSIC, "PL_created"
        )
        assert ambiguous.id not in already
