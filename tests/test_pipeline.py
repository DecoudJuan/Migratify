"""End-to-end pipeline over a fake provider.

Exercises the real Matcher, Store and report code against an in-memory
provider, so the wiring is covered without credentials or network. The fake
implements the same MusicProvider contract the real ones do -- if this passes
and a real provider fails, the bug is in that provider, not the pipeline.
"""

from __future__ import annotations

import uuid

import pytest

from factories import make_track
from migratify.config import Thresholds
from migratify.matching.search import Matcher, accepted_ids, pending_review
from migratify.models import Decision, Provider, Run, Track
from migratify.providers.base import SearchQuery
from migratify.report import to_csv, to_json, to_markdown
from migratify.store import Store


class FakeProvider:
    """A destination whose whole catalog is a list of tracks."""

    name = Provider.YTMUSIC
    supports_cover_upload = False

    def __init__(self, catalog: list[Track]) -> None:
        self.catalog = catalog
        self.created: list[tuple[str, str | None, bool]] = []
        self.added: list[str] = []
        self.searches: list[str] = []

    def build_queries(self, track: Track) -> list[SearchQuery]:
        return [SearchQuery(f"{track.title} {track.artists[0]}", "song", result_filter="songs")]

    def search(self, query: SearchQuery, limit: int = 10) -> list[Track]:
        self.searches.append(query.text)
        # Crude on purpose: the point is to feed the real scorer, not to be a
        # good search engine.
        words = {w for w in query.text.lower().split() if len(w) > 2}
        hits = [
            t for t in self.catalog
            if words & set(f"{t.title} {' '.join(t.artists)}".lower().split())
        ]
        return hits[:limit]

    def create_playlist(self, name, description=None, public=False) -> str:
        self.created.append((name, description, public))
        return "PL_fake"

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        self.added.extend(track_ids)
        return len(track_ids)

    def set_cover(self, playlist_id: str, jpeg_bytes: bytes) -> bool:
        return False

    def playlist_url(self, playlist_id: str) -> str:
        return f"https://music.youtube.com/playlist?list={playlist_id}"

    def list_playlists(self, limit: int = 50):
        return []

    def get_playlist(self, playlist_id: str):
        raise NotImplementedError

    def get_tracks(self, playlist_id: str):
        raise NotImplementedError

    @staticmethod
    def parse_playlist_ref(ref: str) -> str | None:
        return ref


@pytest.fixture
def catalog() -> list[Track]:
    return [
        make_track("Paranoid Android", "Radiohead", id="y1", provider=Provider.YTMUSIC,
                   album="OK Computer", duration_ms=383_000),
        make_track("Karma Police", "Radiohead", id="y2", provider=Provider.YTMUSIC,
                   album="OK Computer", duration_ms=264_000),
        # A trap: right title, wrong artist. Must never be chosen.
        make_track("Creep", "Vega Cover Band", id="y3", provider=Provider.YTMUSIC,
                   duration_ms=241_000),
        # A genuine ambiguity: the album cut and the single, indistinguishable
        # on every signal the scorer has. This is what review exists for.
        make_track("Creep", "Radiohead", id="y4", provider=Provider.YTMUSIC,
                   album="Pablo Honey", duration_ms=238_000),
        make_track("Creep", "Radiohead", id="y5", provider=Provider.YTMUSIC,
                   album="Pablo Honey", duration_ms=238_400),
    ]


@pytest.fixture
def sources() -> list[Track]:
    return [
        make_track("Paranoid Android", "Radiohead", id="s1", album="OK Computer",
                   duration_ms=383_000),
        make_track("Karma Police", "Radiohead", id="s2", album="OK Computer",
                   duration_ms=264_000),
        make_track("Creep", "Radiohead", id="s3", album="Pablo Honey", duration_ms=238_000),
        make_track("A Song Nobody Has", "Unknown Artist", id="s4", duration_ms=200_000),
    ]


@pytest.fixture
def store(tmp_path) -> Store:
    with Store(tmp_path / "test.db") as store:
        yield store


def _run() -> Run:
    return Run(
        id=uuid.uuid4().hex[:12],
        source_provider=Provider.SPOTIFY,
        target_provider=Provider.YTMUSIC,
        source_playlist_id="src",
        source_playlist_name="Test Playlist",
    )


