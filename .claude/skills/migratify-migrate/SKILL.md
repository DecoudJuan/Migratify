---
name: migratify-migrate
description: Move a playlist between Spotify and YouTube Music in either direction, end to end. Use when the user gives a playlist URL and asks to move, migrate, copy or transfer it, or says "pass this playlist to YouTube Music", "copy this to Spotify", "migrate my playlist", "pasá esta playlist a YouTube Music", "mové esta lista a Spotify", "migrá esta playlist".
---

# Migrating a playlist

Run the migration and report honestly on what happened.

## The rule this workflow exists to protect

**Never let a wrong track into the destination playlist.** Everything below
follows from that. When in doubt, leave a track out and say so — a missing
song is a visible, fixable problem; a wrong song silently sitting in a
playlist is not.

Concretely: never resolve a review by picking the highest score just to finish.
If the scorer could not decide, it is because the signals genuinely conflict.

## Flow

### 1. Check the connection

```bash
migratify auth status
```

Not connected? Hand off to the `migratify-setup` skill rather than improvising.

### 2. Plan — this writes nothing

```bash
migratify plan <playlist-url>
```

Direction is detected from the URL: a Spotify link migrates to YouTube Music
and vice versa. Pass `--to spotify` or `--to ytmusic` only for a bare ID.

Nothing is created on either service by this command. It is always safe to
run, including on a playlist the user is unsure about.

### 3. Read the report before continuing

The path is printed at the end of `plan`. **Actually read it.** This is the
step that makes the tool trustworthy, and skipping it forfeits the whole point
of a dry run.

Look for:

- **Auto-accepted matches that look wrong.** Scan the Matched table. A title
  that changed meaningfully, or an artist that is not the source artist, is
  worth flagging to the user even though the scorer accepted it.
- **Misses that look findable.** A well-known song reported as not found
  usually means the destination lists it under a different title — a
  translation, a different transliteration, an alternate single name. Say so,
  and offer to search it manually.
- **The auto-accept rate.** Below roughly 70% on a mainstream playlist
  suggests a tuning problem rather than a catalog problem. Offer
  `migratify-tune`.

### 4. Review the ambiguous ones

```bash
migratify review
```

If the user would rather you decide, use the `migratify-review` skill — it can
reason about discographies and release history in ways the fuzzy scorer
cannot. Do not just take the top score.

### 5. Apply

```bash
migratify apply
```

The only command that writes. It says up front how many tracks it will add and
how many unresolved matches it will leave out. Confirm the user is content
with that number before proceeding — leaving tracks out is correct behaviour,
but it should never be a surprise.

## Reporting the result

Give the user four numbers and the link: added, left for review, not found,
and the destination URL.

Then state the cover-art outcome plainly, because it differs by direction:

- **Toward Spotify** — name, description and cover all transfer.
- **Toward YouTube Music** — name and description transfer; the cover does
  not. YouTube Music has no API for playlist artwork and generates one from
  the track covers instead. Migratify downloads the original to
  `~/.migratify/covers/`; tell the user the path so they can set it by hand.

That last one is a platform limitation, not a bug. Do not offer to fix it and
do not go looking for a workaround — there is no endpoint to call.

## Notes

- A second run over the same playlist is cheap: every resolved match is cached,
  so it costs almost no searches.
- `apply` is safe to re-run. It adds only what is not already written rather
  than duplicating the playlist.
- An interrupted `plan` resumes; results are persisted per track as they
  resolve.
- Large playlists take a while — searching is the slow part, and it is
  rate-limited on both services. That is expected, not a hang.
