"""Session capture without any app registration.

Connecting an account should mean *logging in*, the same way you log in
anywhere else. No developer dashboard, no client ID, no API key, no pasting
request headers out of devtools.

Two strategies, tried in order:

1. **Import from a browser you already use.** Instant and click-free. We walk
   every browser in :mod:`migratify.auth.browsers` that is installed and
   readable on this platform, newest session wins.

2. **A login window in your own browser.** We drive whichever Chromium-family
   browser you already have -- Chrome, Edge, Brave, Comet, Vivaldi, Opera,
   Arc -- against a persistent profile under ``~/.migratify/browser-profile``.
   You sign in normally. The session lives in that profile and survives
   restarts, so every later refresh runs headless and invisible. Only if no
   such browser exists do we fall back to a Playwright-managed Chromium.

Strategy 2 is the one that always works, which is why it is the fallback
rather than the other way round.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from migratify.auth.browsers import Browser, drivable_browsers, readable_browsers
from migratify.config import get_logger, get_settings
from migratify.providers.base import AuthError

log = get_logger(__name__)

#: Cookie domains that identify a live session per service.
SPOTIFY_DOMAIN = ".spotify.com"
YTM_DOMAIN = ".youtube.com"

#: The cookie whose presence means "actually signed in". Everything else these
#: sites set is handed to anonymous visitors too.
SPOTIFY_SESSION_COOKIE = "sp_dc"
YTM_SESSION_COOKIE = "SAPISID"

SPOTIFY_LOGIN_URL = "https://open.spotify.com/"
YTM_LOGIN_URL = "https://music.youtube.com/"

_LOGIN_TIMEOUT_MS = 300_000


@dataclass
class CookieJar:
    """Cookies for one service, plus where they came from."""

    cookies: dict[str, str]
    source: str

    def header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def has(self, name: str) -> bool:
        return bool(self.cookies.get(name))


def profile_dir() -> Path:
    path = get_settings().home / "browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- strategy 1: read a browser the user already uses -----------------------


def read_installed_browsers(domain: str, session_cookie: str) -> CookieJar | None:
    """Lift cookies for a domain from any readable installed browser.

    Best-effort by nature -- browsers actively harden against this -- so an
    individual failure is logged at debug level and we simply move to the next
    browser rather than surfacing an error.
    """
    for browser in readable_browsers():
        jar = _read_one(browser, domain)
        if jar and jar.has(session_cookie):
            log.info("Found a signed-in session in %s.", browser.label)
            return jar
    return None


def _read_one(browser: Browser, domain: str) -> CookieJar | None:
    for backend_name, reader in _backends():
        try:
            cookies = reader(browser, domain)
        except Exception as exc:
            log.debug("%s could not read %s cookies for %s: %s", backend_name, browser.key, domain, exc)
            continue
        if cookies:
            return CookieJar(cookies, source=f"{browser.label} (via {backend_name})")
    return None


def _backends() -> list[tuple[str, Callable[[Browser, str], dict[str, str]]]]:
    """Cookie-reading backends, best first.

    rookiepy covers more browsers and handles macOS Keychain and Safari better
    than browser_cookie3, so it goes first. Both are optional; a missing one is
    skipped silently.
    """
    backends: list[tuple[str, Callable[[Browser, str], dict[str, str]]]] = []

    try:
        import rookiepy

        def _rookie(browser: Browser, domain: str) -> dict[str, str]:
            bare = domain.lstrip(".")
            fn = getattr(rookiepy, browser.cookie_backend or "", None)
            if fn is None:
                # No dedicated function -- point the generic Chromium reader at
                # this browser's own cookie store. This is what makes Comet,
                # Arc and any future Chromium fork work without new code.
                db = browser.cookie_db()
                if db is None:
                    raise LookupError(f"no cookie store found for {browser.key}")
                fn = lambda domains: rookiepy.any_browser(str(db), domains)  # noqa: E731
            return {c["name"]: c["value"] for c in fn([bare])}

        backends.append(("rookiepy", _rookie))
    except ImportError:
        pass

    try:
        import browser_cookie3

        def _bc3(browser: Browser, domain: str) -> dict[str, str]:
            bare = domain.lstrip(".")
            fn = getattr(browser_cookie3, browser.cookie_backend or "", None)
            if fn is None:
                raise LookupError(f"browser_cookie3 has no reader for {browser.key}")
            return {c.name: c.value for c in fn(domain_name=bare)}

        backends.append(("browser_cookie3", _bc3))
    except ImportError:
        pass

    return backends


# --- strategy 2: a login window ---------------------------------------------


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise AuthError(
            "Signing in needs a browser Migratify can drive.\n\n"
            "  pip install 'migratify[login]'\n"
            "  playwright install chromium   # only if you have no Chromium browser\n\n"
            "Or use the fallback flows, which need no browser automation:\n"
            "  migratify auth spotify --pkce\n"
            "  migratify auth ytmusic --paste"
        ) from exc
    return sync_playwright


def _launch_kwargs(prefer: str | None = None) -> dict:
    """Pick which browser binary to drive.

    Using a browser the user already has installed avoids a 150 MB Chromium
    download, and lands them in a familiar-looking window. Playwright's own
    build is the last resort.
    """
    candidates = drivable_browsers()
    if prefer:
        candidates = [b for b in candidates if b.key == prefer] or candidates

    for browser in candidates:
        if browser.channel:
            log.debug("Driving %s via the %s channel.", browser.label, browser.channel)
            return {"channel": browser.channel}
        executable = browser.executable()
        if executable:
            log.debug("Driving %s at %s.", browser.label, executable)
            return {"executable_path": str(executable)}

    log.debug("No installed Chromium browser found; using the bundled Chromium.")
    return {}


def _session_cookies(context, domain: str) -> dict[str, str]:
    """Cookies for a domain, as the browser itself sees them.

    Asking the *context* rather than the page is the whole trick. Both session
    cookies that matter here -- Spotify's ``sp_dc`` and Google's ``SAPISID`` --
    are HttpOnly, so they are invisible to ``document.cookie`` and any
    in-page check for them can never succeed. Playwright's cookie jar sees
    them.
    """
    bare = domain.lstrip(".")
    return {
        c["name"]: c["value"]
        for c in context.cookies()
        if c["domain"].endswith(bare)
    }


def login_window(
    url: str,
    domain: str,
    session_cookie: str,
    service_label: str,
    *,
    prefer_browser: str | None = None,
    timeout_ms: int = _LOGIN_TIMEOUT_MS,
) -> CookieJar:
    """Open a browser window and wait for the user to sign in.

    Uses a persistent profile, so this is a one-time step per service: later
    runs reuse that profile headlessly and never show a window again.

    Completion is detected by polling the browser's own cookie jar, not by
    watching the page. Page-based detection was tried first and is a dead end:
    the session cookies are HttpOnly, and every service renders a different
    post-login page, so there is no reliable element to wait for either.
    """
    sync_playwright = _require_playwright()

    log.info("Opening a browser window -- sign in to %s as you normally would.", service_label)
    log.info("Waiting for you to finish. The window closes itself when it detects the session.")

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                str(profile_dir()),
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
                **_launch_kwargs(prefer_browser),
            )
        except Exception as exc:
            # Distinguish this from a failed sign-in: nothing was ever shown
            # to the user, so telling them to try signing in again is useless.
            raise AuthError(
                f"Could not open a browser window: {exc}\n\n"
                "If no Chromium browser is installed, run: playwright install chromium"
            ) from exc

        cookies: dict[str, str] = {}
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded")

            deadline = time.monotonic() + timeout_ms / 1000
            while time.monotonic() < deadline:
                cookies = _session_cookies(context, domain)
                if cookies.get(session_cookie):
                    break

                if not context.pages:
                    # The user closed the window. That is an answer, not a
                    # crash -- do not make them wait out the full timeout.
                    raise AuthError(
                        f"The {service_label} window was closed before sign-in completed."
                    )

                page.wait_for_timeout(1000)
            else:
                raise AuthError(
                    f"Timed out waiting for the {service_label} sign-in.\n"
                    "Run the command again, and complete the sign-in in the window that opens."
                )
        except AuthError:
            raise
        except Exception as exc:
            raise AuthError(
                f"The {service_label} sign-in did not complete: {exc}"
            ) from exc
        finally:
            context.close()

    log.info("%s connected.", service_label)
    return CookieJar(cookies, source="Migratify login window")


def headless_visit(
    url: str,
    on_response: Callable | None = None,
    settle_ms: int = 6000,
) -> dict[str, str]:
    """Load a URL headlessly in the saved profile and return its cookies.

    This is how a stored session renews itself: the real site runs its own
    token handshake using the profile we already have, and we observe the
    result. Far more durable than reimplementing that handshake, which is
    exactly the part these services keep changing.
    """
    sync_playwright = _require_playwright()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir()),
            headless=True,
            **_launch_kwargs(),
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            if on_response is not None:
                page.on("response", on_response)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)
            return {c["name"]: c["value"] for c in context.cookies()}
        finally:
            context.close()


def acquire(
    domain: str,
    session_cookie: str,
    login_url: str,
    service_label: str,
    *,
    prefer_installed: bool = True,
    prefer_browser: str | None = None,
) -> CookieJar:
    """Get a live session for one service, the least intrusive way available."""
    if prefer_installed:
        jar = read_installed_browsers(domain, session_cookie)
        if jar is not None:
            return jar

    return login_window(
        login_url,
        domain,
        session_cookie,
        service_label,
        prefer_browser=prefer_browser,
    )
