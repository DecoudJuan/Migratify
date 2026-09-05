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
- **You just sign in** — no developer app, no client ID, no API key, nothing
  to paste. Works on a free Spotify account, which the official app flow no
  longer does.
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
pip install -e ".[login]"
```

Requires Python 3.10+. The `login` extra pulls in browser automation, which is
what lets you connect an account by simply signing in.

---

## Connect your accounts

```bash
migratify login
```

That is the whole setup. You sign in to Spotify and YouTube Music the way you
always do, in a browser window.

**No developer app. No client ID. No API key. Nothing to paste.**

This is not a convenience — it is the only path that works for everyone.
Since 2025 Spotify requires a **Premium** subscription to enable Web API
access on a newly registered app, so the traditional "register an app and
paste your client ID" flow is simply unavailable on a free account. Signing in
does not touch that gate.

Under the hood, `login` tries two things in order:

1. **Import a session from a browser you already use.** Instant and
   click-free. Chrome, Edge, Brave, Comet, Arc, Vivaldi, Opera, Chromium,
   Firefox, Zen, LibreWolf and Safari are all recognized, on macOS, Windows
   and Linux.
2. **Open a login window.** Driven against a browser you already have
   installed, with a persistent profile in `~/.migratify/`. Every later
   session refresh runs headless and invisible.

Confirm it worked:

```bash
migratify auth status
migratify playlists spotify
```

### Platform notes

- **macOS** — direct import usually works, since there is no App-Bound
  Encryption. Importing from Safari specifically needs Full Disk Access for
  your terminal: System Settings → Privacy & Security → Full Disk Access.
- **Windows** — Chrome and Edge encrypt cookies with App-Bound Encryption from
  v127, so their sessions cannot be imported by any external process.
  Migratify opens its own login window instead, automatically. Firefox imports
  fine.
- **Linux** — depends on your keyring, but generally works.

All credentials live in `~/.migratify/` and never touch the repository.

### Fallbacks

The sign-in path uses endpoints that are not publicly documented. They are the
ones that ask nothing of you, and someday one of them will change. For that
day, the official flows stay in the tree:

```bash
migratify auth ytmusic --paste   # paste request headers from devtools
migratify auth ytmusic --oauth   # Google Cloud OAuth client
migratify auth spotify --pkce    # your own Spotify app — Premium only
```

> **`--pkce` does not work without Spotify Premium.** This was verified
> directly, not assumed. A free account can register an app, complete the OAuth
> flow and receive a token with every scope granted — and then every single
> Web API call returns 403: *"Active premium subscription required for the
> owner of the app."* Nothing about the app's configuration changes this; the
> gate is on the owner's subscription. `migratify login` registers no app and
> is unaffected.

`--paste` is also the right choice anywhere a browser window cannot open, such
as over SSH or inside a container.

---

## Usage

```bash
# See what you have
migratify playlists spotify
migratify playlists ytmusic

# Dry run: match everything, write a report, touch nothing
migratify plan https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M

# Resolve the ambiguous ones yourself
migratify review

# Now actually create it on the other side
migratify apply
```

Or all three, guided:

```bash
migratify migrate https://open.spotify.com/playlist/...
migratify migrate https://music.youtube.com/playlist?list=...
```

The direction is **detected from the URL** — a Spotify link migrates to
YouTube Music, and vice versa. Pass `--to spotify` or `--to ytmusic` when you
give a bare playlist ID.

`review` and `apply` default to your most recent run, so the three commands
can be typed in sequence with no arguments. Pass a run ID to target an older
one; `migratify runs` lists them.

### What each command does

| Command | Writes to a music service? |
|---|:---:|
| `login`, `auth`, `playlists`, `runs`, `report` | no |
| `plan` | **no** — matches everything and writes a report |
| `review` | no — records your decisions locally |
| `apply` | **yes** — the only one |
| `migrate` | yes — it ends in `apply`, and asks first |

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

## Troubleshooting

**"Signing in needs a browser Migratify can drive"** — install the extra:
`pip install "migratify[login]"`. Only if you have no Chromium-family browser
at all do you also need `playwright install chromium`. Run
`migratify auth status` to see which browsers were found.

**Nothing was imported from my browser** — expected on Windows for Chrome and
Edge; Migratify falls back to its own login window automatically. Let it.

**The login window opened but nothing happened** — run it again. It closes when
it detects the session cookie and can miss it if you signed in on another tab.
The profile persists, so the retry is usually instant.

**It worked yesterday and now says I am not connected** — the session expired.
`migratify login <service>` again, adding `--fresh` to skip the import step.

**Too many tracks went to review** — that is the design working, but it can be
tuned. See the thresholds in `.env.example`, and the `migratify-tune` skill.

**A wrong track got into the playlist** — this is the bug that matters most.
Please open an issue with the source track and what it matched. Raising
`MIGRATIFY_AUTO_ACCEPT` is the immediate workaround.

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
