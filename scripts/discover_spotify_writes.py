"""Learn how the Spotify web player creates and edits playlists.

The read side of the internal API was straightforward to replicate: pathfinder
announces its operations, so watching the player load a page tells you
everything. Writes are not announced anywhere, and guessing the payload
produced a bare HTTP 400 with no explanation.

So this does the same thing that worked for reads and for the token: it opens
the real player, asks a human to perform the action, and records what went
over the wire. Then the provider replays that shape.

It deliberately records only the request **method, URL and body** -- never
headers, which carry the session credential and are captured separately by
:mod:`migratify.auth.spotify_web`.

    python scripts/discover_spotify_writes.py

Follow the instructions in the terminal. Nothing is written to your account
except the playlist you create yourself.
"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

from migratify.auth.browser import _launch_kwargs, profile_dir
from migratify.config import force_utf8_output

#: Hosts that carry playlist mutations. pathfinder is included because a
#: mutation may well be a GraphQL operation rather than an spclient call --
#: which of the two it is, is exactly what we are here to find out.
INTERESTING = ("spclient", "api-partner.spotify.com")

#: Reads are noisy and irrelevant here.
SKIP_SUBSTRINGS = (
    "/extended-metadata",
    "/remote-config-resolver",
    "/ads/",
    "/gander/",
    "/pendragon/",
    "/social-connect",
    "/library-import",
)

INSTRUCTIONS = """
A browser window is open on your Spotify library.

Please do these three things in it, slowly:

  1. Create a new playlist.
  2. Rename it to something, and give it a description.
  3. Search for any song and add it to that playlist.

Then close the browser window.

Everything the player sends will be recorded so Migratify can replay the same
shape. Only methods, URLs and request bodies are recorded -- never headers.
"""


def main() -> int:
    force_utf8_output()
    captured: list[dict] = []

    def on_request(request) -> None:
        url = request.url
        if not any(host in url for host in INTERESTING):
            return
        if request.method == "GET" or any(s in url for s in SKIP_SUBSTRINGS):
            return

        body = None
        try:
            body = request.post_data_json
        except Exception:
            try:
                body = (request.post_data or "")[:2000]
            except Exception:
                body = "<unreadable>"

        entry = {"method": request.method, "url": url.split("?")[0], "body": body}

        # GraphQL mutations all share one URL, so keep the operation name.
        if isinstance(body, dict) and body.get("operationName"):
            entry["operation"] = body["operationName"]
            entry["sha256"] = (
                (body.get("extensions") or {}).get("persistedQuery") or {}
            ).get("sha256Hash")

        captured.append(entry)

    print(INSTRUCTIONS)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir()),
            headless=False,
            args=["--no-first-run", "--no-default-browser-check"],
            **_launch_kwargs(),
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.on("request", on_request)
            page.goto("https://open.spotify.com/collection/playlists",
                      wait_until="domcontentloaded")

            # The window closing is the signal that the human is done.
            while context.pages:
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    break
        finally:
            context.close()

    if not captured:
        print("\nNothing was captured. Was the window closed before creating anything?")
        return 1

    print(f"\nCaptured {len(captured)} write requests:\n")
    for entry in captured:
        label = entry.get("operation") or entry["url"].split("spotify.com")[-1]
        print(f"  {entry['method']}  {label}")

    out = "spotify_writes.json"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(captured, handle, indent=2, ensure_ascii=False)
    print(f"\nFull detail written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
