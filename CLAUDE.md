# CLAUDE.md

Guidance for Claude Code when working in this repository.

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

## Architecture

The system is **symmetric**: the matching engine does not know which direction
a migration runs in. Both services implement one interface and the engine works
on provider-neutral models.

```
src/migratify/
  models.py          Track, Playlist, Candidate, MatchResult -- neutral models
  config.py          settings, ~/.migratify paths, logging
  providers/
    base.py          MusicProvider protocol (the full read+write contract)
    spotify.py       Spotify as both source and destination
    ytmusic.py       YouTube Music as both source and destination
    registry.py      provider lookup by name + direction autodetection from URL
  auth/
    browsers.py      catalog of installed browsers, per platform
    browser.py       session capture: import, or a login window
    spotify_session.py  web-player session -- the default Spotify path
    spotify.py       OAuth PKCE -- fallback, needs a registered app
    ytmusic.py       cookie session, plus paste-headers and OAuth fallbacks
  matching/
    normalize.py     title/artist normalization, version-tag extraction
    search.py        candidate collection across query strategies
    score.py         weighted scoring, vetoes, decision thresholds
  artwork.py         cover download, JPEG reencode, Spotify upload
  store/db.py        SQLite: runs, tracks, matches; cache + resume
  report.py          markdown / csv / json reports
  cli.py             Typer + Rich commands
.claude/skills/      migratify-setup / -migrate / -review / -tune
```

### Adding a provider

Implement `MusicProvider` in `providers/`, register it in `registry.py`, add
its URL pattern to the autodetector. **Do not touch `matching/`** — if a new
provider requires changing the scorer, the abstraction is wrong.

## The matching engine

This is the core of the project. Changes here need tests.

### Normalization (`matching/normalize.py`)

Unicode NFKD, diacritics stripped, casefolded. Title qualifiers are **extracted
and kept**, not discarded — `(feat. X)`, `- Remastered 2011`, `- Live at
Wembley`, `(Radio Edit)`. They become `version_tags`, and version tags are how
we tell a studio cut apart from a live cut.

`feat.` artists are merged into the artist set: Spotify puts them in
`artists[]`, YouTube Music usually buries them in the title.

YouTube Music as a source needs extra cleanup: `"… - Topic"` channels,
`(Official Video)`, `(Lyric Video)`, `[HD]` suffixes.

### Scoring (`matching/score.py`)

Score 0..100:

| Signal | Weight |
|---|---|
| artist similarity | **0.40** |
| title similarity | 0.30 |
| duration proximity | **0.20** |
| album match | 0.05 |
| result-type bonus (song > video, official channel) | 0.05 |

Vetoes and penalties — this is what actually prevents false positives:

- **Artist veto**: `artist_score < 0.5` caps the candidate no matter how
  perfect the title is. This is the defense against "same name, other artist".
- **Version penalty**: −25 per differing version tag. A studio track must not
  match a live take, a remix, or a karaoke version.
- **Duration** is the strongest objective signal and the best cover detector —
  a cover almost never lands within ±5s of the original.
- **ISRC match short-circuits to 100.** It is recording identity, not
  similarity. Only available when the destination is Spotify.

### Decision thresholds

- `>= 88` and no version mismatch → auto-accept
- `70..88`, **or** top-1 and top-2 within 4 points → review queue
- `< 70` → not found

Thresholds live in `config.py` and are env-overridable. When you tune them,
re-run the golden set.

### The golden set (`tests/`)

`matching/score.py` is a **pure function** — `(Track, Candidate) -> score` —
so it is tested with fixtures and no network at all. The golden set holds the
hard cases: same title/different artist, cover, live version, remaster,
displaced `feat.`, translated title, sped-up edit, `- Topic` channel.

**Any change to normalization or scoring must keep the golden set green.**
If a case legitimately changes, change the expectation in the same commit and
say why in the message.

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
  encoder in `artwork.py` iterates on size and quality to fit it.
- **Spotify auth uses PKCE**, so there is no client secret anywhere in this
  project. Do not add one.
- **Spotify gates Web API access on a Premium subscription** for newly
  registered apps, as of 2025. This is why `migratify login` -- which
  registers nothing -- is the default path and `--pkce` is the fallback, and
  not the other way round. Never restructure the auth flow to lead with app
  registration; it locks out every free account.
- **Windows Chrome and Edge cookies cannot be read** by any external process
  (App-Bound Encryption, v127+). `readable_browsers()` excludes them
  deliberately rather than letting an import fail confusingly. The login
  window exists for this case.
- **We do not reimplement the Spotify web-player token handshake.** It is
  guarded by a rotating TOTP scheme that changes without notice. We let the
  real player perform it in a headless page and observe the result, so their
  own client adapts on our behalf. Do not replace this with a direct call to
  `/api/token`.

## Conventions

- Python ≥ 3.10, `src/` layout. Format and lint with `ruff`.
- Type hints on every public function. `pydantic` models for anything crossing
  a provider boundary.
- Network calls only in `providers/` and `auth/`. `matching/` stays pure — that
  is what makes it testable.
- User-facing CLI output goes through `rich`; never bare `print`.
- Errors the user can fix (expired token, missing scope, playlist not found)
  get an actionable message, not a traceback.

## Commits

Conventional Commits, scoped and atomic — one logical change per commit. The
history is meant to be readable as the story of how the tool was built.

## Local development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest                      # golden set runs offline

migratify auth status
migratify plan <playlist-url>   # read-only, always safe to run
```

`plan` never writes to a music service, so it is safe to run while iterating.
`apply` does write — be deliberate about running it against a real account.
