# Migratify

**Migrate playlists between Spotify and YouTube Music — in both directions, without the wrong songs.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Moving a playlist is easy. Moving it *correctly* is not.

Every playlist migrator hits the same wall: the same song title exists as a
cover, a karaoke track, a live take, a remix, a sped-up edit — and as a
completely different song by a completely different artist. Most tools take the
first search result and hand you a playlist that is quietly 15% wrong.

Migratify is built around one rule: **never silently add a wrong track.** When
it is confident, it matches. When it is not, it asks you.

---

## What it does

- **Bidirectional** — Spotify → YouTube Music and YouTube Music → Spotify, same
  engine, same precision.
- **Carries the playlist itself**, not just the tracks: name, description, and
  cover art.
- **Precision-first matching** — weighted scoring over artist, title, duration,
  album and result type, with hard vetoes for wrong-artist and wrong-version
  matches.
- **Three outcomes per track**, never one guess: auto-accepted, queued for your
  review with the top 5 candidates, or reported as not found.
- **Read-only by default** — `plan` computes the whole migration and writes a
  report. Nothing reaches your account until you run `apply`.
- **Resumable and idempotent** — every resolved match is cached in SQLite, so an
  interrupted run picks up where it stopped and a re-run costs no new searches.
- **Reports** in Markdown, CSV or JSON, so you can audit exactly what happened.

---

## Install

```bash
git clone https://github.com/DecoudJuan/Migratify.git
cd Migratify
pip install -e .
```

Requires Python 3.10+.

---

## Setup

### Spotify

1. Create an app at the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Add `http://127.0.0.1:8888/callback` as a redirect URI.
3. Copy `.env.example` to `.env` and set `MIGRATIFY_SPOTIFY_CLIENT_ID`.

Migratify uses the **PKCE** flow — there is no client secret to store anywhere.

```bash
migratify auth spotify
```

### YouTube Music

YouTube Music has no public write API, so Migratify uses
[`ytmusicapi`](https://github.com/sigma67/ytmusicapi). Two ways to authenticate:

```bash
migratify auth ytmusic --browser   # fastest: paste headers from an open session
migratify auth ytmusic --oauth     # longer setup, refreshes itself, doesn't expire
```

- `--browser` takes about two minutes and needs no Google Cloud project, but the
  session expires every so often and you re-paste.
- `--oauth` needs a Google Cloud OAuth client of type *TV and Limited Input*,
  then refreshes on its own indefinitely.

Check what is connected:

```bash
migratify auth status
```

All credentials live in `~/.migratify/` and never touch the repository.

---

## Usage

```bash
# See what you have
migratify playlists spotify
migratify playlists ytmusic

# Dry run: match everything, write a report, touch nothing
migratify plan https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M

# Resolve the ambiguous ones yourself
migratify review <run-id>

# Now actually create it on the other side
migratify apply <run-id>
```

Or all three, guided:

```bash
migratify migrate https://open.spotify.com/playlist/...
migratify migrate https://music.youtube.com/playlist?list=...
```

The direction is **detected from the URL**. Pass `--to spotify` or
`--to ytmusic` when you give a bare playlist ID.

---

## How the matching works

Each source track produces several search queries against the destination.
Every candidate is scored 0–100:

| Signal | Weight | Why |
|---|---|---|
| Artist similarity | **0.40** | The single most common failure mode is the right title by the wrong artist |
| Title similarity | 0.30 | Token-set matching, so word order and extra qualifiers don't break it |
| Duration proximity | **0.20** | The strongest objective signal — a cover almost never lands within ±5s of the original |
| Album match | 0.05 | |
| Result type | 0.05 | A YTM *song* beats a *video*; an official artist channel beats a user upload |

On top of the weighted score:

- **Artist veto** — if artist similarity is below 0.5, the candidate is capped
  regardless of how perfect the title looks.
- **Version penalty** — −25 for each mismatched version tag (`live`, `remix`,
  `acoustic`, `instrumental`, `karaoke`, `radio_edit`, `sped_up`, …). A studio
  recording will not match a live take.
- **ISRC short-circuit** — when the destination is Spotify and the source track
  carries an ISRC, an exact ISRC hit scores 100 immediately. That is recording
  identity, not similarity.

Then:

| Score | Outcome |
|---|---|
| ≥ 88, no version mismatch | **Auto-accepted** |
| 70–88, or top two within 4 points | **Queued for review** with 5 candidates |
| < 70 | **Reported as not found** |

Thresholds are configurable in `.env`.

---

## Playlist metadata

|  | Name | Description | Cover art |
|---|:---:|:---:|:---:|
| → Spotify | ✅ | ✅ | ✅ |
| → YouTube Music | ✅ | ✅ | ❌ |

`ytmusicapi` has no endpoint for uploading a playlist cover — YouTube Music
generates one from the track artwork instead. Migratify still downloads the
original cover to `~/.migratify/covers/` and tells you where it is, so you can
upload it by hand if you want it.

---

## Claude Code skills

The repo ships skills under `.claude/skills/` for driving Migratify
conversationally:

| Skill | What it does |
|---|---|
| `migratify-setup` | Walks you through authenticating both services |
| `migratify-migrate` | End-to-end migration in either direction |
| `migratify-review` | Resolves the ambiguous queue — Claude reasons about discographies and context where fuzzy scoring can't break a tie |
| `migratify-tune` | Analyzes a run's misses and proposes threshold changes |

---

## Why not a Chrome extension?

YouTube Music exposes no public write API, and the matching engine depends on
libraries (rapidfuzz, Unicode normalization, Pillow) that have no place in a
content script. A local CLI is the honest form for this problem.

## Why not the official YouTube Data API?

It costs 50 quota units per track inserted against a 10,000/day cap — roughly
200 songs per day — and its `search` returns YouTube *videos* rather than
YouTube Music *songs*, so it gives you no structured artist or duration data.
Those are exactly the fields the scorer depends on.

---

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

The scorer is a pure function, so the golden set of hard matching cases runs
entirely offline. See [CLAUDE.md](CLAUDE.md) for the architecture and the
invariants to preserve.

---

## License

MIT — see [LICENSE](LICENSE).
