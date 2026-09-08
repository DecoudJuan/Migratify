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

## Phase 3 — Matching engine ✅

The core of the project. Pure, offline-testable, direction-agnostic.

| | Item | Commit |
|---|---|---|
| ✅ | Normalization: comparable title core + extracted version tags, featured-artist relocation, `- Topic`/VEVO stripping | `feat(matching): normalization` |
| ✅ | Weighted scoring, artist veto, version penalty, ISRC short-circuit, ambiguity rule | `feat(matching): weighted scoring` |
| ✅ | Search orchestration: run each provider's query strategies, dedupe, rank, decide | `feat(matching): search orchestration` |
| ✅ | Golden set: same title/different artist, cover, live, remaster, displaced feat., sped-up, `- Topic` channel | `test(matching): golden set` |

## Phase 4 — Providers ✅

| | Item | Commit |
|---|---|---|
| ✅ | Spotify: playlists, tracks, search with `isrc:`/`track:`/`artist:` filters, create, add, cover upload | `feat(providers): both` |
| ✅ | YouTube Music: playlists, tracks, song/video search, create, batched add | `feat(providers): both` |

## Phase 5 — Persistence and metadata ✅

| | Item |
|---|---|
| ✅ | SQLite store: `runs`, `tracks`, `matches` — direction-keyed match cache, resume, idempotency |
| ✅ | Artwork: cover download, JPEG reencode inside Spotify's hard 256 KB base64 limit, upload |
| ✅ | Playlist metadata carry-over: name, description, provenance line |

## Phase 6 — Interface ✅

| | Item |
|---|---|
| ✅ | `migratify login` — one command, both services |
| ✅ | `migratify auth status`, `migratify playlists`, `migratify runs` |
| ✅ | `migratify plan` — read-only, writes a report, touches nothing |
| ✅ | `migratify review` — interactive resolution of the ambiguous queue |
| ✅ | `migratify apply` — create, upload metadata, add tracks, idempotent |
| ✅ | `migratify migrate` — guided plan → review → apply |
| ✅ | Reports: markdown, CSV, JSON |

## Phase 7 — Claude Code skills ✅

| | Item |
|---|---|
| ✅ | `migratify-setup` — guided sign-in for both services |
| ✅ | `migratify-migrate` — conversational end-to-end migration, either direction |
| ✅ | `migratify-review` — resolve ambiguous matches by reasoning over discography and context, where fuzzy scoring cannot break a tie |
| ✅ | `migratify-tune` — analyze a run's misses, propose threshold and normalization changes |

## Phase 8 — Quality ✅

| | Item |
|---|---|
| ✅ | GitHub Actions: ruff + pytest on Linux, macOS and Windows |
| ✅ | 60 offline tests — golden set plus an end-to-end pipeline over a fake provider |
| ✅ | `/init` pass to validate `CLAUDE.md` against the finished tree |
| ✅ | First real end-to-end run, both directions, on the hostile test bench |
| ✅ | Threshold calibration from that run — no threshold changed; two real bugs found instead |

### What the first real run measured

A deliberately hostile 24-track playlist (`scripts/seed_test_playlist.py`),
free Spotify account, both directions:

| Direction | Auto | Review | Not found | Applied |
|---|---:|---:|---:|---|
| Spotify → YouTube Music | 24 | 0 | 0 | 24 written |
| YouTube Music → Spotify | 22 | 2 | 0 | 22 written, 2 correctly left out |

Both directions were applied and read back: name, description, track count and
order all intact. The three cases that went to review before the duplicate
rule landed all resolved to the exact right match afterwards — Blue Monday to
the original rather than the '88 remix, Come Together to the 2009 remaster
rather than the 2019 mix, Levitating keeping its feature credit.

The two remaining review cases in the reverse direction are genuinely
ambiguous: competing masters seconds apart, not confusion between songs.

The traps behaved: `Hurt — Johnny Cash` resolved to Johnny Cash and not to the
real catalog artist *The Ghost of Johnny Cash*; radio edits matched radio
edits; remasters still matched their originals.

Getting there took two bug fixes rather than any tuning — Spotify names track
duration differently in search than in playlists, and the ambiguity rule was
treating duplicate catalog listings of one recording as a tie.

## Phase 9 — Beyond one playlist ✅

The three items the CLI had earned by proving itself on real accounts.

| | Item |
|---|---|
| ✅ | Liked Songs / saved library as a source, both directions, under one neutral `LIKED` id |
| ✅ | `migratify sync` — re-run a migration and add only what is new, per destination |
| ✅ | Standalone binary via PyInstaller, built and verified per platform in CI |

### Liked Songs

YouTube Music was nearly free: it addresses the saved library as a playlist
with the fixed id `LM`, so the work was translating the neutral id at the
edges.

Spotify was not. Pathfinder rejects `spotify:collection:tracks` outright —
*"Argument <uri> for field /playlistV2 is not of type [PLAYLIST,
PLAYLIST_V2]"* — and no liked-songs read operation appears in a capture of the
Liked Songs page at all, because the list does not come over pathfinder. It
comes from the **collection service**: `POST spclient /collection/v2/paging`
with `set: "collection"`, which answers with the whole set in one shot and no
pagination cursor. That set is mixed — saved albums and liked tracks share it
— and carries nothing but URIs and an `added_at`, so the metadata comes from
`decorateContextTracks`, an *observed* operation rather than a pinned one.

Found by watching the real player rather than guessing, which is the same
principle the rest of the Spotify path is built on.

**Nothing is written into a saved library.** Liked songs land as an ordinary
playlist on the destination: a playlist you did not want is one click to
delete, several hundred tracks added to a library are not.

### Sync

