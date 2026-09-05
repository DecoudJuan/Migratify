---
name: migratify-setup
description: Connect Spotify and YouTube Music to Migratify, and diagnose a connection that is not working. Use when the user says "connect my accounts", "log in to Spotify", "set up Migratify", "migratify says I'm not connected", "my session expired", "conectá mis cuentas", "iniciar sesión en Spotify", "configurar Migratify", or when any other Migratify command fails with an authentication error.
---

# Connecting Migratify

Get both services connected with the least effort, and diagnose it when that
fails.

## The one thing to know first

**Signing in requires no developer app, no client ID and no API key.** If you
find yourself walking the user through the Spotify developer dashboard or the
Google Cloud console, you have taken a wrong turn — go back to `migratify
login`.

This matters more than it used to: since 2025 Spotify requires a **Premium**
subscription to enable Web API access on a new app. The sign-in path does not
touch that gate at all, which is precisely why it is the default.

## Normal flow

```bash
migratify login            # both services
migratify auth status      # confirm
```

`login` tries to import an existing session from an installed browser first,
and opens a login window only if that fails. Either way the user just signs
in the way they always do.

Then confirm it actually works — a stored session is not proof of a working
one:

```bash
migratify playlists spotify
migratify playlists ytmusic
```

## When it fails

Work through these in order. Stop at the first one that applies.

### "Signing in needs a browser Migratify can drive"

The `login` extra is not installed:

```bash
pip install "migratify[login]"
```

Only if the machine has no Chromium-family browser at all does it also need
`playwright install chromium`. Check first — `migratify auth status` lists
the browsers it found. If Chrome, Edge, Brave, Comet, Vivaldi, Opera or Arc
is in that list, skip the download.

### Nothing was imported from the installed browser

Expected on Windows, and not a failure. Chrome and Edge encrypt cookies with
App-Bound Encryption from v127, so no external process can read them.
Migratify falls back to its own login window automatically. Let it.

On macOS, import usually works. Safari specifically needs Full Disk Access
for the terminal: System Settings → Privacy & Security → Full Disk Access.

### The login window opened but was not detected as signed in

Ask the user to run it again. The window closes when it detects the session
cookie; if they were slow, or signed in on a second tab, it can miss it. The
profile is persistent, so the second attempt usually completes instantly.

To force a specific browser:

```bash
migratify login spotify --browser brave
```

### It worked before and stopped

The session expired. Re-run `migratify login <service>`. Add `--fresh` to skip
the import step and go straight to a login window.

## Fallbacks, and when they are worth it

Reach for these only after the sign-in path has genuinely failed.

| Command | Cost to the user | Use when |
|---|---|---|
| `migratify auth ytmusic --paste` | Copy headers from devtools | No window can be opened — SSH, container, headless box |
| `migratify auth spotify --pkce` | Register a Spotify app; **needs Premium** | The web-player session path breaks |
| `migratify auth ytmusic --oauth` | A Google Cloud OAuth client | Long-lived unattended automation |

For `--paste`: music.youtube.com → devtools → Network → click any request to
`/youtubei/v1/` → copy the full request headers → paste. Migratify accepts the
raw multi-line block, so nothing needs editing. It verifies `SAPISID` is
present rather than storing a signed-out session that would fail later with an
opaque error.

For `--pkce`: the app needs `http://127.0.0.1:8888/callback` as a redirect URI,
and `MIGRATIFY_SPOTIFY_CLIENT_ID` set in `.env`. There is no client secret —
PKCE does not use one, so never ask the user for theirs.

## Never do

- Commit or echo a credential. Everything lives in `~/.migratify/`, which is
  outside the repository by design.
- Suggest registering a developer app as a first step.
- Report a service as connected because a file exists. Confirm with
  `migratify playlists <service>`.
