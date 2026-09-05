---
name: migratify-review
description: Resolve the ambiguous matches Migratify could not decide on its own, reasoning about discography and release history rather than fuzzy scores. Use when the user says "resolve the review queue", "pick the right versions", "decide the ambiguous ones for me", "resolvé los dudosos", "elegí vos las versiones", or after a plan reports tracks needing review.
---

# Resolving the review queue

This is the skill with the clearest reason to exist. The scorer compares
strings and numbers. You can reason about *the music*: which album a song
belongs to, whether a 2011 remaster and a 1975 original are the same
recording, whether an artist has two songs with the same title, whether a
duration gap means a radio edit or a different song entirely.

Use that. **Do not just pick the highest score** — if score alone were enough,
the track would not be in this queue.

## Getting the data

```bash
migratify report --format json
```

The JSON keeps every candidate with its full breakdown, which is what you need
to reason. The Markdown report is for humans; read the JSON.

Each pending entry has `decision: "review"`, `chosen_id: null`, a `source`
track and a `candidates` list. Each candidate carries `score` and a
`breakdown` with per-signal values: `artist`, `title`, `duration`, `album`,
`kind`, `version_penalty`, `artist_vetoed`, `isrc_exact`.

## How to decide

Work in this order. Stop at the first one that settles it.

1. **Duration.** The strongest objective signal. Within 2 seconds of the
   source is almost certainly the same recording; more than 20 seconds off is
   almost certainly not, whatever the title says.
2. **Album.** If the source is the album cut and one candidate is on that same
   album, that is usually the answer. Compilations and greatest-hits releases
   often carry a different master.
3. **Artist identity.** Check whether these are genuinely the same artist, not
   just a similar name. Two different bands share a name more often than
   people expect.
4. **Version tags.** A studio source must not take a live, acoustic, remix,
   instrumental or karaoke candidate. If every candidate carries a version
   the source does not, the right answer is to skip.
5. **Release era.** A 1960s song with a candidate uploaded as a 2019 recording
   by a name you do not recognize is a cover.

## When to skip

Skipping is a real answer, and often the right one. Skip when:

- Every candidate is a cover, a live take, or a karaoke track.
- The candidates are all plausible and nothing distinguishes them — an
  arbitrary pick is worse than an honest gap.
- The source is a local file, a podcast episode, or something that has no
  reason to exist in the destination catalog.

Tell the user what you skipped and why. A short reason per track is enough.

## Applying decisions

`migratify review` is interactive and expects a human at the keyboard, so
drive the store directly for a batch resolution:

```python
from migratify.store import Store
from migratify.models import Decision

with Store() as store:
    run = store.latest_run()
    results = store.load_results(run.id)
    for position, result in enumerate(results, start=1):
        if result.decision is not Decision.REVIEW or result.chosen_id:
            continue
        # ... your reasoning picks a candidate, or None to skip
        result.chosen_id = "<video or track id>"   # or leave None
        result.resolved_by = "manual"
        if result.chosen_id is None:
            result.decision = Decision.SKIPPED
        store.save_result(run.id, position, result)
    store.refresh_counters(run.id)
```

Setting `resolved_by = "manual"` matters: it records that a human decision was
made, and it caches the choice so the same track is never asked about again in
any future migration.

Then hand back to the user:

```bash
migratify apply
```

## Report what you did

For each track, one line: what you chose and the reason that settled it.
"Took the Pablo Honey cut — duration matched to the second and the album lines
up" is useful. "Score 84" is not; the user already had that number and it is
what left the track in the queue.
