---
name: migratify-tune
description: Diagnose why a Migratify run matched poorly and propose threshold or normalization changes. Use when the user says "too many tracks went to review", "it missed songs it should have found", "it matched the wrong version", "the matching is bad", "muchas quedaron en revisión", "no encontró canciones que existen", "está matcheando mal", or after a run with a low auto-accept rate.
---

# Tuning the matching engine

Diagnose from evidence, change one thing, and prove it with the golden set.

## First, decide whether there is a problem

A migration is not broken because some tracks did not match. Compare against
what is normal:

| Signal | Healthy | Worth investigating |
|---|---|---|
| Auto-accepted, mainstream playlist | 85–95% | below 75% |
| Auto-accepted, obscure or regional | 60–80% | below 50% |
| Sent to review | 5–15% | above 25% |
| Not found | 2–10% | above 15% |

A playlist of local files, podcasts or region-locked releases will legitimately
score badly. Check what is actually in it before touching a weight.

## Get the evidence

```bash
migratify report --format json
```

Then classify the failures — the fix depends entirely on which of these it is:

- **Wrong version accepted** (a live take, a remix, a sped-up edit landed in
  the playlist). The most serious kind. Version-tag detection missed a
  pattern.
- **Correct match sent to review.** Look at the breakdown: which signal is
  dragging it down?
- **Findable track reported missing.** Usually a search problem, not a scoring
  problem — the right candidate was never retrieved to be scored.
- **Everything close together.** Many near-ties means the candidates really
  are similar; that is the queue working, not a bug.

## Fixes, in order of preference

### Prefer a normalization fix over a threshold change

If one pattern explains several failures, fix the pattern. It is targeted and
it cannot loosen anything else.

Common gaps in `src/migratify/matching/normalize.py`:

- A version qualifier in a language the patterns miss — `ao vivo`, `en directo`,
  `akustisch`, `セルフカバー`. Add it to `_VERSION_PATTERNS`.
- A decoration not being stripped — a label suffix, a regional tag,
  `(Video Oficial)`. Add it to `_NOISE`.
- An artist-name suffix poisoning comparison. Add it to `_ARTIST_NOISE`.

### Then a search fix

If the right candidate never appeared in the results at all, no scoring change
can help. Add a query strategy to that provider's `build_queries`. Symptoms:
the `candidates` list is short, or full of clearly unrelated tracks.

### Threshold changes last, and one at a time

In `.env`:

```bash
MIGRATIFY_AUTO_ACCEPT=88      # lower accepts more; raises the risk of a wrong track
MIGRATIFY_REVIEW_FLOOR=70     # lower surfaces more candidates for review
MIGRATIFY_AMBIGUITY_MARGIN=4  # raise to send more near-ties to review
```

Direction matters, and the two are not symmetric in cost:

- **Too much review, matches look right** → lower `MIGRATIFY_AUTO_ACCEPT` to
  84 or 85. Do not go below 80; that is where wrong versions start slipping
  through.
- **A wrong track got in** → raise `MIGRATIFY_AUTO_ACCEPT` to 92 and raise
  `MIGRATIFY_AMBIGUITY_MARGIN` to 6. Accept the extra review work. A wrong
  track is a worse outcome than a tedious one.
- **Good matches reported as missing** → lower `MIGRATIFY_REVIEW_FLOOR` to 60
  so they surface as candidates instead of vanishing.

Never lower the artist veto. It is the single defense against the commonest
failure in playlist migration, and everything else in the design leans on it.

## Prove the change

```bash
pytest
```

The golden set must stay green. If a case now fails, the change is too loose —
that case is there because it is a real way this goes wrong.

If a case legitimately needs to change, change the expectation in the same
commit as the code and explain why in the message.

Then re-run against the real playlist. Cached matches are not re-searched, so
clear them to see the real effect:

```bash
migratify plan <url> --no-cache
```

## Report

Say which failure class you found, the one change you made, and the before and
after rates. If you changed a threshold rather than a pattern, say why the
pattern fix was not available — that is the more interesting half of the
answer.
