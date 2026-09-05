"""Session capture without any app registration.

Connecting an account should mean *logging in*, the same way you log in
anywhere else. No developer dashboard, no client ID, no API key, no pasting
request headers out of devtools.

Three strategies, because no single one works everywhere:

1. **Import from a browser you already use.** Instant and click-free, and the
   best outcome when it is available. On macOS and Linux it usually is. On
   Windows it is not: App-Bound Encryption (Chromium v127+) makes every
   Chromium profile unreadable from outside the browser that owns it, and
   that includes Brave, Comet, Vivaldi and Arc, not just Chrome and Edge.

2. **A driven login window.** Playwright opens a browser you already have,
   against a persistent profile in ``~/.migratify``. Fine for Spotify.

3. **A manual sign-in in an ordinary window** (:func:`manual_profile_login`).
   Google refuses to authenticate inside an automation-controlled browser, so
   strategy 2 cannot work for YouTube Music at all. Here the browser is
   launched as a plain process -- no automation, nothing to detect -- pointed
   at a profile directory we own. The user signs in normally; we reattach
   afterwards to read the session, which is also how we get around App-Bound
   Encryption, since the browser does its own decrypting.

Whichever route is taken, the session lands in a persistent profile, so every
later refresh runs headless and invisible.
"""

from __future__ import annotations

import subprocess
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


def _pick_drivable(prefer: str | None = None) -> Browser | None:
    """The browser we would drive, honouring an explicit preference."""
    candidates = drivable_browsers()
    if prefer:
        candidates = [b for b in candidates if b.key == prefer] or candidates
    return candidates[0] if candidates else None


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


def _is_closed_target(exc: Exception) -> bool:
    text = str(exc).lower()
    return "has been closed" in text or "target closed" in text


def _window_closed_message(service_label: str, domain: str) -> str:
    """Explain a closed login window, including the case we cannot fix.

    Google refuses to sign you in inside an automation-controlled browser --
    "this browser or app may not be secure". That is a deliberate protection on
    their side, not a bug to defeat, so for YouTube Music the honest answer is
    to point at the two routes that do work rather than to keep retrying.
    """
    lines = [f"The {service_label} window closed before the sign-in completed."]

    if domain == YTM_DOMAIN:
        lines += [
            "",
            "If Google said the browser 'may not be secure', that is expected:",
            "it refuses sign-ins inside an automated browser. Two ways around it,",
            "both of which avoid automation entirely:",
            "",
        ]
        importable = [b.label for b in readable_browsers()]
        if importable:
            lines += [
                f"  1. Sign in to music.youtube.com in {' or '.join(importable)}",
                "     as you normally would, then run: migratify login ytmusic",
                "     (Migratify can read the session from those browsers.)",
            ]
        else:
            lines.append("  1. Sign in in Firefox, then run: migratify login ytmusic")
        lines += [
            "",
            "  2. Paste the request headers instead:",
            "     migratify auth ytmusic --paste",
        ]
    else:
        lines.append("Run the command again to try once more.")

    return "\n".join(lines)


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
                    raise AuthError(_window_closed_message(service_label, domain))

                try:
                    page.wait_for_timeout(1000)
                except Exception as exc:
                    # The window can close during the wait itself, so the check
                    # above is necessary but not sufficient. Same situation,
                    # same explanation -- not an internal error.
                    if _is_closed_target(exc):
                        raise AuthError(
                            _window_closed_message(service_label, domain)
                        ) from exc
                    raise
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


def manual_profile_login(
    url: str,
    domain: str,
    session_cookie: str,
    service_label: str,
    *,
    prefer_browser: str | None = None,
) -> CookieJar:
    """Sign in through an ordinary browser window, then reuse that profile.

    Google refuses sign-ins inside an automation-controlled browser, which
    kills the driven login window for YouTube Music. Rather than try to look
    like something we are not, this launches the browser the way a person
    would -- a plain process, no automation flags, no debugging port -- and
    simply points it at a profile directory we own.

    The user signs in normally, in a normal browser. We only reconnect to that
    profile afterwards, which is the same thing importing cookies from an
    installed browser does. On Windows it is also the *only* way in, since
    App-Bound Encryption makes every Chromium profile unreadable from outside
    the browser that owns it -- reattaching lets the browser do the decrypting.
    """
    browser = _pick_drivable(prefer_browser)
    executable = browser.executable() if browser else None
    if executable is None:
        raise AuthError(
            f"No browser available to sign in to {service_label}.\n"
            "Install Chrome, Edge, Brave or another Chromium browser, or use:\n"
            "  migratify auth ytmusic --paste"
        )

    profile = profile_dir()
    log.info("Opening %s so you can sign in to %s.", browser.label, service_label)
    log.info("Sign in as usual, then CLOSE the browser window to continue.")

    process = subprocess.Popen(
        [
            str(executable),
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            url,
        ]
    )
    try:
        process.wait(timeout=_LOGIN_TIMEOUT_MS / 1000)
    except subprocess.TimeoutExpired:
        process.terminate()
        raise AuthError(
            f"Timed out waiting for the {service_label} sign-in.\n"
            "Run the command again, sign in, then close the browser window."
        ) from None

    # The profile is free now, so we can read it the way the browser itself
    # would -- by opening it.
    log.info("Reading the session from that profile...")
    cookies = headless_visit(url, settle_ms=4000, domain=domain)
    bare = domain.lstrip(".")

    jar = CookieJar(cookies, source=f"{browser.label} profile")
    if not jar.has(session_cookie):
        raise AuthError(
            f"No {service_label} session was found in that profile.\n"
            f"Make sure you completed the sign-in at {bare} before closing the window."
        )

    log.info("%s connected.", service_label)
    return jar


def headless_visit(
    url: str,
    on_response: Callable | None = None,
    settle_ms: int = 6000,
    domain: str | None = None,
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
            if domain is not None:
                return _session_cookies(context, domain)
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
    """Get a live session for one service, the least intrusive way available.

    Google domains skip the driven login window entirely. It cannot succeed
    there -- Google rejects sign-ins from automation-controlled browsers -- so
    opening one would only waste the user's time before failing.
    """
    if prefer_installed:
        jar = read_installed_browsers(domain, session_cookie)
        if jar is not None:
            return jar

    if domain == YTM_DOMAIN:
        return manual_profile_login(
            login_url, domain, session_cookie, service_label,
            prefer_browser=prefer_browser,
        )

    return login_window(
        login_url,
        domain,
        session_cookie,
        service_label,
        prefer_browser=prefer_browser,
    )
