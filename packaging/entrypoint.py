"""Entry point for the packaged build.

PyInstaller needs a script to start from, and ``migratify.cli:app`` is a Typer
object rather than a module that runs on import. This is that script and
nothing more -- it must stay a thin shim, so that the packaged binary and
``migratify`` on a PATH are running the same code.

``multiprocessing.freeze_support`` is called first because the provider layer
uses a thread pool today and could use a process pool tomorrow; without it, a
frozen Windows build re-runs the whole CLI in every worker it spawns.
"""

from __future__ import annotations

import multiprocessing


def main() -> None:
    multiprocessing.freeze_support()

    from migratify.cli import app

    app()


if __name__ == "__main__":
    main()
