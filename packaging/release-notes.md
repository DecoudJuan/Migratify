**Move a playlist between Spotify and YouTube Music — in either direction, without the wrong songs.**

Moving a playlist is easy. Moving it *correctly* is not. The same song title
exists as a cover, a karaoke track, a live take, a remix, a sped-up edit — and
as a completely different song by a completely different artist. Most tools
take the first search result and hand you a playlist that is quietly 15% wrong.

Migratify is built around one rule: **never silently add a wrong track.** When
it is confident, it matches. When it is not, it asks you — and until you say
so, nothing is written to your account at all.

---

## Download

| Platform | File |
|---|---|
| Windows | `migratify-windows.zip` |
| macOS | `migratify-macos.zip` |

Unzip it and run `migratify` from inside the folder. **No Python needed.**
Keep the whole folder — the executable on its own will not run.

- **Windows** — SmartScreen will warn about an unsigned binary. *More info →
  Run anyway.*
- **macOS** — Gatekeeper will block it on first launch. Right-click the
  executable → *Open*, or `xattr -d com.apple.quarantine migratify`.

Prefer to install from source? `pip install -e ".[login]"` — see the README.

---

## Getting started

```bash
migratify login                       # sign in to both services
migratify playlists spotify           # see what you have
migratify migrate <playlist-url>      # plan -> review -> apply, guided
```

**No developer app. No client ID. No API key. Nothing to paste.** You sign in
the way you always do, in a browser window. This is not a convenience: since
2025 Spotify requires **Premium** to enable Web API access on a newly
registered app, so the usual "register an app and paste your client ID" flow
is simply unavailable on a free account. Signing in does not touch that gate.

The direction is read from the URL — a Spotify link migrates to YouTube Music,
and a YouTube Music link migrates to Spotify.

## Commands

| Command | What it does | Writes? |
|---|---|:---:|
| `login` | Connect Spotify and YouTube Music | no |
| `auth status` | What is connected, and whether it actually works | no |
| `playlists <service>` | List your playlists, with track counts | no |
| `plan <playlist>` | Match every track and write a report | **no** |
| `review` | Resolve the ambiguous matches yourself | no |
| `apply` | Create the playlist and add the accepted tracks | **yes** |
| `migrate <playlist>` | `plan` → `review` → `apply`, guided | yes |
| `sync <playlist>` | Migrate again, adding only what is new | yes |
| `report` / `runs` | Re-render a report, list past runs | no |
| `help` | All of the above, on one screen | no |

`plan` is always read-only, and `review` and `apply` default to your most
recent run — so the three can be typed in sequence with no arguments.

## Liked Songs

Your saved library migrates like anything else. It is not a playlist on either
service, so it has no URL to read a direction from — say where it is going:

```bash
migratify migrate liked --to ytmusic     # Spotify Liked Songs -> YouTube Music
migratify migrate liked --to spotify     # YouTube Music Liked -> Spotify
```

It lands as an ordinary playlist on the other side. **Nothing is ever written
into your saved library** — a playlist you did not want is one click to delete;
several hundred tracks added to a library are not.

## Syncing

Added songs to a playlist you already migrated? Do not migrate it again:

```bash
migratify sync <playlist-url>
```

Only the tracks that have not already reached the destination are matched, and
they go into the playlist the earlier migration created rather than a second
one. The link is remembered per destination service, so the same playlist can
be topped up on one service while still migrating in full to another.

---

## How it decides

Each source track produces several search queries against the destination, and
every candidate is scored 0–100 on artist similarity (0.40), title similarity
(0.30), duration proximity (0.20), album match and result type.

On top of that:

- **Artist veto** — artist similarity below 0.5 caps the candidate below the
  review floor, so a wrong-artist match cannot even be offered as a suggestion.
- **Version penalty** — −25 per mismatched version tag, so a studio recording
  will not match a live take, a remix or a karaoke version.
- **Duration** is the best cover detector there is: a cover almost never lands
  within ±5s of the original.

Then one of three outcomes, never a single guess:

| Score | Outcome |
|---|---|
| ≥ 88, no version mismatch, no near-tie | **Auto-accepted** |
| 70–88, or the top two within 4 points | **Queued for your review**, with candidates and reasons |
| < 70 | **Reported as not found** |

Indistinguishable candidates go to review too. If the scorer cannot tell two
recordings apart, taking the higher number would be a coin flip dressed up as
a decision.

## What carries across

|  | Tracks | Name | Description | Cover art |
|---|:---:|:---:|:---:|:---:|
| → Spotify | ✅ | ✅ | ✅ | ✅ |
| → YouTube Music | ✅ | ✅ | ✅ | ❌ |

YouTube Music exposes no API for uploading a playlist cover and generates one
from the track artwork instead. Migratify downloads the original to
`~/.migratify/covers/` and tells you where it is.

---

Reports in Markdown, CSV and JSON. Every resolved match is cached in SQLite,
so an interrupted run resumes and a re-run costs no new searches. Credentials
live only in `~/.migratify/`.

Full documentation in the [README](https://github.com/DecoudJuan/Migratify#readme).
