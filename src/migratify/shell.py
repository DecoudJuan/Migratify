"""The interactive prompt: what you get when Migratify is opened, not typed.

A packaged binary is *double-clicked* as often as it is invoked from a shell,
and a CLI that prints its help and exits looks broken when it is: the console
window closes before anything can be read. So running ``migratify`` with no
arguments opens a prompt that stays, and every command works there exactly as
it does on a real command line -- the same Typer app, the same parsing, the
same safety properties. Nothing about `plan` being read-only or `apply` being
the only writer changes because the words arrived from a prompt.

The banner follows the same idea as the rest of the tool: say what is true.
It reports the version, which services are actually connected, and how many
runs are on disk, so the first screen already answers "am I set up?".
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import click
import typer
from rich.console import Console

from migratify.config import get_settings
from migratify.models import Provider

#: The pet: a record, spinning, with a couple of notes coming off it. Rich
#: markup, three lines, each rendering to the same visible width so the
#: information column beside it stays flush -- keep that true when editing.
ART = (
    " [bold cyan]▄▟███▙▄[/bold cyan] [yellow]♪[/yellow] ",
    " [bold cyan]███[/bold cyan][white]◉[/white][bold cyan]███[/bold cyan]  [yellow]♫[/yellow]",
    " [bold cyan]▀▜███▛▀[/bold cyan] [yellow]♪[/yellow] ",
)

#: The same record in plain ASCII, for consoles whose encoding cannot carry the
#: block characters. Falling back is better than printing mojibake, and much
#: better than the UnicodeEncodeError that would otherwise land on line one.
ART_ASCII = (
    "  [bold cyan],---.[/bold cyan]  ",
    " [bold cyan]( ([/bold cyan][white]o[/white][bold cyan]) )[/bold cyan] [yellow]~[/yellow]",
    "  [bold cyan]`---'[/bold cyan]  ",
)

#: Typer vendors its own copy of Click's exception classes, so a usage error
#: raised inside a Typer app is *not* an instance of ``click.ClickException``.
#: Catching only one of the two families lets a plain "no such command" reach
#: the generic handler and be reported as if it were a crash.
_EXCEPTION_MODULES = [click.exceptions]
try:
    from typer import _click as _typer_click

    _EXCEPTION_MODULES.append(_typer_click.exceptions)
except (ImportError, AttributeError):  # pragma: no cover - older Typer
    pass


def _exception_family(name: str) -> tuple[type[BaseException], ...]:
    """Every class called *name* across the Click copies in play.

    The two copies do not carry the same set -- Typer's vendored module has
    the exception classes but not the control-flow ones -- so a missing name
    is normal, not a problem to report.
    """
    found: list[type[BaseException]] = []
    for module in _EXCEPTION_MODULES:
        candidate = getattr(module, name, None)
        if (
            isinstance(candidate, type)
            and issubclass(candidate, BaseException)
            and candidate not in found
        ):
            found.append(candidate)
    return tuple(found)


CLEAN_EXITS = (*_exception_family("Exit"), SystemExit)
ABORTS = _exception_family("Abort")
COMMAND_ERRORS = _exception_family("ClickException")

EXIT_WORDS = {"exit", "quit", "q", ":q", "bye"}
CLEAR_WORDS = {"clear", "cls"}


def _version() -> str:
    """The version to show.

    ``migratify.__version__`` rather than the installed distribution metadata:
    an editable checkout keeps the metadata from whenever it was last
    installed, and a frozen build carries none at all. The banner should say
    what this source tree is.
    """
    from migratify import __version__

    return __version__


def _console_can_draw() -> bool:
    """Whether the box-drawing art will survive this console's encoding."""
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "▄▟███▙▄◉♪♫".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _connections() -> list[tuple[str, bool]]:
    """Each service the banner reports on, and whether it has credentials.

    Deliberately does *not* verify them: the banner must not spend a network
    round trip per service before the first prompt. ``auth status`` is the
    command that earns the word "connected", and this line points at it.

    A list rather than two names in a sentence, because a third service is the
    plan: adding one should be one entry here, and the banner should not be a
    place that has to be rewritten when it arrives.
    """
    from migratify.auth import spotify_web
    from migratify.auth import ytmusic as ytm_auth

    return [
        (Provider.SPOTIFY.label, spotify_web.is_connected()),
        (Provider.YTMUSIC.label, ytm_auth.is_connected()),
    ]


