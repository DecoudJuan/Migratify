"""Build the standalone Migratify binary, and prove it starts.

    pip install -e ".[login,package]"
    python scripts/build_exe.py

Building is the easy half. The half that actually catches problems is the
check afterwards: a PyInstaller bundle that is missing a data file or a
dynamically imported module builds perfectly and then fails on first run. So
this runs the binary and asserts it can list its own commands before calling
the build a success.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "migratify.spec"
DIST = ROOT / "dist" / "migratify"


def binary() -> Path:
    return DIST / ("migratify.exe" if sys.platform == "win32" else "migratify")


def build() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit('PyInstaller is not installed. Run: pip install -e ".[package]"')

    # A stale build directory is the usual cause of a bundle that no longer
    # matches the source, so start from nothing.
    for path in (ROOT / "build", ROOT / "dist"):
        shutil.rmtree(path, ignore_errors=True)

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC)],
        cwd=ROOT,
        check=True,
    )


def verify() -> None:
    """Run the binary, and require it to answer.

    ``help`` is the right check: it imports the CLI, Typer and Rich, and
    renders a table -- enough of the bundle to catch a missing module, while
    touching no credentials and no network.
    """
    executable = binary()
    if not executable.is_file():
        sys.exit(f"Build finished but produced no binary at {executable}")

    result = subprocess.run(
        [str(executable), "help"],
        capture_output=True,
        text=True,
        # The help screen is a Rich table of box-drawing characters. Decoding
        # it with the console's legacy codepage raises, which reads as a
        # broken build when the build is fine.
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if result.returncode != 0 or "migratify" not in result.stdout:
        sys.exit(
            f"The binary was built but does not run (exit {result.returncode}).\n"
            f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )

    # Opened rather than invoked, the binary starts an interactive prompt. With
    # no terminal to type at -- CI, a pipe, this check -- it has to print its
    # help and leave instead of waiting forever for a line that never comes.
    try:
        subprocess.run(
            [str(executable)],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        sys.exit("The binary hangs when run with no arguments and no terminal.")


def main() -> None:
    build()
    verify()

    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"\nBuilt and verified: {binary()}")
    print(f"{total / 1_000_000:.0f} MB in {DIST}")
    print("Ship the whole folder -- the executable alone will not run.")


if __name__ == "__main__":
    main()
