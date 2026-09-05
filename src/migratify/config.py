"""Settings, filesystem paths and logging.

Everything user-specific -- credentials, the match cache, downloaded covers,
generated reports -- lives under ``~/.migratify`` (override with
``MIGRATIFY_HOME``). Nothing is ever written into the repository.
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from rich.logging import RichHandler

# --- Spotify OAuth ----------------------------------------------------------

#: Read scopes plus the write scopes needed when Spotify is the *destination*,
#: plus ``ugc-image-upload`` for playlist cover art.
SPOTIFY_SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
    "playlist-modify-private",
    "playlist-modify-public",
    "ugc-image-upload",
)

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _load_dotenv() -> None:
    """Load ``.env`` from the current directory if present.

    Deliberately dependency-free: we only need ``KEY=value`` lines, and adding
    python-dotenv for that would be overkill.
    """
    path = Path.cwd() / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Thresholds:
    """Decision boundaries for the matching engine.

    A candidate is auto-accepted only when it clears ``auto_accept`` *and* has
    no version-tag mismatch. Anything from ``review_floor`` up -- or any result
    where the top two candidates are within ``ambiguity_margin`` of each other
    -- goes to the review queue rather than being guessed at.
    """

    auto_accept: float = 88.0
    review_floor: float = 70.0
    ambiguity_margin: float = 4.0
    max_candidates_shown: int = 5


@dataclass(frozen=True)
class Settings:
    home: Path
    spotify_client_id: str | None
    spotify_redirect_uri: str
    ytm_client_id: str | None
    ytm_client_secret: str | None
    thresholds: Thresholds = field(default_factory=Thresholds)

    # -- derived paths -------------------------------------------------------

    @property
    def spotify_token_file(self) -> Path:
        return self.home / "spotify_token.json"

    @property
    def ytm_oauth_file(self) -> Path:
        return self.home / "ytm_oauth.json"

    @property
    def ytm_browser_file(self) -> Path:
        return self.home / "ytm_browser.json"

    @property
    def db_file(self) -> Path:
        return self.home / "migratify.db"

    @property
    def covers_dir(self) -> Path:
        return self.home / "covers"

    @property
    def reports_dir(self) -> Path:
        return self.home / "reports"

    def ensure_dirs(self) -> None:
        for directory in (self.home, self.covers_dir, self.reports_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv()

    home_override = os.environ.get("MIGRATIFY_HOME")
    home = Path(home_override).expanduser() if home_override else Path.home() / ".migratify"

    settings = Settings(
        home=home,
        spotify_client_id=os.environ.get("MIGRATIFY_SPOTIFY_CLIENT_ID") or None,
        spotify_redirect_uri=os.environ.get("MIGRATIFY_SPOTIFY_REDIRECT_URI")
        or DEFAULT_REDIRECT_URI,
        ytm_client_id=os.environ.get("MIGRATIFY_YTM_CLIENT_ID") or None,
        ytm_client_secret=os.environ.get("MIGRATIFY_YTM_CLIENT_SECRET") or None,
        thresholds=Thresholds(
            auto_accept=_env_float("MIGRATIFY_AUTO_ACCEPT", 88.0),
            review_floor=_env_float("MIGRATIFY_REVIEW_FLOOR", 70.0),
            ambiguity_margin=_env_float("MIGRATIFY_AMBIGUITY_MARGIN", 4.0),
        ),
    )
    settings.ensure_dirs()
    return settings


def force_utf8_output() -> None:
    """Make stdout able to carry the characters music metadata actually uses.

    Windows still defaults to a legacy code page, and printing a track name
    containing anything outside it raises UnicodeEncodeError mid-render. Track
    and artist names are full of such characters, so this is the normal case
    rather than an edge one.
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8", errors="replace")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=verbose)],
    )
    # These are chatty at DEBUG and drown out our own output.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
