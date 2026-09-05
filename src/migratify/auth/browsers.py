"""Catalog and detection of installed browsers, across macOS, Windows and Linux.

Two separate jobs are served from one catalog:

* **Reading an existing session.** Where each browser keeps its cookie store,
  so we can lift a session the user already has.
* **Driving a login window.** Where each browser's executable lives, so we can
  open *their* browser rather than downloading one.

The Chromium family is open-ended on purpose. Brave, Comet, Arc, Vivaldi, Opera
and friends are all Chrome underneath and all keep their profile in the same
shape, so supporting a new one is a row in a table, not new code.

Platform notes that drive the design:

* **macOS** has no App-Bound Encryption. Chromium cookies are protected by a
  Keychain entry that the cookie readers know how to ask for, so direct import
  usually just works. Safari is supported too, but reading it requires Full
  Disk Access for the terminal.
* **Windows** encrypts Chrome and Edge cookies with App-Bound Encryption from
  v127 onward and no third-party reader can open them. Firefox still works.
  This is why the login window exists.
* **Linux** varies by keyring but generally works.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

Family = str  # "chromium" | "firefox" | "webkit"


def _home() -> Path:
    return Path.home()


def _mac_app_support() -> Path:
    return _home() / "Library" / "Application Support"


def _win_local() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", _home() / "AppData" / "Local"))


def _win_roaming() -> Path:
    return Path(os.environ.get("APPDATA", _home() / "AppData" / "Roaming"))


def _win_program_files() -> list[Path]:
    return [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")),
        _win_local(),
    ]


@dataclass(frozen=True)
class Browser:
    """One browser we know how to read from and/or drive."""

    key: str
    label: str
    family: Family

    #: Profile roots per platform. For Chromium these are the "User Data"
    #: directories that contain ``Default/Cookies``.
    profiles: dict[str, list[Path]] = field(default_factory=dict)

    #: Executable candidates per platform, for launching a login window.
    executables: dict[str, list[Path]] = field(default_factory=dict)

    #: Playwright channel name, when the browser is one Playwright ships
    #: first-class support for. Cheaper and more reliable than executable_path.
    channel: str | None = None

    #: Name understood by the cookie-reader backends, when they have a
    #: dedicated function for this browser.
    cookie_backend: str | None = None

    def profile_root(self) -> Path | None:
        for candidate in self.profiles.get(_os_key(), []):
            if candidate.exists():
                return candidate
        return None

    def executable(self) -> Path | None:
        for candidate in self.executables.get(_os_key(), []):
            if candidate.exists():
                return candidate
        return None

    @property
    def installed(self) -> bool:
        return self.profile_root() is not None or self.executable() is not None

    def cookie_db(self) -> Path | None:
        """Path to the cookie store, for the generic Chromium reader."""
        root = self.profile_root()
        if root is None or self.family != "chromium":
            return None
        for relative in ("Default/Network/Cookies", "Default/Cookies"):
            candidate = root / relative
            if candidate.exists():
                return candidate
        # Some builds use non-default profile names; take the first that has one.
        for profile in sorted(root.glob("*/Network/Cookies")):
            return profile
        return None


def _os_key() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "win32"
    return "linux"


def _mac_exe(app: str, binary: str | None = None) -> Path:
    """Path to the executable inside a macOS .app bundle."""
    return Path("/Applications") / f"{app}.app" / "Contents" / "MacOS" / (binary or app)


def _win_exes(*relative: str) -> list[Path]:
    return [base / rel for base in _win_program_files() for rel in relative]


CATALOG: list[Browser] = [
    Browser(
        key="chrome",
        label="Google Chrome",
        family="chromium",
        channel="chrome",
        cookie_backend="chrome",
        profiles={
            "darwin": [_mac_app_support() / "Google" / "Chrome"],
            "win32": [_win_local() / "Google" / "Chrome" / "User Data"],
            "linux": [_home() / ".config" / "google-chrome"],
        },
        executables={
            "darwin": [_mac_exe("Google Chrome")],
            "win32": _win_exes(r"Google\Chrome\Application\chrome.exe"),
            "linux": [Path("/usr/bin/google-chrome"), Path("/usr/bin/google-chrome-stable")],
        },
    ),
    Browser(
        key="edge",
        label="Microsoft Edge",
        family="chromium",
        channel="msedge",
        cookie_backend="edge",
        profiles={
            "darwin": [_mac_app_support() / "Microsoft Edge"],
            "win32": [_win_local() / "Microsoft" / "Edge" / "User Data"],
            "linux": [_home() / ".config" / "microsoft-edge"],
        },
        executables={
            "darwin": [_mac_exe("Microsoft Edge")],
            "win32": _win_exes(r"Microsoft\Edge\Application\msedge.exe"),
            "linux": [Path("/usr/bin/microsoft-edge")],
        },
    ),
    Browser(
        key="brave",
        label="Brave",
        family="chromium",
        cookie_backend="brave",
        profiles={
            "darwin": [_mac_app_support() / "BraveSoftware" / "Brave-Browser"],
            "win32": [_win_local() / "BraveSoftware" / "Brave-Browser" / "User Data"],
            "linux": [_home() / ".config" / "BraveSoftware" / "Brave-Browser"],
        },
        executables={
            "darwin": [_mac_exe("Brave Browser")],
            "win32": _win_exes(r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            "linux": [Path("/usr/bin/brave-browser"), Path("/usr/bin/brave")],
        },
    ),
    Browser(
        key="comet",
        label="Comet",
        family="chromium",
        profiles={
            "darwin": [
                _mac_app_support() / "Perplexity" / "Comet",
                _mac_app_support() / "Comet",
            ],
            "win32": [
                _win_local() / "Perplexity" / "Comet" / "User Data",
                _win_local() / "Comet" / "User Data",
            ],
            "linux": [_home() / ".config" / "comet"],
        },
        executables={
            "darwin": [_mac_exe("Comet")],
            "win32": _win_exes(
                r"Perplexity\Comet\Application\comet.exe",
                r"Comet\Application\comet.exe",
            ),
            "linux": [Path("/usr/bin/comet")],
        },
    ),
    Browser(
        key="arc",
        label="Arc",
        family="chromium",
        profiles={
            "darwin": [_mac_app_support() / "Arc" / "User Data"],
            "win32": [_win_local() / "Packages" / "TheBrowserCompany.Arc" / "LocalCache"],
            "linux": [],
        },
        executables={
            "darwin": [_mac_exe("Arc")],
            "win32": [],
            "linux": [],
        },
    ),
    Browser(
        key="vivaldi",
        label="Vivaldi",
        family="chromium",
        cookie_backend="vivaldi",
        profiles={
            "darwin": [_mac_app_support() / "Vivaldi"],
            "win32": [_win_local() / "Vivaldi" / "User Data"],
            "linux": [_home() / ".config" / "vivaldi"],
        },
        executables={
            "darwin": [_mac_exe("Vivaldi")],
            "win32": _win_exes(r"Vivaldi\Application\vivaldi.exe"),
            "linux": [Path("/usr/bin/vivaldi")],
        },
    ),
    Browser(
        key="opera",
        label="Opera",
        family="chromium",
        cookie_backend="opera",
        profiles={
            "darwin": [_mac_app_support() / "com.operasoftware.Opera"],
            "win32": [_win_roaming() / "Opera Software" / "Opera Stable"],
            "linux": [_home() / ".config" / "opera"],
        },
        executables={
            "darwin": [_mac_exe("Opera")],
            "win32": _win_exes(r"Opera\opera.exe"),
            "linux": [Path("/usr/bin/opera")],
        },
    ),
    Browser(
        key="chromium",
        label="Chromium",
        family="chromium",
        channel="chromium",
        cookie_backend="chromium",
        profiles={
            "darwin": [_mac_app_support() / "Chromium"],
            "win32": [_win_local() / "Chromium" / "User Data"],
            "linux": [_home() / ".config" / "chromium"],
        },
        executables={
            "darwin": [_mac_exe("Chromium")],
            "win32": _win_exes(r"Chromium\Application\chrome.exe"),
            "linux": [Path("/usr/bin/chromium"), Path("/usr/bin/chromium-browser")],
        },
    ),
    Browser(
        key="firefox",
        label="Firefox",
        family="firefox",
        cookie_backend="firefox",
        profiles={
            "darwin": [_mac_app_support() / "Firefox" / "Profiles"],
            "win32": [_win_roaming() / "Mozilla" / "Firefox" / "Profiles"],
            "linux": [_home() / ".mozilla" / "firefox"],
        },
        executables={
            "darwin": [_mac_exe("Firefox")],
            "win32": _win_exes(r"Mozilla Firefox\firefox.exe"),
            "linux": [Path("/usr/bin/firefox")],
        },
    ),
    Browser(
        key="zen",
        label="Zen Browser",
        family="firefox",
        profiles={
            "darwin": [_mac_app_support() / "zen" / "Profiles"],
            "win32": [_win_roaming() / "zen" / "Profiles"],
            "linux": [_home() / ".zen"],
        },
        executables={
            "darwin": [_mac_exe("Zen", "zen")],
            "win32": _win_exes(r"Zen Browser\zen.exe"),
            "linux": [Path("/usr/bin/zen")],
        },
    ),
    Browser(
        key="librewolf",
        label="LibreWolf",
        family="firefox",
        cookie_backend="librewolf",
        profiles={
            "darwin": [_mac_app_support() / "LibreWolf" / "Profiles"],
            "win32": [_win_roaming() / "librewolf" / "Profiles"],
            "linux": [_home() / ".librewolf"],
        },
        executables={"darwin": [_mac_exe("LibreWolf", "librewolf")], "win32": [], "linux": []},
    ),
    Browser(
        key="safari",
        label="Safari",
        family="webkit",
        cookie_backend="safari",
        # Modern macOS keeps Safari cookies inside a sandbox container.
        profiles={
            "darwin": [
                _home() / "Library" / "Containers" / "com.apple.Safari" / "Data" / "Library"
                / "Cookies",
                _home() / "Library" / "Cookies",
            ]
        },
        executables={"darwin": [_mac_exe("Safari")]},
    ),
]

BY_KEY = {browser.key: browser for browser in CATALOG}


def installed_browsers() -> list[Browser]:
    """Every browser from the catalog that appears to be present."""
    return [browser for browser in CATALOG if browser.installed]


def readable_browsers() -> list[Browser]:
    """Browsers whose cookie store we can plausibly read on this platform.

    On Windows the **entire Chromium family** is excluded. App-Bound
    Encryption arrived in Chromium v127 and every downstream browser inherits
    it -- Brave, Comet, Vivaldi, Opera and Arc are as unreadable as Chrome and
    Edge, which was verified by trying. An earlier version of this function
    excluded only Chrome and Edge by name, and the result was advice telling
    users to sign in to Brave, where the import could never work.

    Firefox-family browsers remain readable everywhere.
    """
    result = []
    for browser in installed_browsers():
        if browser.profile_root() is None:
            continue
        if _os_key() == "win32" and browser.family == "chromium":
            continue
        result.append(browser)
    return result


def drivable_browsers() -> list[Browser]:
    """Browsers we can open a login window in.

    Chromium-family only: we drive them through the Chrome DevTools Protocol
    with a persistent profile, which Firefox and Safari do not offer.
    """
    return [
        browser
        for browser in installed_browsers()
        if browser.family == "chromium" and (browser.channel or browser.executable())
    ]


def platform_notes() -> list[str]:
    """Caveats worth telling the user about on this platform."""
    notes: list[str] = []
    key = _os_key()
    if key == "win32":
        notes.append(
            "On Windows, Chrome and Edge encrypt cookies with App-Bound Encryption "
            "(v127+), so their sessions cannot be imported. Migratify opens its own "
            "login window instead."
        )
    if key == "darwin":
        notes.append(
            "On macOS, importing from Safari requires Full Disk Access for your "
            "terminal: System Settings > Privacy & Security > Full Disk Access."
        )
    return notes


def describe_platform() -> str:
    return f"{platform.system()} {platform.release()}"
