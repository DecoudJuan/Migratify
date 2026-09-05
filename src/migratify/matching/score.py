"""Candidate scoring and the accept / review / miss decision.

Pure functions over normalized tracks. No network, no state, no provider
knowledge -- which is what makes the golden set possible and what lets the same
scorer run in both migration directions.

The weights encode what actually goes wrong in practice:

* **Artist carries the most weight (0.40)** because the most common failure is
  the right title by the wrong artist. It also has a hard veto: below 0.5
  similarity the candidate is capped no matter how perfect everything else is.
* **Duration matters more than album (0.20 vs 0.05)** because it is the only
  signal neither service can spin. A cover almost never lands within a few
  seconds of the original, and two recordings that agree on length to within
  two seconds are almost always the same recording.
* **Version tags are a penalty, not a weight.** A live take is not a slightly
  worse match for a studio track, it is the wrong song.
* **ISRC short-circuits everything.** It is recording identity rather than
  similarity, so it does not get a weight -- it gets a score of 100.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from migratify.config import Thresholds
from migratify.matching.normalize import NormalizedTrack, normalize_track
from migratify.models import (
    Candidate,
    Decision,
    MatchResult,
    ResultKind,
    ScoreBreakdown,
    Track,
)

# --- weights ----------------------------------------------------------------

W_ARTIST = 0.40
W_TITLE = 0.30
W_DURATION = 0.20
W_ALBUM = 0.05
W_KIND = 0.05

#: Below this, the candidate is almost certainly a different artist.
ARTIST_VETO = 0.50

#: What a vetoed candidate is capped at. Deliberately under the review floor,
#: so a wrong-artist match cannot reach the user as a suggestion at all.
VETO_CEILING = 40.0

#: Per mismatched significant version tag.
VERSION_PENALTY = 25.0

#: Tie-break only. Explicit and clean releases are the same performance.
EXPLICIT_PENALTY = 3.0


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b) / 100.0


def artist_similarity(source: NormalizedTrack, candidate: NormalizedTrack) -> float:
    """How confident we are that these are the same artist.

    An exact provider artist ID overlap is conclusive -- it is the same entity
    in the same catalog, not a name that happens to look alike. Failing that we
    compare artist *sets*, because a track credited to three artists on one
    service and one on the other is still the same track.
    """
    if source.artist_ids and candidate.artist_ids and (source.artist_ids & candidate.artist_ids):
        return 1.0

    if not source.artists or not candidate.artists:
        return 0.0

    # Best pairwise match per source artist, averaged. Rewards overlap without
    # punishing a candidate for listing extra collaborators.
    scores = [
        max(_ratio(a, b) for b in candidate.artists)
        for a in source.artists
    ]
    best = max(scores)
    average = sum(scores) / len(scores)

    # Weighted toward the best single match: getting the lead artist right
    # matters far more than agreeing on the full credit list.
    return 0.7 * best + 0.3 * average


def title_similarity(source: NormalizedTrack, candidate: NormalizedTrack) -> float:
    """Token-set similarity, so word order and stray extras do not break it."""
    core = _ratio(source.title, candidate.title)

    # A candidate title that embeds the source title verbatim is a strong
    # signal even when the rest of the string diverges, which is the usual
    # shape of a YouTube upload title.
    if source.title and source.title in candidate.title:
        core = max(core, 0.92)

    return core


def duration_similarity(source_ms: int | None, candidate_ms: int | None) -> float:
    """Confidence from track length.

    Unknown duration returns a neutral 0.5 rather than 0: some YouTube Music
    results omit it, and a missing value is not evidence of a bad match. It
    should neither rescue nor sink a candidate.
    """
    if not source_ms or not candidate_ms:
        return 0.5

    delta = abs(source_ms - candidate_ms) / 1000.0
    if delta <= 2:
        return 1.0
    if delta <= 5:
        return 0.85
    if delta <= 10:
        return 0.5
    if delta <= 20:
        return 0.2
    return 0.0


def kind_bonus(track: Track) -> float:
    """Prefer catalog songs over arbitrary uploads.

    A YouTube Music *song* is a licensed catalog entry with structured
    metadata. A *video* may be a lyric video, a live rip, a cover or a fan
    edit, so it starts from a lower base.
    """
    if track.kind is ResultKind.SONG:
        return 1.0 if track.official else 0.8
    if track.kind is ResultKind.VIDEO:
        return 0.5 if track.official else 0.25
    return 0.5


def score_candidate(
    source: NormalizedTrack,
    candidate_track: Track,
    *,
    source_explicit: bool = False,
    via: str = "",
) -> Candidate:
    """Score one destination track against the source."""
    candidate = normalize_track(candidate_track)
    breakdown = ScoreBreakdown()

    # Identity beats similarity. An ISRC match is the same recording by
    # definition, so there is nothing left to weigh.
    if source.isrc and candidate.isrc and source.isrc.upper() == candidate.isrc.upper():
        breakdown.isrc_exact = True
        breakdown.artist = 1.0
        breakdown.title = 1.0
        breakdown.duration = duration_similarity(source.duration_ms, candidate.duration_ms)
        return Candidate(track=candidate_track, score=100.0, breakdown=breakdown, via=via)

    breakdown.artist = artist_similarity(source, candidate)
    breakdown.title = title_similarity(source, candidate)
    breakdown.duration = duration_similarity(source.duration_ms, candidate.duration_ms)
    breakdown.album = _ratio(source.album, candidate.album)
    breakdown.kind = kind_bonus(candidate_track)

    weighted = (
        W_ARTIST * breakdown.artist
        + W_TITLE * breakdown.title
        + W_DURATION * breakdown.duration
        + W_ALBUM * breakdown.album
        + W_KIND * breakdown.kind
    ) * 100.0

    mismatched = source.significant_tags ^ candidate.significant_tags
    if mismatched:
        breakdown.version_penalty = -VERSION_PENALTY * len(mismatched)
        weighted += breakdown.version_penalty

    if source_explicit != candidate_track.explicit:
        breakdown.explicit_penalty = -EXPLICIT_PENALTY
        weighted += breakdown.explicit_penalty

    if breakdown.artist < ARTIST_VETO:
        breakdown.artist_vetoed = True
        weighted = min(weighted, VETO_CEILING)

    return Candidate(
        track=candidate_track,
        score=max(0.0, min(100.0, weighted)),
        breakdown=breakdown,
        via=via,
    )


def rank(
    source_track: Track,
    candidate_tracks: list[tuple[Track, str]],
) -> list[Candidate]:
    """Score and sort candidates, best first.

    Takes (track, via) pairs so a report can say which query strategy found the
    winner -- the main input when tuning search.
    """
    source = normalize_track(source_track)
    seen: set[str] = set()
    scored: list[Candidate] = []

    for track, via in candidate_tracks:
        if track.id in seen:
            continue
        seen.add(track.id)
        scored.append(
            score_candidate(source, track, source_explicit=source_track.explicit, via=via)
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


def decide(
    source_track: Track,
    candidates: list[Candidate],
    thresholds: Thresholds,
) -> MatchResult:
    """Turn ranked candidates into an outcome.

    Three outcomes, never one guess. The ambiguity rule matters as much as the
    threshold: two candidates within a few points of each other means the
    scorer cannot tell them apart, and picking the higher one would be a coin
    flip dressed up as a decision.
    """
    result = MatchResult(
        source=source_track,
        candidates=candidates[: thresholds.max_candidates_shown],
    )

    best = result.best
    if best is None or best.score < thresholds.review_floor:
        result.decision = Decision.MISS
        return result

    runner_up = candidates[1] if len(candidates) > 1 else None
    ambiguous = (
        runner_up is not None
        and (best.score - runner_up.score) < thresholds.ambiguity_margin
    )

    confident = (
        best.score >= thresholds.auto_accept
        and not best.breakdown.version_penalty
        and not best.breakdown.artist_vetoed
        and not ambiguous
    )

    if confident:
        result.decision = Decision.AUTO
        result.chosen_id = best.track.id
    else:
        result.decision = Decision.REVIEW

    return result
