"""The interactive prompt.

Two properties are worth pinning. The prompt must survive anything a command
does to it -- a usage error, our own ``_fail``, an unexpected exception -- and
the banner must render on a console that cannot draw box characters, which is
the ordinary case on Windows before ``force_utf8_output`` has run.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys

import pytest
from rich.console import Console

from migratify import shell


def _console() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, width=100, no_color=True, legacy_windows=False), out


class TestParse:
    def test_blank_and_comments_are_nothing_to_run(self) -> None:
        assert shell.parse("") is None
        assert shell.parse("   ") is None
        assert shell.parse("# a note") is None

    def test_splits_like_a_shell(self) -> None:
        assert shell.parse("plan https://x/y --to ytmusic") == [
            "plan",
            "https://x/y",
            "--to",
            "ytmusic",
        ]
        assert shell.parse('apply --name "My List"') == ["apply", "--name", "My List"]

    def test_forgives_the_program_name_and_a_leading_slash(self) -> None:
        assert shell.parse("migratify runs") == ["runs"]
        assert shell.parse("/help") == ["help"]
        assert shell.parse("migratify") is None

    def test_unbalanced_quote_is_reported_not_raised_raw(self) -> None:
        with pytest.raises(ValueError, match="quotes"):
            shell.parse('apply --name "unfinished')


class TestBanner:
    def test_three_lines_of_art_with_information_beside_them(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("MIGRATIFY_HOME", str(tmp_path))
        lines = shell.banner_lines()
        assert len(lines) == 3
        assert "Migratify" in lines[0]
        assert "Spotify" in lines[1]

    def test_lists_every_service_it_knows_about(self, tmp_path, monkeypatch) -> None:
        # Naming two services in a sentence is what a third one would break.
        monkeypatch.setenv("MIGRATIFY_HOME", str(tmp_path))
        monkeypatch.setattr(
            shell, "_connections", lambda: [("Spotify", True), ("Tidal", False)]
        )
        line = shell._connection_line()
        assert "Spotify" in line
        assert "Tidal" in line
        assert "login" in line  # one is not connected, so the line says what to run

    def test_falls_back_to_ascii_when_the_console_cannot_encode_blocks(
        self, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setenv("MIGRATIFY_HOME", str(tmp_path))
        monkeypatch.setattr(shell.sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))

        console, out = _console()
        shell.print_banner(console)
        rendered = out.getvalue()

        assert "▟" not in rendered
        assert "(o)" in rendered


class TestThePromptSurvivesItsCommands:
    """Every exit path a command can take has to end back at the prompt."""

    def test_usage_error_is_reported(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("MIGRATIFY_HOME", str(tmp_path))
        console, out = _console()
        shell.dispatch(["plan"], console)  # a required argument is missing
        assert "!" in out.getvalue()

    def test_unknown_command_is_reported(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("MIGRATIFY_HOME", str(tmp_path))
        console, out = _console()
        shell.dispatch(["not-a-command"], console)
        assert "!" in out.getvalue()

    def test_an_unexpected_exception_does_not_escape(self, monkeypatch) -> None:
        console, out = _console()

        def explode(*args: object, **kwargs: object) -> None:
            raise RuntimeError("the network fell over")

        monkeypatch.setattr(shell.typer.main, "get_command", explode)
        shell.dispatch(["runs"], console)
        assert "the network fell over" in out.getvalue()

    def test_help_exits_the_command_not_the_shell(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setenv("MIGRATIFY_HOME", str(tmp_path))
        console, _ = _console()
        shell.dispatch(["--help"], console)
        # Commands write to their own console; the prompt only has to survive.
        assert "Usage" in capsys.readouterr().out


class TestWithoutClick:
    """Click is not a dependency of this project.

    Typer vendors its own copy, so a clean install has no ``click`` to import
    -- and a prompt that reaches for one crashes on the machines that matter
    most, the ones running the packaged binary. Checked in a subprocess
    because the import has already happened in this one.
    """

    def test_the_prompt_still_reports_a_usage_error(self, tmp_path) -> None:
        (tmp_path / "click.py").write_text('raise ImportError("no click here")')

        env = dict(os.environ, PYTHONPATH=str(tmp_path), PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import io\n"
                "from rich.console import Console\n"
                "from migratify import shell\n"
                "out = io.StringIO()\n"
                "shell.dispatch(['not-a-command'], Console(file=out, no_color=True))\n"
                "print(out.getvalue())\n",
            ],
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        assert "No such command" in result.stdout


class TestLoop:
    def _drive(self, lines: list[str], tmp_path, monkeypatch) -> str:
        monkeypatch.setenv("MIGRATIFY_HOME", str(tmp_path))
        console, out = _console()
        typed = iter(lines)

        def read(_prompt: str = "") -> str:
            try:
                return next(typed)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr(console, "input", read)
        shell.run(console)
        return out.getvalue()

    def test_exit_leaves(self, tmp_path, monkeypatch) -> None:
        assert "Bye" in self._drive(["exit"], tmp_path, monkeypatch)

    def test_end_of_input_leaves(self, tmp_path, monkeypatch) -> None:
        # A closed stdin must not spin the loop forever.
        assert "Bye" in self._drive([], tmp_path, monkeypatch)

    def test_a_bad_command_does_not_end_the_session(self, tmp_path, monkeypatch, capsys) -> None:
        output = self._drive(["not-a-command", "runs", "exit"], tmp_path, monkeypatch)
        assert "No such command" in output
        assert "Bye" in output
        # And the command after the bad one still ran.
        assert "No runs yet" in capsys.readouterr().out
