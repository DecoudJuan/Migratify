"""Search orchestration: run a provider's query strategies and decide.

This is the only part of :mod:`migratify.matching` that touches a provider, and
it does so through the protocol alone -- it never learns which service it is
talking to. Everything specific lives in the provider's ``build_queries``.

Two economies matter here, because searches are the slow and rate-limited part
of a migration:

* A **decisive** query (an ISRC lookup) that lands ends the search.
* Once a candidate is comfortably past the auto-accept bar and unambiguous,
  further queries can only confirm what we already know, so we stop.

Both are correctness-preserving: we never stop early on a result that would
have gone to review.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from migratify.config import Thresholds, get_logger
from migratify.matching.score import decide, rank
from migratify.models import Candidate, Decision, MatchResult, Track
from migratify.providers.base import MusicProvider

log = get_logger(__name__)

#: Results pulled per query. Enough to see past a wrong-but-popular first hit,
#: small enough not to waste the request.
RESULTS_PER_QUERY = 10

#: Stop searching once the leader is this far past auto-accept. At this margin
#: no later query could produce something better in a way that changes the
#: outcome, so the remaining round trips buy nothing.
EARLY_EXIT_MARGIN = 4.0


class Matcher:
    """Finds each source track on a destination provider.

    The cache is keyed by (source provider, source track, destination
    provider), so a track resolved in one playlist is never searched again --
    in either direction, and across runs.
    """

    def __init__(
        self,
        target: MusicProvider,
        thresholds: Thresholds,
        cache: Callable[[Track], MatchResult | None] | None = None,
    ) -> None:
        self.target = target
        self.thresholds = thresholds
        self.cache = cache

    def collect(self, track: Track) -> list[tuple[Track, str]]:
        """Gather candidates from every query strategy, best-effort.

        A failing query is logged and skipped rather than aborting the track:
        one bad query out of five should not turn a findable song into a miss.
        """
        collected: list[tuple[Track, str]] = []
        seen: set[str] = set()

        for query in self.target.build_queries(track):
            try:
                results = self.target.search(query, limit=RESULTS_PER_QUERY)
            except Exception as exc:
                log.debug("Query %s failed for %r: %s", query.label, track.display, exc)
                continue

            fresh = [(r, query.label) for r in results if r.id not in seen]
            seen.update(r.id for r, _ in fresh)
            collected.extend(fresh)

            if query.decisive and results:
                # Recording identity. Nothing further can improve on this.
                log.debug("Decisive %s hit for %r", query.label, track.display)
                break

            if self._settled(track, collected):
                log.debug("Early exit for %r after %s", track.display, query.label)
                break

        return collected

    def _settled(self, track: Track, collected: list[tuple[Track, str]]) -> bool:
        """True when more searching cannot change the outcome."""
        if not collected:
            return False
        ranked = rank(track, collected)
        best = ranked[0]
        if best.score < self.thresholds.auto_accept + EARLY_EXIT_MARGIN:
            return False
        if best.breakdown.version_penalty or best.breakdown.artist_vetoed:
            return False
        # A close runner-up means this would go to review anyway, and another
        # query might yet break the tie.
        return not (
            len(ranked) > 1
            and (best.score - ranked[1].score) < self.thresholds.ambiguity_margin
        )

    def match(self, track: Track) -> MatchResult:
        """Resolve one track to a decision."""
        if self.cache is not None:
            cached = self.cache(track)
            if cached is not None:
                cached.resolved_by = "cache"
                return cached

        collected = self.collect(track)
        if not collected:
            return MatchResult(source=track, decision=Decision.MISS)

        return decide(track, rank(track, collected), self.thresholds)

    def match_all(
        self,
        tracks: Iterable[Track],
        on_result: Callable[[MatchResult], None] | None = None,
    ) -> list[MatchResult]:
        """Resolve every track, reporting as it goes.

        Results are emitted through the callback as they land so a long
        migration can persist progress and stay resumable, rather than holding
        everything until the end and losing it all on an interruption.
        """
        results: list[MatchResult] = []
        for track in tracks:
            result = self.match(track)
            results.append(result)
            if on_result is not None:
                on_result(result)
        return results


def summarize(results: list[MatchResult]) -> dict[str, int]:
    counts = {decision.value: 0 for decision in Decision}
    for result in results:
        counts[result.decision.value] += 1
    counts["total"] = len(results)
    return counts


def accepted_ids(results: list[MatchResult]) -> list[str]:
    """Destination track IDs to write, in source playlist order.

    Only tracks with an explicit chosen candidate -- auto-accepted or resolved
    by the user. Anything still sitting in review is deliberately excluded:
    unresolved is not the same as approved.
    """
    ids: list[str] = []
    for result in results:
        if result.chosen_id and result.decision in (Decision.AUTO, Decision.REVIEW):
            ids.append(result.chosen_id)
    return ids


def pending_review(results: list[MatchResult]) -> list[MatchResult]:
    return [r for r in results if r.decision is Decision.REVIEW and not r.chosen_id]


def best_candidates(result: MatchResult, limit: int) -> list[Candidate]:
    return result.candidates[:limit]