class TestPipeline:
    def test_matches_what_exists_and_refuses_what_does_not(self, catalog, sources) -> None:
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        results = matcher.match_all(sources)

        by_id = {r.source.id: r for r in results}
        assert by_id["s1"].decision is Decision.AUTO
        assert by_id["s2"].decision is Decision.AUTO
        # The cover band must never be accepted as Radiohead.
        assert by_id["s3"].chosen_id is None
        assert by_id["s4"].decision is Decision.MISS

    def test_only_accepted_tracks_are_written(self, catalog, sources) -> None:
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        results = matcher.match_all(sources)

        target = FakeProvider(catalog)
        target.add_tracks("PL_fake", accepted_ids(results))

        assert set(target.added) == {"y1", "y2"}
        assert "y3" not in target.added

    def test_results_survive_a_round_trip_through_the_store(self, store, catalog, sources) -> None:
        run = store.create_run(_run())
        matcher = Matcher(FakeProvider(catalog), Thresholds())

        for position, track in enumerate(sources, start=1):
            store.save_result(run.id, position, matcher.match(track))

        reloaded = store.load_results(run.id)
        assert len(reloaded) == len(sources)
        assert [r.source.id for r in reloaded] == [t.id for t in sources]
        assert [r.decision for r in reloaded] == [
            Decision.AUTO, Decision.AUTO, Decision.REVIEW, Decision.MISS
        ]

    def test_counters_are_recounted_not_incremented(self, store, catalog, sources) -> None:
        run = store.create_run(_run())
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        for position, track in enumerate(sources, start=1):
            store.save_result(run.id, position, matcher.match(track))

        store.refresh_counters(run.id)
        assert store.get_run(run.id).auto == 2

        # Resolving a review must move the counter, not leave it stale.
        results = store.load_results(run.id)
        review_index = next(
            i for i, r in enumerate(results, start=1) if r.decision is Decision.REVIEW
        )
        target = results[review_index - 1]
        target.decision = Decision.AUTO
        target.chosen_id = target.candidates[0].track.id
        store.save_result(run.id, review_index, target)
        store.refresh_counters(run.id)

        assert store.get_run(run.id).auto == 3
        assert store.get_run(run.id).review == 0


class TestCache:
    def test_a_resolved_track_is_never_searched_twice(self, store, catalog, sources) -> None:
        run = store.create_run(_run())
        provider = FakeProvider(catalog)
        matcher = Matcher(provider, Thresholds(), cache=store.cache_lookup(Provider.YTMUSIC))

        for position, track in enumerate(sources, start=1):
            store.save_result(run.id, position, matcher.match(track))
        first_pass = len(provider.searches)

        for track in sources:
            matcher.match(track)

        # Only the unresolved tracks are searched again.
        assert len(provider.searches) - first_pass < first_pass

    def test_the_cache_is_direction_keyed(self, store, catalog, sources) -> None:
        """A Spotify to YouTube Music match must not answer the reverse lookup."""
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        result = matcher.match(sources[0])
        store.save_result(store.create_run(_run()).id, 1, result)

        assert store.cache_lookup(Provider.YTMUSIC)(sources[0]) is not None
        assert store.cache_lookup(Provider.SPOTIFY)(sources[0]) is None

    def test_misses_are_not_cached(self, store, catalog, sources) -> None:
        """Catalogs change; a track absent today may exist next month."""
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        missing = sources[3]
        store.save_result(store.create_run(_run()).id, 1, matcher.match(missing))

        assert store.cache_lookup(Provider.YTMUSIC)(missing) is None


class TestIdempotency:
    def test_applying_twice_writes_each_track_once(self, store, catalog, sources) -> None:
        run = store.create_run(_run())
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        for position, track in enumerate(sources, start=1):
            store.save_result(run.id, position, matcher.match(track))

        target = FakeProvider(catalog)

        first = [r.chosen_id for r in store.unwritten(run.id) if r.chosen_id]
        target.add_tracks("PL_fake", first)
        store.mark_written(run.id, first)

        second = [r.chosen_id for r in store.unwritten(run.id) if r.chosen_id]
        target.add_tracks("PL_fake", second)

        assert second == []
        assert len(target.added) == len(set(target.added))


class TestReports:
    @pytest.fixture
    def rendered(self, catalog, sources):
        matcher = Matcher(FakeProvider(catalog), Thresholds())
        return _run(), matcher.match_all(sources)

    def test_markdown_names_every_track(self, rendered) -> None:
        run, results = rendered
        markdown = to_markdown(run, results)
        for result in results:
            assert result.source.title in markdown

    def test_markdown_separates_the_three_outcomes(self, rendered) -> None:
        run, results = rendered
        markdown = to_markdown(run, results)
        assert "## Matched" in markdown
        assert "## Needs review" in markdown
        assert "## Not found" in markdown

    def test_csv_has_a_row_per_track(self, rendered) -> None:
        run, results = rendered
        assert len(to_csv(run, results).strip().splitlines()) == len(results) + 1

    def test_json_keeps_the_losing_candidates(self, rendered) -> None:
        """They are the evidence for whether a threshold is set right."""
        import json

        run, results = rendered
        payload = json.loads(to_json(run, results))
        assert any(entry["candidates"] for entry in payload["results"])


def test_pending_review_excludes_resolved(catalog, sources) -> None:
    matcher = Matcher(FakeProvider(catalog), Thresholds())
    results = matcher.match_all(sources)

    assert len(pending_review(results)) == 1
    for result in results:
        if result.decision is Decision.REVIEW:
            result.chosen_id = result.candidates[0].track.id
    assert pending_review(results) == []
