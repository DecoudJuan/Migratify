"""SQLite persistence: runs, per-track results, and the match cache.

Three jobs:

* **Resume.** Results are written as each track resolves, so an interrupted
  migration continues instead of starting over.
* **Idempotency.** A run knows which destination playlist it created and what
  it already wrote, so re-running does not duplicate anything.
* **Cache.** A track resolved once is never searched again -- in any playlist,
  in either direction, across runs. Search is the slow, rate-limited part of a
  migration, so this is what makes the second run of anything nearly instant.

The cache key is (source provider, source track, destination provider), which
is what makes it direction-safe: Spotify→YTM and YTM→Spotify are different
keys and cannot contaminate each other.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from migratify.config import get_logger, get_settings
from migratify.models import (
    Candidate,
    Decision,
    MatchResult,
    Provider,
    Run,
    RunStatus,
    Track,
)

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                  TEXT PRIMARY KEY,
    source_provider     TEXT NOT NULL,
    target_provider     TEXT NOT NULL,
    source_playlist_id  TEXT NOT NULL,
    source_playlist_name TEXT NOT NULL,
    target_playlist_id  TEXT,
    status              TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    total               INTEGER DEFAULT 0,
    auto                INTEGER DEFAULT 0,
    review              INTEGER DEFAULT 0,
    miss                INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_tracks (
    run_id          TEXT NOT NULL,
    position        INTEGER NOT NULL,
    source_json     TEXT NOT NULL,
    decision        TEXT NOT NULL,
    chosen_id       TEXT,
    candidates_json TEXT NOT NULL,
    resolved_by     TEXT NOT NULL,
    written         INTEGER DEFAULT 0,
    PRIMARY KEY (run_id, position),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS match_cache (
    source_provider TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    target_provider TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    score           REAL NOT NULL,
    candidates_json TEXT NOT NULL,
    resolved_by     TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (source_provider, source_id, target_provider)
);

CREATE INDEX IF NOT EXISTS idx_run_tracks_decision ON run_tracks(run_id, decision);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Everything Migratify remembers between commands."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or get_settings().db_file
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- runs ----------------------------------------------------------------

    def create_run(self, run: Run) -> Run:
        self._db.execute(
            """INSERT INTO runs (id, source_provider, target_provider, source_playlist_id,
                                 source_playlist_name, target_playlist_id, status, created_at,
                                 total, auto, review, miss)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run.id,
                run.source_provider.value,
                run.target_provider.value,
                run.source_playlist_id,
                run.source_playlist_name,
                run.target_playlist_id,
                run.status.value,
                run.created_at.isoformat(),
                run.total,
                run.auto,
                run.review,
                run.miss,
            ),
        )
        self._db.commit()
        return run

    def get_run(self, run_id: str) -> Run | None:
        row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def latest_run(self) -> Run | None:
        row = self._db.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, limit: int = 20) -> list[Run]:
        rows = self._db.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> Run:
        return Run(
            id=row["id"],
            source_provider=Provider(row["source_provider"]),
            target_provider=Provider(row["target_provider"]),
            source_playlist_id=row["source_playlist_id"],
            source_playlist_name=row["source_playlist_name"],
            target_playlist_id=row["target_playlist_id"],
            status=RunStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            total=row["total"],
            auto=row["auto"],
            review=row["review"],
            miss=row["miss"],
        )

    def update_run(self, run: Run) -> None:
        self._db.execute(
            """UPDATE runs SET target_playlist_id = ?, status = ?,
                               total = ?, auto = ?, review = ?, miss = ?
               WHERE id = ?""",
            (
                run.target_playlist_id,
                run.status.value,
                run.total,
                run.auto,
                run.review,
                run.miss,
                run.id,
            ),
        )
        self._db.commit()

    def refresh_counters(self, run_id: str) -> dict[str, int]:
        """Recount decisions from the stored results.

        Derived rather than incremented, so a review that changes a decision
        cannot leave the summary disagreeing with the rows it summarizes.
        """
        rows = self._db.execute(
            "SELECT decision, COUNT(*) AS n FROM run_tracks WHERE run_id = ? GROUP BY decision",
            (run_id,),
        ).fetchall()
        counts = {row["decision"]: row["n"] for row in rows}
        total = sum(counts.values())
        self._db.execute(
            "UPDATE runs SET total = ?, auto = ?, review = ?, miss = ? WHERE id = ?",
            (
                total,
                counts.get(Decision.AUTO.value, 0),
                counts.get(Decision.REVIEW.value, 0),
                counts.get(Decision.MISS.value, 0),
                run_id,
            ),
        )
        self._db.commit()
        return counts

    # -- results -------------------------------------------------------------

    def save_result(self, run_id: str, position: int, result: MatchResult) -> None:
        """Persist one track's outcome, replacing any earlier attempt at it."""
        self._db.execute(
            """INSERT OR REPLACE INTO run_tracks
               (run_id, position, source_json, decision, chosen_id, candidates_json,
                resolved_by, written)
               VALUES (?,?,?,?,?,?,?, COALESCE(
                   (SELECT written FROM run_tracks WHERE run_id = ? AND position = ?), 0))""",
            (
                run_id,
                position,
                result.source.model_dump_json(),
                result.decision.value,
                result.chosen_id,
                json.dumps([c.model_dump(mode="json") for c in result.candidates]),
                result.resolved_by,
                run_id,
                position,
            ),
        )
        self._db.commit()

        if result.chosen_id:
            self._cache_put(result)

    def load_results(self, run_id: str) -> list[MatchResult]:
        rows = self._db.execute(
            "SELECT * FROM run_tracks WHERE run_id = ? ORDER BY position", (run_id,)
        ).fetchall()
        return [self._row_to_result(row) for row in rows]

    def resolved_positions(self, run_id: str) -> set[int]:
        """Positions already matched, so a resumed run skips them."""
        rows = self._db.execute(
            "SELECT position FROM run_tracks WHERE run_id = ?", (run_id,)
        ).fetchall()
        return {row["position"] for row in rows}

    def mark_written(self, run_id: str, chosen_ids: list[str]) -> None:
        """Record which tracks actually reached the destination.

        This is what makes ``apply`` safe to re-run: a second pass adds only
        what is not already flagged as written, instead of duplicating the
        whole playlist.
        """
        self._db.executemany(
            "UPDATE run_tracks SET written = 1 WHERE run_id = ? AND chosen_id = ?",
            [(run_id, cid) for cid in chosen_ids],
        )
        self._db.commit()

    def unwritten(self, run_id: str) -> list[MatchResult]:
        rows = self._db.execute(
            """SELECT * FROM run_tracks
               WHERE run_id = ? AND written = 0 AND chosen_id IS NOT NULL
               ORDER BY position""",
            (run_id,),
        ).fetchall()
        return [self._row_to_result(row) for row in rows]

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> MatchResult:
        return MatchResult(
            source=Track.model_validate_json(row["source_json"]),
            candidates=[Candidate.model_validate(c) for c in json.loads(row["candidates_json"])],
            decision=Decision(row["decision"]),
            chosen_id=row["chosen_id"],
            resolved_by=row["resolved_by"],
        )

    # -- match cache ---------------------------------------------------------

    def _cache_put(self, result: MatchResult) -> None:
        """Remember a resolved match.

        Only results with a chosen candidate are cached. A pending review has
        nothing to remember, and a miss is deliberately not cached: catalogs
        change, and a track missing today may exist next month. Caching misses
        would make that permanently invisible.
        """
        chosen = result.chosen
        if chosen is None:
            return

        self._db.execute(
            """INSERT OR REPLACE INTO match_cache
               (source_provider, source_id, target_provider, target_id, score,
                candidates_json, resolved_by, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                result.source.provider.value,
                result.source.id,
                chosen.track.provider.value,
                chosen.track.id,
                chosen.score,
                json.dumps([c.model_dump(mode="json") for c in result.candidates]),
                result.resolved_by,
                _now(),
            ),
        )
        self._db.commit()

    def cache_lookup(self, target: Provider):
        """A cache callable for :class:`~migratify.matching.search.Matcher`.

        Returns a closure rather than a method so the matcher stays unaware
        that a store exists at all.
        """

        def lookup(track: Track) -> MatchResult | None:
            row = self._db.execute(
                """SELECT * FROM match_cache
                   WHERE source_provider = ? AND source_id = ? AND target_provider = ?""",
                (track.provider.value, track.id, target.value),
            ).fetchone()
            if row is None:
                return None

            candidates = [
                Candidate.model_validate(c) for c in json.loads(row["candidates_json"])
            ]
            return MatchResult(
                source=track,
                candidates=candidates,
                # A cached entry always has a chosen candidate, and a match the
                # user resolved by hand stays resolved -- we never re-ask.
                decision=Decision.AUTO,
                chosen_id=row["target_id"],
                resolved_by="cache",
            )

        return lookup

    def forget(self, source: Track, target: Provider) -> None:
        """Drop a cached match, so the next run searches for it again."""
        self._db.execute(
            """DELETE FROM match_cache
               WHERE source_provider = ? AND source_id = ? AND target_provider = ?""",
            (source.provider.value, source.id, target.value),
        )
        self._db.commit()

    def cache_size(self) -> int:
        return self._db.execute("SELECT COUNT(*) AS n FROM match_cache").fetchone()["n"]

    # -- misc ----------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return {
            "runs": self._db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"],
            "cached_matches": self.cache_size(),
            "database": str(self.path),
        }
