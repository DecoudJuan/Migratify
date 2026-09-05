"""Migration reports in Markdown, CSV and JSON.

The report is the deliverable of ``plan``. It is what makes a dry run useful:
you read it, decide whether the matching is trustworthy, and only then apply.

So every row carries *why*, not just what. A bare score asks the reader to
trust a number they cannot check; the per-signal breakdown lets them see that
a match was accepted on an exact duration and an artist ID, or flagged because
two candidates were indistinguishable.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from migratify.config import get_settings
from migratify.models import Candidate, Decision, MatchResult, Run

_DECISION_LABEL = {
    Decision.AUTO: "auto",
    Decision.REVIEW: "review",
    Decision.MISS: "not found",
    Decision.SKIPPED: "skipped",
}

_DECISION_ICON = {
    Decision.AUTO: "OK",
    Decision.REVIEW: "??",
    Decision.MISS: "--",
    Decision.SKIPPED: "xx",
}


def _artists(track) -> str:
    return ", ".join(track.artists) or "unknown"


def _duration(track) -> str:
    if not track.duration_ms:
        return ""
    seconds = round(track.duration_ms / 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _why(candidate: Candidate | None) -> str:
    if candidate is None:
        return "no candidate cleared the floor"
    reasons = candidate.breakdown.reasons()
    reasons.append(f"found via {candidate.via}" if candidate.via else "")
    return ", ".join(r for r in reasons if r)


# --- markdown ---------------------------------------------------------------


def to_markdown(run: Run, results: list[MatchResult]) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# {run.source_playlist_name}")
    add("")
    add(f"**{run.direction}** · run `{run.id}` · {run.created_at:%Y-%m-%d %H:%M} UTC")
    add("")

    counts = dict.fromkeys(Decision, 0)
    for result in results:
        counts[result.decision] += 1
    total = len(results) or 1

    add("| Outcome | Tracks | Share |")
    add("|---|---:|---:|")
    for decision in (Decision.AUTO, Decision.REVIEW, Decision.MISS, Decision.SKIPPED):
        count = counts[decision]
        if count or decision is not Decision.SKIPPED:
            add(f"| {_DECISION_LABEL[decision]} | {count} | {count / total:.0%} |")
    add(f"| **total** | **{len(results)}** | |")
    add("")

    matched = [r for r in results if r.chosen]
    if matched:
        add("## Matched")
        add("")
        add("| | Source | Destination | Score | Why |")
        add("|---|---|---|---:|---|")
        for result in matched:
            chosen = result.chosen
            add(
                f"| {_DECISION_ICON[result.decision]} "
                f"| {result.source.title} — {_artists(result.source)} "
                f"| {chosen.track.title} — {_artists(chosen.track)} "
                f"| {chosen.score:.0f} | {_why(chosen)} |"
            )
        add("")

    needs_review = [r for r in results if r.decision is Decision.REVIEW and not r.chosen_id]
    if needs_review:
        add("## Needs review")
        add("")
        add("The scorer could not decide these on its own. Run `migratify review` to resolve them.")
        add("")
        for result in needs_review:
            add(f"### {result.source.title} — {_artists(result.source)}")
            add("")
            add(f"*{_duration(result.source)} · {result.source.album or 'no album'}*")
            add("")
            add("| # | Candidate | Length | Score | Why |")
            add("|---:|---|---|---:|---|")
            for index, candidate in enumerate(result.candidates, start=1):
                add(
                    f"| {index} | {candidate.track.title} — {_artists(candidate.track)} "
                    f"| {_duration(candidate.track)} | {candidate.score:.0f} "
                    f"| {_why(candidate)} |"
                )
            add("")

    misses = [r for r in results if r.decision is Decision.MISS]
    if misses:
        add("## Not found")
        add("")
        add("Nothing on the destination scored high enough to be worth showing.")
        add("")
        for result in misses:
            best = result.best
            hint = f" — closest was {best.score:.0f}" if best else ""
            add(f"- {result.source.title} — {_artists(result.source)}{hint}")
        add("")

    return "\n".join(lines)


# --- csv --------------------------------------------------------------------


def to_csv(run: Run, results: list[MatchResult]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "position",
            "decision",
            "source_title",
            "source_artists",
            "source_album",
            "source_duration_ms",
            "source_isrc",
            "target_id",
            "target_title",
            "target_artists",
            "target_duration_ms",
            "score",
            "artist_score",
            "title_score",
            "duration_score",
            "version_penalty",
            "artist_vetoed",
            "isrc_exact",
            "found_via",
            "resolved_by",
        ]
    )

    for position, result in enumerate(results, start=1):
        chosen = result.chosen
        breakdown = chosen.breakdown if chosen else None
        writer.writerow(
            [
                position,
                result.decision.value,
                result.source.title,
                "; ".join(result.source.artists),
                result.source.album or "",
                result.source.duration_ms or "",
                result.source.isrc or "",
                chosen.track.id if chosen else "",
                chosen.track.title if chosen else "",
                "; ".join(chosen.track.artists) if chosen else "",
                (chosen.track.duration_ms or "") if chosen else "",
                f"{chosen.score:.1f}" if chosen else "",
                f"{breakdown.artist:.3f}" if breakdown else "",
                f"{breakdown.title:.3f}" if breakdown else "",
                f"{breakdown.duration:.3f}" if breakdown else "",
                f"{breakdown.version_penalty:.0f}" if breakdown else "",
                int(breakdown.artist_vetoed) if breakdown else "",
                int(breakdown.isrc_exact) if breakdown else "",
                chosen.via if chosen else "",
                result.resolved_by,
            ]
        )

    return buffer.getvalue()


# --- json -------------------------------------------------------------------


def to_json(run: Run, results: list[MatchResult]) -> str:
    """Full fidelity, including every candidate considered.

    This is the format ``migratify-tune`` and the review skill read: the losing
    candidates are the evidence for whether a threshold is set right.
    """
    return json.dumps(
        {
            "run": run.model_dump(mode="json"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "results": [
                {
                    "decision": result.decision.value,
                    "resolved_by": result.resolved_by,
                    "chosen_id": result.chosen_id,
                    "source": result.source.model_dump(mode="json"),
                    "candidates": [c.model_dump(mode="json") for c in result.candidates],
                }
                for result in results
            ],
        },
        indent=2,
        ensure_ascii=False,
    )


# --- writing ----------------------------------------------------------------

FORMATS = {"md": to_markdown, "csv": to_csv, "json": to_json}


def write(run: Run, results: list[MatchResult], fmt: str = "md") -> Path:
    """Render a report to ``~/.migratify/reports`` and return its path."""
    if fmt not in FORMATS:
        raise ValueError(f"Unknown report format {fmt!r}. Choose from: {', '.join(FORMATS)}")

    settings = get_settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.reports_dir / f"{run.id}.{fmt}"
    path.write_text(FORMATS[fmt](run, results), encoding="utf-8")
    return path
