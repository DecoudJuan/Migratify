# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Migratify is

A CLI that migrates playlists **bidirectionally** between Spotify and YouTube
Music, carrying over the tracks *and* the playlist's name, description and
cover art.

The hard part is not moving data — it is **not moving the wrong song**. The
same title exists as covers, karaoke tracks, live versions, remixes, sped-up
edits, and as entirely different songs by different artists. The whole design
of this codebase serves one goal: **never silently add a wrong track**.

Two rules follow from that, and they are not negotiable:

1. **Nothing is written to the destination without an explicit `apply`.**
   `plan` is always read-only.
2. **When the matcher is unsure, it asks — it does not guess.** An ambiguous
   track goes to a review queue, never to the playlist.

When a change trades precision for convenience — a looser threshold, an
early exit, a fallback that guesses — it is the wrong change, even when it
makes the tool feel better to use.

## Commands

```bash
pip install -e ".[dev]"        # core + test tooling
pip install -e ".[login]"      # + browser automation, needed to sign in

ruff check .                   # what CI enforces
ruff check --fix .

pytest                         # whole suite, offline, no credentials
pytest tests/test_score.py     # one file
pytest -k "veto"               # one pattern
pytest tests/test_score.py::TestTheWrongSong::test_karaoke_is_rejected
pytest -q --cov=migratify      # with coverage
```

`ruff format` is **not** enforced — CI runs `ruff check` only, and the tree is
not fully `ruff format` clean. Do not reformat the codebase as a side effect
of an unrelated change.

Running the tool itself:

```bash
migratify login                # connect both services
migratify auth status          # what is connected, which browsers were found
migratify plan <playlist-url>  # read-only, always safe while iterating
migratify runs                 # past runs, with their IDs
```

`plan` never writes to a music service. `apply` does — be deliberate about
running it against a real account.

## Architecture

The system is **symmetric**: the matching engine does not know which direction
a migration runs in. Both services implement one interface and the engine works
on provider-neutral models.

```
src/migratify/
  models.py          Track, Playlist, Candidate, MatchResult, Run -- neutral
  config.py          settings, ~/.migratify paths, logging, thresholds
  providers/
    base.py          MusicProvider protocol (the full read+write contract)
    spotify.py       Spotify as both source and destination
    ytmusic.py       YouTube Music as both source and destination
    registry.py      provider lookup + direction autodetection from a URL
  auth/
    browsers.py      catalog of installed browsers, per platform
    browser.py       session capture: import, or a login window
    spotify_session.py  web-player session -- the default Spotify path
    spotify.py       OAuth PKCE -- fallback, needs a registered app
    ytmusic.py       cookie session, plus paste-headers and OAuth fallbacks
  matching/
    normalize.py     title/artist normalization, version-tag extraction
    score.py         weighted scoring, vetoes, decision thresholds
    search.py        query orchestration -- the only matching/ module that
                     touches a provider, and only via the protocol
  artwork.py         cover download, JPEG reencode, Spotify upload
  store/db.py        SQLite: runs, run_tracks, match_cache
  report.py          markdown / csv / json reports
  cli.py             Typer + Rich commands
tests/factories.py   make_track() -- imported as `from factories import ...`
.claude/skills/      migratify-setup / -migrate / -review / -tune
```

### The flow through those modules

`cli.plan` → `registry.resolve_direction` (URL decides source and destination)
→ source provider `get_playlist` + `get_tracks` → `matching.search.Matcher`
per track → provider `build_queries` → provider `search` →
`score.rank` → `score.decide` → `store.save_result` → `report.write`.

`cli.apply` then reads the store and writes only what has a chosen candidate.

### Adding a provider

Implement `MusicProvider` in `providers/`, register it in `registry.py`, add
its URL pattern to the autodetector. **Do not touch `matching/`** — if a new
provider requires changing the scorer, the abstraction is wrong.

## The matching engine

The core of the project. Changes here need tests.

### Normalization (`matching/normalize.py`)

Unicode NFKD, diacritics stripped, casefolded. Title qualifiers are **extracted
and kept**, not discarded — `(feat. X)`, `- Remastered 2011`, `- Live at
Wembley`, `(Radio Edit)`. They become `version_tags`, and version tags are how
we tell a studio cut apart from a live cut.

`feat.` artists are merged into the artist set: Spotify puts them in
`artists[]`, YouTube Music usually buries them in the title.

YouTube Music as a source needs extra cleanup: `"… - Topic"` channels,
`(Official Video)`, `(Lyric Video)`, `[HD]` suffixes.

Two traps worth knowing before editing:

- `clean_title` falls back to the unstripped title when stripping leaves
  nothing, so `(Don't Fear) The Reaper` does not normalize to `""` and match
  everything.
- `REMASTER` is detected but excluded from `SIGNIFICANT_TAGS`. It is a
  different master of the same performance, and the services disagree on
  whether to label it, so penalizing it rejects correct matches constantly.

### Scoring (`matching/score.py`)

`score_candidate(NormalizedTrack, Track) -> Candidate`. Pure — no network, no
state, no provider knowledge. Score 0..100:

| Signal | Weight |
|---|---|
| artist similarity | **0.40** |
| title similarity | 0.30 |
| duration proximity | **0.20** |
| album match | 0.05 |
| result-type bonus (song > video, official channel) | 0.05 |

Vetoes and penalties — this is what actually prevents false positives:

