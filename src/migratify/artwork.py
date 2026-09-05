"""Playlist cover art: download, reencode, upload.

A migrated playlist that arrives without its name and cover does not feel
migrated, so this is not a nice-to-have.

The support matrix is asymmetric and that is a platform fact, not an omission:

======================  ======  ===========  =====
Destination             Name    Description  Cover
======================  ======  ===========  =====
Spotify                 yes     yes          yes
YouTube Music           yes     yes          **no**
======================  ======  ===========  =====

``ytmusicapi`` exposes no playlist-cover endpoint, and neither does YouTube
Music outside its own web client. So going that way we still download the
original and tell the user where it is, which is the only path that exists.

Spotify's limit is the awkward part: 256 KB **after** base64 encoding, which
is 4/3 the size of the bytes. Encoders do not let you request an exact output
size, so we walk quality and dimensions down until it fits.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx

from migratify.config import get_logger, get_settings

log = get_logger(__name__)

#: Spotify's hard limit, measured on the base64 payload.
MAX_ENCODED_BYTES = 256 * 1024

#: Base64 inflates by 4/3, so this is the byte budget before encoding.
MAX_RAW_BYTES = (MAX_ENCODED_BYTES * 3) // 4

#: Quality then size. Dropping quality preserves the dimensions a cover is
#: judged by, so it is tried first and only exhausted quality forces a resize.
_QUALITY_STEPS = (90, 82, 74, 66, 58, 50, 42)
_SIZE_STEPS = (1000, 800, 640, 500, 400, 300)


def download(url: str, run_id: str) -> Path | None:
    """Fetch a cover to ``~/.migratify/covers/<run id>.jpg``.

    Returns None on any failure: a missing cover must never fail a migration
    that otherwise succeeded.
    """
    settings = get_settings()
    settings.covers_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.covers_dir / f"{run_id}.jpg"

    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("Could not download the playlist cover: %s", exc)
        return None

    destination.write_bytes(response.content)
    log.debug("Cover saved to %s", destination)
    return destination


def encode_for_spotify(image_path: Path) -> bytes:
    """Reencode an image to a JPEG that fits Spotify's 256 KB base64 limit.

    Raises only if even the smallest, lowest-quality rendering is too large,
    which in practice means the input was not an image at all.
    """
    from PIL import Image

    with Image.open(image_path) as image:
        # Covers can arrive as PNG with transparency; JPEG has no alpha
        # channel, so flatten rather than let the encoder guess.
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        for size in _SIZE_STEPS:
            candidate = image.copy()
            candidate.thumbnail((size, size), Image.LANCZOS)

            for quality in _QUALITY_STEPS:
                buffer = io.BytesIO()
                candidate.save(buffer, format="JPEG", quality=quality, optimize=True)
                data = buffer.getvalue()
                if len(data) <= MAX_RAW_BYTES:
                    log.debug(
                        "Cover encoded at %dpx q%d (%d KB, %d KB base64)",
                        size, quality, len(data) // 1024,
                        len(base64.b64encode(data)) // 1024,
                    )
                    return data

    raise ValueError(
        f"Could not compress {image_path.name} under Spotify's 256 KB cover limit."
    )


def transfer(
    cover_url: str | None,
    run_id: str,
    target,
    target_playlist_id: str,
) -> tuple[bool, Path | None]:
    """Move a cover to the destination, as far as the destination allows.

    Returns ``(uploaded, local_path)``. The path is returned even when upload
    is impossible, so the caller can tell the user where the image is rather
    than silently dropping it.
    """
    if not cover_url:
        return False, None

    local = download(cover_url, run_id)
    if local is None:
        return False, None

    if not target.supports_cover_upload:
        # Not an error. YouTube Music generates artwork from the track covers
        # and offers no way to override it.
        return False, local

    try:
        jpeg = encode_for_spotify(local)
        uploaded = target.set_cover(target_playlist_id, jpeg)
    except Exception as exc:  # a cover must never fail an otherwise good migration
        log.warning("Could not upload the playlist cover: %s", exc)
        return False, local

    return uploaded, local


def describe_description(source_name: str, source_provider_label: str) -> str:
    """A provenance line appended to the migrated playlist's description.

    Short and factual. It answers the question someone will ask in six months
    looking at two near-identical playlists on two services.
    """
    return f"Migrated from {source_provider_label} with Migratify."