def _connection_line() -> str:
    services = _connections()
    shown = " [dim]·[/dim] ".join(
        f"[green]{name}[/green]" if connected else f"[red]{name}[/red]"
        for name, connected in services
    )

    if all(connected for _, connected in services):
        return f"{shown} [dim]· connected, either direction[/dim]"
    return f"{shown} [dim]· run [/dim]login[dim] to connect what is red[/dim]"


def _store_line() -> str:
    settings = get_settings()
    home = settings.home

    try:
        shown = f"~/{home.relative_to(Path.home())}" if home.is_relative_to(Path.home()) else home
    except (OSError, ValueError):  # pragma: no cover - unusual home layouts
        shown = home

    try:
        from migratify.store import Store

        with Store() as store:
            count = len(store.list_runs(limit=1000))
    except Exception:  # a broken store must not stop the prompt from opening
        return f"[dim]{shown}[/dim]"

    runs = "no runs yet" if count == 0 else f"{count} run{'s' if count != 1 else ''}"
    return f"[dim]{shown} · {runs}[/dim]"


def banner_lines() -> list[str]:
    """The opening screen, as Rich markup -- art on the left, facts beside it."""
    art = ART if _console_can_draw() else ART_ASCII
    info = [
        f"[bold]Migratify[/bold] v{_version()}",
        _connection_line(),
        _store_line(),
    ]
    return [f"{drawing}   {text}" for drawing, text in zip(art, info, strict=True)]


def print_banner(console: Console) -> None:
    console.print()
    for line in banner_lines():
        console.print(line)
    console.print(
        "\n[dim]Type a command without the [/dim]migratify[dim] prefix -- "
        "e.g. [/dim]plan <playlist-url>[dim]."
        "\n[/dim]plan[dim] reads and reports. [/dim]apply[dim] is the one that writes -- "
        "and only what it accepted, or what you resolved in [/dim]review[dim]."
        "\n[/dim]help[dim] lists everything. [/dim]exit[dim] closes this window.[/dim]\n"
    )


def parse(line: str) -> list[str] | None:
    """Split a prompt line into argv, or None when there is nothing to run.

    Typing the program's own name is a reflex worth forgiving, and so is a
    leading slash -- both mean the command that follows.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("/"):
        line = line[1:]

    try:
        argv = shlex.split(line)
    except ValueError as exc:  # an unbalanced quote
        raise ValueError(f"{exc}. Check the quotes.") from exc

    if argv and argv[0].lower() in {"migratify", "migratify.exe"}:
        argv = argv[1:]
    return argv or None


def dispatch(argv: list[str], console: Console) -> None:
    """Run one command, and keep the prompt alive whatever it does.

    ``standalone_mode=False`` stops Click from calling ``sys.exit`` on usage
    errors, which would take the shell down with it. Every exit path a command
    can take -- success, usage error, our own ``_fail``, an unexpected
    exception -- has to end back at the prompt.
    """
    from migratify.cli import app

    try:
        command = typer.main.get_command(app)
        command.main(args=argv, prog_name="migratify", standalone_mode=False)
    except CLEAN_EXITS:
        pass  # a command finished, or printed its own --help
    except (KeyboardInterrupt, *ABORTS):
        console.print("[dim]Cancelled.[/dim]")
    except COMMAND_ERRORS as exc:
        console.print(f"[bold red]![/bold red] {exc.format_message()}")
        console.print("[dim]Try: help[/dim]")
    except Exception as exc:  # the prompt outlives any one command
        console.print(f"[bold red]![/bold red] {type(exc).__name__}: {exc}")


def run(console: Console) -> None:
    """The prompt loop. Returns when the user asks to leave."""
    print_banner(console)

    while True:
        try:
            line = console.input("[bold cyan]migratify[/bold cyan] [dim]>[/dim] ")
        except KeyboardInterrupt:
            console.print("[dim]Ctrl-C. Type exit to close.[/dim]")
            continue
        except EOFError:
            break

        try:
            argv = parse(line)
        except ValueError as exc:
            console.print(f"[bold red]![/bold red] {exc}")
            continue
        if argv is None:
            continue

        first = argv[0].lower()
        if first in EXIT_WORDS:
            break
        if first in CLEAR_WORDS:
            console.clear()
            print_banner(console)
            continue
        if first == "shell":
            console.print("[dim]Already in the interactive prompt.[/dim]")
            continue

        console.print()
        dispatch(argv, console)
        console.print()

    console.print("[dim]Bye.[/dim]")