- **Artist veto**: `artist_score < 0.5` caps the candidate at 40, which is
  *below the review floor*, so a wrong-artist match cannot even be offered as
  a suggestion. This is the defense against "same name, other artist" and
  everything else leans on it. Never lower it.
- **Version penalty**: −25 per differing significant version tag. A studio
  track must not match a live take, a remix, or a karaoke version.
- **Duration** is the strongest objective signal and the best cover detector —
  a cover almost never lands within ±5s of the original. Unknown duration
  scores a neutral 0.5, not 0: absence is not evidence.
- **ISRC match short-circuits to 100.** Recording identity, not similarity.
  Only reachable when the destination is Spotify; YouTube Music does not
  expose ISRCs.

### Decision thresholds

- `>= 88`, no version mismatch, no veto, no near-tie → auto-accept
- `70..88`, **or** top-1 and top-2 within 4 points → review queue
- `< 70` → not found

The near-tie rule matters as much as the threshold: indistinguishable
candidates mean the scorer cannot tell them apart, and taking the higher score
would be a coin flip dressed as a decision.

Thresholds live in `config.Thresholds` and are env-overridable
(`MIGRATIFY_AUTO_ACCEPT`, `MIGRATIFY_REVIEW_FLOOR`,
`MIGRATIFY_AMBIGUITY_MARGIN`). When you tune them, re-run the golden set.

### Search economies (`matching/search.py`)

Two early exits, both **correctness-preserving by construction**:

- A `decisive` query (ISRC) that returns anything ends the search.
- `_settled()` stops once the leader is past `auto_accept + 4` *and* has no
  version penalty, no veto and no close runner-up.

That second condition set is the whole point: the early exit refuses to fire on
anything that would have gone to review, so it can never convert a review into
a silent auto-accept. **Preserve that property if you touch this.**

### Persistence semantics (`store/db.py`)

- The cache key is `(source provider, source id, destination provider)`, so the
  two directions cannot contaminate each other.
- **Misses are deliberately not cached.** Catalogs change; caching an absence
  makes it permanent.
- Pending reviews are not cached either — there is nothing yet to remember.
- Run counters are *recounted* from `run_tracks`, never incremented, so
  resolving a review cannot leave the summary disagreeing with its rows.
- `apply` is idempotent through the `written` flag on `run_tracks`.

### The test suite (`tests/`)

Everything runs offline — no credentials, no network, no cassettes.

- `test_normalize.py` pins the contract the scorer is built on.
- `test_score.py` is the **golden set**: same title/different artist, cover at
  the wrong length, live, karaoke, remix, sped-up, remaster, displaced
  `feat.`, `- Topic` channel, ISRC identity.
- `test_pipeline.py` runs the real `Matcher`, `Store` and report code against
  an in-memory provider implementing `MusicProvider`. If it passes and a real
  provider fails, the bug is in that provider.

**Any change to normalization or scoring must keep the golden set green.** If
a case legitimately changes, change the expectation in the same commit and say
why in the message.

`tests/` is not a package; `factories.py` is imported as
`from factories import make_track`, which works because pytest puts the test
directory on `sys.path`. A relative import from `conftest.py` does not.

## Platform constraints — read before "fixing" something

- **`ytmusicapi` cannot upload a playlist cover.** No endpoint exists. Covers
  are writable on the Spotify side only; going toward YTM we download the
  image to `~/.migratify/covers/` and tell the user to upload it by hand.
  Do not try to work around this.
- **We deliberately do not use YouTube Data API v3.** 50 quota units per track
  insert against a 10k/day cap is ~200 songs a day, and its `search` returns
  YouTube *videos* rather than YTM *songs* — no structured artist or duration
  metadata, which is exactly what the scorer needs.
- **Spotify cover upload** is JPEG, base64-encoded, hard limit 256 KB. The
  encoder in `artwork.py` iterates on quality then size to fit it.
- **Spotify auth uses PKCE**, so there is no client secret anywhere in this
  project. Do not add one.
- **Spotify gates Web API access on a Premium subscription** for newly
  registered apps, as of 2025. This is why `migratify login` — which registers
  nothing — is the default path and `--pkce` is the fallback, and not the
  other way round. Never restructure the auth flow to lead with app
  registration; it locks out every free account.
- **Windows Chrome and Edge cookies cannot be read** by any external process
  (App-Bound Encryption, v127+). `readable_browsers()` excludes them
  deliberately rather than letting an import fail confusingly. The login
  window exists for this case. macOS has no such restriction.
- **We do not reimplement the Spotify web-player token handshake.** It is
  guarded by a rotating TOTP scheme that changes without notice. We let the
  real player perform it in a headless page and observe the result, so their
  own client adapts on our behalf. Do not replace this with a direct call to
  `/api/token`.

## Conventions

- Python ≥ 3.10, `src/` layout, lint with `ruff check`.
- Type hints on every public function. `pydantic` models for anything crossing
  a provider boundary.
- Network calls only in `providers/` and `auth/`. `matching/` stays pure — that
  is what makes it testable.
- User-facing CLI output goes through `rich`; never bare `print`.
- Errors the user can fix (expired session, missing permission, playlist not
  found) get an actionable message naming the command to run, not a traceback.
- Credentials live only in `~/.migratify/`, never in the repo or the working
  directory.

## Commits

Conventional Commits, scoped and atomic — one logical change per commit. The
body explains *why*, especially for a non-obvious trade-off, so the history
reads as the story of how the tool was built. `ROADMAP.md` links completed
items back to their commit; keep it current.