Needed no new tables. `runs` already records which destination playlist a run
created and `run_tracks` already flags what was written, so sync is two
queries over what was there.

The link is keyed on the **destination service**, which is what makes it keep
working as providers are added: a playlist already carried to YouTube Music
needs only its new tracks there, and still migrates in full the first time it
goes anywhere it has never been.

Only *written* tracks are skipped. A track left in review, skipped, or not
found is offered again on the next sync — the same reasoning that keeps misses
out of the match cache: catalogs change.

### The binary

A folder rather than a one-file bundle, because `--onefile` unpacks itself on
every invocation and this is a CLI you run several times in a row. It carries
the browser automation, because signing in *is* the browser automation and a
build without it can only reach fallbacks that a free Spotify account cannot
use. It does not carry a browser: Migratify drives the one already installed.

`scripts/build_exe.py` builds *and then runs* the result — a bundle missing a
data file builds perfectly and fails on first launch, so building without
checking proves nothing. Verified on Windows: `auth status` reported both
services working against live APIs, and a run against a throwaway
`MIGRATIFY_HOME` drove a real browser through the bundled Playwright.

### The interactive prompt

| | Item | Commit |
|---|---|---|
| ✅ | A bare `migratify` opens a prompt instead of printing help and exiting | `feat(cli): open a prompt when Migratify is opened, not typed` |
| ✅ | Banner: a vinyl pet, the version, which services are connected, how many runs are stored | same |
| ✅ | ASCII fallback for consoles that cannot encode the block characters | same |

A packaged binary gets double-clicked, and `no_args_is_help` makes that look
broken: the window closes before the help can be read. The prompt is the same
Typer app dispatched line by line, so nothing about `plan` being read-only or
`apply` being the only writer depends on where the words came from. It only
opens on a terminal — piped or redirected, help is still the right answer, and
`scripts/build_exe.py` now checks that path so a hang can never ship.

---

## Phase 10 — The landing page ✅

| | Item |
|---|---|
| ✅ | `web/` — a static bilingual landing page, English at `/` and Spanish at `/es/` |
| ✅ | SEO: canonical + `hreflang` pair, Open Graph and Twitter cards, `SoftwareApplication` and `FAQPage` JSON-LD, `robots.txt`, `sitemap.xml` |
| ✅ | `scripts/build_og.py` — the 1200×630 social card, drawn with Pillow |
| ✅ | `.github/workflows/pages.yml` — published to GitHub Pages on any push that touches `web/` |

Two files, one stylesheet and about two kilobytes of script. No framework and
no build step: a page that exists to hand someone a download does not need a
toolchain, and the repository stays a Python project with a folder of HTML in
it rather than a Python project with a JavaScript project inside it.

**Bilingual because the search terms are.** *"Transfer Spotify playlist to
YouTube Music"* and *"migrar playlists de Spotify a YouTube Music"* are two
different queries with two different results pages, and the tool answers both.
The English page is `x-default`, and the two point at each other with
`hreflang`.

**The terminal is redrawn, not screenshotted.** The banner, the panels and the
progress bar are SVG and CSS instead of an image, so the text is real,
selectable and indexable — and it survives the fonts. Reproducing the Rich
output with box-drawing characters looked right in a terminal and fell apart in
a browser: whichever font supplies the glyphs the webfont is missing brings its
own advance width, so a border made of `─` never lines up with the ASCII beside
it.

**Every path is relative.** The site is a *project* page, so it is served from
`decoudjuan.github.io/Migratify/` rather than a domain root, and a
root-absolute `/assets/style.css` would 404 only in production. Relative paths
also mean the same files work unchanged if it ever moves to its own domain.
Pages serves no clean URLs either, so Spanish is linked as `es/`, with the
slash, everywhere — including the `hreflang` pair and the sitemap.

**What it claims, it claims exactly.** The weights, the thresholds, the veto
cap, the platform limits and the download size on the page are the ones in
`score.py`, `config.Thresholds` and the published release assets. A landing
page that oversells the matcher would undermine the only promise the project
makes — `web/README.md` says so, next to the `grep` that finds them.

---

## Beyond v0.1 💭

Not committed to, recorded so the reasoning is not lost.

| | Item | Note |
|---|---|---|
| 💭 | More providers: Tidal, Apple Music, Deezer | The point of `MusicProvider`. Should need zero changes to `matching/`, and `sync` already keys per destination |
| 💭 | `--strict` profile that reviews everything below 95 | For libraries where a wrong track is worse than a missing one |
| 💭 | Scheduled sync | `sync` is the hard half; the rest is a scheduler, which the OS already has |
| ⬜ | Spotify cover upload | The one write endpoint never observed from a real session; currently 404s. Re-run `scripts/discover_spotify_writes.py` and change a playlist image while it watches |

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
- **Google blocks sign-in inside automated browsers.** Deliberate account
  protection, not a bug to defeat. Migratify launches an ordinary browser
  process instead and reattaches to the profile afterwards.
- **On Windows, no Chromium browser's cookies can be imported.** App-Bound
  Encryption from v127 covers the whole family — Brave, Comet, Vivaldi and Arc
  included, not just Chrome and Edge. Firefox still works.
- **The sign-in paths use non-public endpoints.** They are the ones that ask
  nothing of the user, and they will break someday. The official flows stay
  in the tree as fallbacks for that day.
- **Spotify requires Premium for Web API access on a registered app** (2025).
  **Verified on 2026-09-05**, not assumed: a free account registered an app,
  completed the PKCE flow and received a token with all six scopes granted --
  and every Web API call returned 403, *"Active premium subscription required
  for the owner of the app."* The gate is on the app owner's subscription and
  no app configuration avoids it. This is why signing in is the default and
  `--pkce` is the fallback; the primary path registers no app and is
  unaffected.
