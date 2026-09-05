# Roadmap

Status of every piece of Migratify. Updated as work lands.

**Legend** — ✅ done · 🔨 in progress · ⬜ pending · 💭 considered, not committed

---

## Phase 1 — Foundations ✅

| | Item | Commit |
|---|---|---|
| ✅ | Project scaffolding: `pyproject.toml`, `.gitignore`, `.env.example` | `chore: scaffold project` |
| ✅ | `CLAUDE.md` — architecture, invariants, platform constraints | `docs: add detailed CLAUDE.md` |
| ✅ | `README.md` — usage, setup, matching design | `docs: rewrite README` |
| ✅ | Settings, `~/.migratify` paths, logging, tunable thresholds | `feat(config)` |
| ✅ | Provider-neutral models: `Track`, `Playlist`, `Candidate`, `MatchResult`, `Run` | `feat(models)` |
| ✅ | `MusicProvider` protocol + registry with URL direction detection | `feat(providers)` |

## Phase 2 — Authentication ✅

Goal: connecting an account means *signing in*. No developer dashboard, no API key.

| | Item | Commit |
|---|---|---|
| ✅ | Browser catalog: Chrome, Edge, Brave, Comet, Arc, Vivaldi, Opera, Chromium, Firefox, Zen, LibreWolf, Safari — macOS / Windows / Linux | `feat(auth): browser session capture` |
| ✅ | Session import from an installed browser (rookiepy → browser_cookie3 → generic Chromium reader) | `feat(auth): browser session capture` |
| ✅ | Login window driven against the user's own Chromium browser, persistent profile | `feat(auth): browser session capture` |
| ✅ | Spotify web-player session — token observed rather than reimplemented | `feat(auth): sign-in as default` |
| ✅ | YouTube Music cookie session | `feat(auth): sign-in as default` |
| ✅ | Fallback: Spotify OAuth PKCE with a user-registered app | `feat(auth): Spotify OAuth PKCE` |
| ✅ | Fallback: YouTube Music paste-headers and Google Cloud OAuth | `feat(auth): sign-in as default` |

## Phase 3 — Matching engine 🔨

The core of the project. Pure, offline-testable, direction-agnostic.

| | Item | Commit |
|---|---|---|
| ✅ | Normalization: comparable title core + extracted version tags, featured-artist relocation, `- Topic`/VEVO stripping | `feat(matching): normalization` |
| ✅ | Weighted scoring, artist veto, version penalty, ISRC short-circuit, ambiguity rule | `feat(matching): weighted scoring` |
| 🔨 | Search orchestration: run each provider's query strategies, dedupe, rank, decide | — |
| ⬜ | Golden set: same title/different artist, cover, live, remaster, displaced feat., translated title, sped-up, `- Topic` channel | — |

## Phase 4 — Providers 🔨

| | Item | Commit |
|---|---|---|
| 🔨 | Spotify: playlists, tracks, search with `isrc:`/`track:`/`artist:` filters, create, add, cover upload | — |
| ⬜ | YouTube Music: playlists, tracks, song/video search, create, batched add | — |

## Phase 5 — Persistence and metadata ⬜

| | Item |
|---|---|
| ⬜ | SQLite store: `runs`, `tracks`, `matches` — direction-keyed match cache, resume, idempotency |
| ⬜ | Artwork: cover download, JPEG reencode inside Spotify's hard 256 KB base64 limit, upload |
| ⬜ | Playlist metadata carry-over: name, description, provenance line |

## Phase 6 — Interface ⬜

| | Item |
|---|---|
| ⬜ | `migratify login` — one command, both services |
| ⬜ | `migratify auth status`, `migratify playlists` |
| ⬜ | `migratify plan` — read-only, writes a report, touches nothing |
| ⬜ | `migratify review` — interactive resolution of the ambiguous queue |
| ⬜ | `migratify apply` — create, upload metadata, add tracks |
| ⬜ | `migratify migrate` — guided plan → review → apply |
| ⬜ | Reports: markdown, CSV, JSON |

## Phase 7 — Claude Code skills ⬜

| | Item |
|---|---|
| ⬜ | `migratify-setup` — guided sign-in for both services |
| ⬜ | `migratify-migrate` — conversational end-to-end migration, either direction |
| ⬜ | `migratify-review` — resolve ambiguous matches by reasoning over discography and context, where fuzzy scoring cannot break a tie |
| ⬜ | `migratify-tune` — analyze a run's misses, propose threshold and normalization changes |

## Phase 8 — Quality ⬜

| | Item |
|---|---|
| ⬜ | GitHub Actions: ruff + pytest |
| ⬜ | `/init` pass to validate `CLAUDE.md` against the finished tree |
| ⬜ | First real end-to-end run, both directions, on a small playlist |
| ⬜ | Threshold calibration from that run |

---

## Beyond v0.1 💭

Not committed to, recorded so the reasoning is not lost.

| | Item | Note |
|---|---|---|
| 💭 | Liked Songs / saved library migration | Same engine, different source enumeration |
| 💭 | Sync mode — re-run against a playlist and add only what is new | The match cache already makes this cheap |
| 💭 | More providers: Tidal, Apple Music, Deezer | The point of `MusicProvider`. Should need zero changes to `matching/` |
| 💭 | `--strict` profile that reviews everything below 95 | For libraries where a wrong track is worse than a missing one |
| 💭 | Packaged `.exe` via PyInstaller | Only worth it if the CLI proves itself first |

---

## Known limitations

These are properties of the platforms, not bugs to be fixed.

- **YouTube Music cannot receive a playlist cover.** `ytmusicapi` exposes no
  endpoint for it; YTM generates artwork from the track covers instead.
  Migratify downloads the original to `~/.migratify/covers/` so it can be
  uploaded by hand.
- **Windows Chrome and Edge sessions cannot be imported.** App-Bound
  Encryption from v127 makes their cookie store unreadable to any other
  process. The login window exists for exactly this case.
- **Safari import needs Full Disk Access** for the terminal, on macOS.
- **The sign-in paths use non-public endpoints.** They are the ones that ask
  nothing of the user, and they will break someday. The official flows stay
  in the tree as fallbacks for that day.
