# Architecture

Migratify is a Python CLI (`src/` layout, package `migratify`). The design goal is
one rule: never silently add a wrong track. This page is the short map; the full
invariants and the reasons behind them are in [CLAUDE.md](../CLAUDE.md).

## Symmetry

The matching engine does not know which direction a migration runs in. Spotify and
YouTube Music both implement one `MusicProvider` protocol
(`src/migratify/providers/base.py`) and the engine works on provider-neutral models
(`models.py`). Adding a provider means implementing the protocol, registering it in
`providers/registry.py` and adding its URL pattern; `matching/` is not touched.

## Modules

| Path | Responsibility |
| --- | --- |
| `src/migratify/models.py` | Neutral `Track`, `Playlist`, `Candidate`, `MatchResult`, `Run` |
| `src/migratify/providers/` | Spotify and YouTube Music as source and destination, plus the registry that detects the direction from a URL |
| `src/migratify/auth/` | Session capture from installed browsers or a login window, with paste-headers, OAuth and PKCE as fallbacks |
| `src/migratify/matching/` | `normalize.py`, `score.py` (pure scoring, vetoes, thresholds) and `search.py` (query orchestration) |
| `src/migratify/store/db.py` | SQLite: runs, per-track results and the match cache |
| `src/migratify/report.py` | Markdown, CSV and JSON reports |
| `src/migratify/artwork.py` | Cover download and Spotify upload |
| `src/migratify/shell.py`, `cli.py` | The interactive prompt and the Typer commands |
| `packaging/`, `scripts/build_exe.py` | PyInstaller spec and the build that also runs the binary |
| `web/` | The landing page, static, published to GitHub Pages |

## Flow of a migration

`plan` resolves the direction from the URL, reads the source playlist, runs the
matcher per track (build queries, search, score, decide), saves each result in the
store and writes a report. Nothing is written to a music service. `review` records
the user's choices for ambiguous tracks. `apply` is the only command that writes,
and it is idempotent through the `written` flag on each stored track. `sync` is the
same flow restricted to tracks that have not yet been written to the linked
destination playlist.

## Matching

Each candidate is scored 0 to 100 from artist (0.40), title (0.30), duration (0.20),
album (0.05) and result type (0.05). An artist similarity below 0.5 caps the score
below the review floor, each differing version tag costs 25 points, and an ISRC
match scores 100. A score of 88 or more with no mismatch is accepted, 70 to 88 (or a
near tie) goes to review, and below 70 is reported as not found. The thresholds are
overridable with `MIGRATIFY_AUTO_ACCEPT`, `MIGRATIFY_REVIEW_FLOOR` and
`MIGRATIFY_AMBIGUITY_MARGIN`.

## Tests

`pytest` runs everything offline, with no credentials. `tests/test_score.py` is the
golden set of hard matching cases and must stay green after any change to
normalization or scoring. `ruff check .` is what CI enforces.
