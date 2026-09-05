# PyInstaller spec for a self-contained Migratify build.
#
#     pyinstaller packaging/migratify.spec       (or: python scripts/build_exe.py)
#
# Two decisions worth explaining, because both look wrong at a glance.
#
# **A folder, not a single file.** ``--onefile`` unpacks the whole bundle into
# a temporary directory on *every* invocation. Migratify is a CLI someone runs
# several times in a row -- plan, review, apply -- and the browser automation it
# carries makes the bundle large enough that paying that cost each time is
# worse than handing over a folder. The release workflow zips it, so it is
# still one download.
#
# **Browser automation is included.** It would be much smaller without it, and
# the tool would also be useless: signing in *is* the browser automation, and
# the documented fallbacks need either a registered Spotify app -- which free
# accounts cannot use -- or hand-pasted request headers. What is deliberately
# not included is a *browser*: Migratify drives the one already installed, so
# the ~150 MB Chromium download stays unnecessary.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# The spec runs with SPECPATH set; the project root is one level up.
ROOT = Path(SPECPATH).parent  # noqa: F821 - injected by PyInstaller

datas = []
binaries = []
hiddenimports = []

# Packages that carry data files or load code dynamically, so the analysis
# cannot see everything they need:
#   ytmusicapi     bundles JSON locale and parser data
#   playwright     ships its own driver, which is a separate executable
#   rookiepy       a compiled extension, imported only when it exists
#   browser_cookie3  the pure-Python fallback for the same job
for package in ("ytmusicapi", "playwright", "rookiepy", "browser_cookie3"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception:
        # An optional dependency that is not installed in this environment.
        # The CLI already explains what to install when one is missing.
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden


analysis = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entrypoint.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Test and build tooling that nothing at runtime imports.
    excludes=["pytest", "tkinter", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="migratify",
    console=True,
    debug=False,
    strip=False,
    upx=False,
)

collected = COLLECT(  # noqa: F821
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="migratify",
)
