"""Build a deliberately hostile playlist for testing migrations.

Any playlist will exercise the happy path. This one is chosen to break things:
every entry is here because it represents a specific way playlist migration
goes wrong, and the comments say which.

Run it on either service, then migrate the result to the other and read the
report. A track that lands wrong tells you more than fifty that land right.

    python scripts/seed_test_playlist.py spotify
    python scripts/seed_test_playlist.py ytmusic
"""

from __future__ import annotations

import sys

from migratify.config import force_utf8_output, setup_logging
from migratify.models import Provider
from migratify.providers.base import ProviderError, SearchQuery
from migratify.providers.registry import get_provider

PLAYLIST_NAME = "Migratify Test Bench"
PLAYLIST_DESCRIPTION = (
    "Deliberately hard cases for testing playlist migration: same titles by "
    "different artists, heavily covered songs, remixes, live takes, remasters, "
    "and regional releases with thin catalog coverage."
)

#: (search term, why it is here). The reason is the point -- without it this is
#: just a playlist, and nobody can tell whether a result is a bug or expected.
CORPUS: list[tuple[str, str]] = [
    # --- same title, entirely different song ---------------------------------
    ("Hurt Johnny Cash", "same title as a Nine Inch Nails song; the artist veto must hold"),
    ("Crazy Gnarls Barkley", "at least three famous unrelated songs share this title"),
    ("Alive Pearl Jam", "common title; several plausible wrong answers"),
    # --- heavily covered -----------------------------------------------------
    ("Creep Radiohead", "one of the most covered songs alive; duration is the only guard"),
    ("Hallelujah Jeff Buckley", "the cover is more famous than the original"),
    ("Nothing Else Matters Metallica", "endless live and orchestral versions"),
    # --- remix and edit traps ------------------------------------------------
    ("Levels Avicii", "the remix is often ranked above the original"),
    ("Sandstorm Darude", "countless edits and re-uploads"),
    ("Blue Monday New Order", "album, single and remix versions differ by minutes"),
    # --- live versions dominate search ---------------------------------------
    ("Wonderwall Oasis", "live takes crowd the results"),
    ("Comfortably Numb Pink Floyd", "the Pulse live version is as well known as the studio one"),
    # --- remaster labelling disagreement -------------------------------------
    ("Come Together The Beatles", "labelled a remaster on one service and not the other"),
    ("Bohemian Rhapsody Queen", "many remasters, all the same performance"),
    # --- features credited differently ---------------------------------------
    ("Cold Heart Elton John Dua Lipa", "feature in artists[] on one side, in the title on the other"),
    ("Levitating Dua Lipa DaBaby", "feature present on one service, absent on the other"),
    # --- accents and non-ASCII ----------------------------------------------
    ("Corazón Espinado Santana Maná", "accents must not break normalization"),
    ("Déjà Vu Olivia Rodrigo", "accented title, common word"),
    # --- thin catalogue coverage, regional -----------------------------------
    ("El Mató a un Policía Motorizado Mi Próximo Movimiento", "Argentine indie; sparse on YTM"),
    ("Las Ligas Menores Ahora", "small label; a real miss is a valid outcome here"),
    ("Usted Señálemelo Frágil", "regional release, inconsistent metadata"),
    ("Bandalos Chinos Demasiado Tarde", "regional, commonly mislabelled"),
    ("Silvio Rodríguez Ojalá", "many live recordings, few studio ones"),
    # --- long tail -----------------------------------------------------------
    ("Godspeed You Black Emperor Storm", "long instrumental; durations vary by release"),
    ("Boards of Canada Roygbiv", "electronic, many re-uploads on YouTube"),
]


def main(target: str) -> int:
    force_utf8_output()
    setup_logging(False)

    try:
        provider_name = Provider(target.lower())
    except ValueError:
        print(f"Unknown service {target!r}. Use 'spotify' or 'ytmusic'.")
        return 1

    provider = get_provider(provider_name)
    print(f"Building {PLAYLIST_NAME!r} on {provider_name.label}\n")

    found: list[str] = []
    missing: list[str] = []

    for term, reason in CORPUS:
        try:
            # "songs" matters on YouTube Music: an unfiltered search returns
            # albums and stray uploads, which carry no artist and would seed
            # the bench with entries that are not tracks at all.
            results = provider.search(
                SearchQuery(term, "seed", result_filter="songs"), limit=1
            )
        except ProviderError as exc:
            print(f"  !  {term}: {exc}")
            missing.append(term)
            continue

        if not results:
            print(f"  -  {term}  ({reason})")
            missing.append(term)
            continue

        track = results[0]
        found.append(track.id)
        print(f"  +  {track.display}")

    if not found:
        print("\nNothing was found; not creating an empty playlist.")
        return 1

    print(f"\nCreating the playlist with {len(found)} tracks...")
    playlist_id = provider.create_playlist(PLAYLIST_NAME, PLAYLIST_DESCRIPTION, public=False)
    added = provider.add_tracks(playlist_id, found)

    print(f"Added {added} tracks.")
    if missing:
        print(f"{len(missing)} not found on this service (expected for the regional ones).")
    print(f"\n{provider.playlist_url(playlist_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "spotify"))
