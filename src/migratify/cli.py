"""The Migratify command line.

Shaped around one safety property: **`plan` never writes to a music service.**
Matching, reporting and reviewing all happen before anything is created, and
`apply` is the only command that touches the destination. That separation is
what makes it reasonable to run this against a real account.

Commands:

    login       sign in to a service -- nothing to register
    auth        fallback auth flows, and status
    playlists   list what you have
    plan        match a playlist, write a report, change nothing
    review      resolve the ambiguous matches yourself
    apply       create the playlist and write the accepted tracks
    migrate     the three above, guided
    report      re-render a finished run
    runs        what you have done before
"""

from __future__ import annotations

import uuid

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt
from rich.table import Table

from migratify import artwork
from migratify import report as reporting
from migratify.auth import browsers, spotify_web
from migratify.auth import ytmusic as ytm_auth
from migratify.config import force_utf8_output, get_settings, setup_logging
from migratify.matching.search import Matcher, accepted_ids, pending_review
from migratify.models import Decision, MatchResult, Provider, Run, RunStatus
from migratify.providers.base import ProviderError
from migratify.providers.registry import get_provider, parse_ref, resolve_direction
from migratify.store import Store

force_utf8_output()
console = Console()

app = typer.Typer(
    name="migratify",
    help="Move playlists between Spotify and YouTube Music, without the wrong songs.",
    no_args_is_help=True,
    add_completion=False,
)
auth_app = typer.Typer(help="Fallback auth flows and connection status.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")


# --- helpers ----------------------------------------------------------------


def _fail(message: str) -> None:
    console.print(f"[bold red]![/bold red] {message}")
    raise typer.Exit(1)


def _provider_option(value: str | None) -> Provider | None:
    if value is None:
        return None
    try:
        return Provider(value.lower())
    except ValueError:
        _fail(f"Unknown service {value!r}. Use 'spotify' or 'ytmusic'.")
        return None


def _resolve_run(store: Store, run_id: str | None) -> Run:
    """The named run, or the most recent one.

    Defaulting to the latest run is what lets `plan` / `review` / `apply` be
    typed as three bare commands in a row.
    """
    run = store.get_run(run_id) if run_id else store.latest_run()
    if run is None:
        _fail("No run found. Start with: migratify plan <playlist-url>")
    return run  # type: ignore[return-value]


def _decision_style(decision: Decision) -> str:
    return {
        Decision.AUTO: "green",
        Decision.REVIEW: "yellow",
        Decision.MISS: "red",
        Decision.SKIPPED: "dim",
    }[decision]


def _summary_table(results: list[MatchResult]) -> Table:
    counts = dict.fromkeys(Decision, 0)
    for result in results:
        counts[result.decision] += 1

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_row("[green]matched automatically[/green]", str(counts[Decision.AUTO]))
    table.add_row("[yellow]needs your review[/yellow]", str(counts[Decision.REVIEW]))
    table.add_row("[red]not found[/red]", str(counts[Decision.MISS]))
    if counts[Decision.SKIPPED]:
        table.add_row("[dim]skipped[/dim]", str(counts[Decision.SKIPPED]))
    table.add_row("[bold]total[/bold]", f"[bold]{len(results)}[/bold]")
    return table


# --- login ------------------------------------------------------------------


@app.command()
def login(
    service: str | None = typer.Argument(
        None, help="spotify or ytmusic. Omit to connect both."
    ),
    browser: str | None = typer.Option(
        None, "--browser", help="Which browser to open, e.g. chrome, brave, comet."
    ),
    fresh: bool = typer.Option(
        False, "--fresh", help="Skip importing an existing session and sign in again."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Sign in. No app registration, no API keys, nothing to paste."""
    setup_logging(verbose)

    for note in browsers.platform_notes():
        console.print(f"[dim]{note}[/dim]\n")

    targets = [service.lower()] if service else ["spotify", "ytmusic"]
    for name in targets:
        try:
            if name == "spotify":
                spotify_web.connect(prefer_installed=not fresh, prefer_browser=browser)
            elif name == "ytmusic":
                ytm_auth.connect(prefer_installed=not fresh, prefer_browser=browser)
            else:
                _fail(f"Unknown service {name!r}. Use 'spotify' or 'ytmusic'.")
        except ProviderError as exc:
            _fail(str(exc))

    console.print("\n[green]Done.[/green] Try: [bold]migratify playlists spotify[/bold]")


# --- auth (fallbacks and status) --------------------------------------------


def _stored_method(provider: Provider) -> str | None:
    """How credentials for a service are stored, or None if there are none.

    Says nothing about whether they *work* -- see `_verify`.
    """
    if provider is Provider.SPOTIFY:
        return "signed-in session" if spotify_web.is_connected() else None

    return ytm_auth.describe() if ytm_auth.is_connected() else None


def _verify(provider: Provider) -> str | None:
    """Make one real call. Returns an error message, or None on success.

    Stored credentials are not proof of a working connection: a Spotify token
    issued to an app whose owner has no Premium subscription authenticates
    perfectly and then 403s on every request. Reporting that as "connected"
    sends the user hunting for a bug in the wrong place, so status earns the
    word by actually using the credentials.
    """
    try:
        get_provider(provider).list_playlists(limit=1)
    except ProviderError as exc:
        return str(exc).splitlines()[0]
    except Exception as exc:  # an unexpected failure is still a failure to report
        return f"{type(exc).__name__}: {exc}"
    return None


@auth_app.command("status")
def auth_status(
    verify: bool = typer.Option(
        True, "--verify/--no-verify", help="Make one real API call per service."
    ),
) -> None:
    """What is connected, and whether it actually works."""
    settings = get_settings()

    table = Table(title="Connections")
    table.add_column("Service")
    table.add_column("Status")
    table.add_column("Method")

    problems: list[str] = []

    for provider in (Provider.SPOTIFY, Provider.YTMUSIC):
        method = _stored_method(provider)

        if method is None:
            table.add_row(
                provider.label,
                "[red]not connected[/red]",
                f"run: migratify login {provider.value}",
            )
            continue

        if not verify:
            table.add_row(provider.label, "[dim]credentials stored[/dim]", method)
            continue

        error = _verify(provider)
        if error is None:
            table.add_row(provider.label, "[green]working[/green]", method)
        else:
            table.add_row(provider.label, "[red]not working[/red]", method)
            problems.append(f"[bold]{provider.label}[/bold]: {error}")

    console.print(table)

    for problem in problems:
        console.print(f"\n[red]![/red] {problem}")

    detected = browsers.installed_browsers()
    if detected:
        console.print(
            f"\n[dim]Browsers found: {', '.join(b.label for b in detected)}[/dim]"
        )
    for note in browsers.platform_notes():
        console.print(f"[dim]{note}[/dim]")

    with Store() as store:
        console.print(f"[dim]Cached matches: {store.cache_size()} · {settings.db_file}[/dim]")


@auth_app.command("ytmusic")
def auth_ytmusic(
    paste: bool = typer.Option(False, "--paste", help="Paste request headers from devtools."),
    oauth: bool = typer.Option(False, "--oauth", help="Google Cloud OAuth device flow."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Fallback YouTube Music auth. Prefer: migratify login ytmusic"""
    setup_logging(verbose)
    try:
        if oauth:
            ytm_auth.connect_oauth()
        elif paste:
            console.print(
                Panel(
                    "Open [bold]music.youtube.com[/bold] while signed in.\n"
                    "Devtools > Network > click any request to [bold]/youtubei/v1/[/bold] >\n"
                    "copy the full request headers, paste them below, then press\n"
                    "[bold]Ctrl-Z then Enter[/bold] (Windows) or [bold]Ctrl-D[/bold] (macOS/Linux).",
                    title="Paste request headers",
                )
            )
            import sys

            ytm_auth.save_pasted_headers(sys.stdin.read())
        else:
            ytm_auth.connect()
    except ProviderError as exc:
        _fail(str(exc))
    console.print("[green]YouTube Music connected.[/green]")


# --- browsing ---------------------------------------------------------------


@app.command()
def playlists(
    service: str = typer.Argument(..., help="spotify or ytmusic"),
    limit: int = typer.Option(50, "--limit"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List your playlists on a service."""
    setup_logging(verbose)
    provider_name = _provider_option(service)
    assert provider_name is not None

    try:
        provider = get_provider(provider_name)
        found = provider.list_playlists(limit=limit)
    except ProviderError as exc:
        _fail(str(exc))
        return

    table = Table(title=f"{provider_name.label} playlists")
    table.add_column("Name")
    table.add_column("Tracks", justify="right")
    table.add_column("ID", style="dim")

    for playlist in found:
        table.add_row(playlist.name, str(playlist.track_count or "?"), playlist.id)
    console.print(table)


# --- plan -------------------------------------------------------------------


@app.command()
def plan(
    playlist: str = typer.Argument(..., help="Playlist URL or ID."),
    to: str | None = typer.Option(None, "--to", help="Destination service."),
    source: str | None = typer.Option(None, "--from", help="Source service."),
    fmt: str = typer.Option("md", "--format", help="md, csv or json."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Re-search everything."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Match a playlist against the destination and write a report.

    Read-only. Nothing is created on either service.
    """
    setup_logging(verbose)

    try:
        source_provider, target_provider = resolve_direction(
            playlist, _provider_option(source), _provider_option(to)
        )
        playlist_id = parse_ref(playlist, source_provider)

        src = get_provider(source_provider)
        dst = get_provider(target_provider)

        source_playlist = src.get_playlist(playlist_id)
        tracks = src.get_tracks(playlist_id)
    except ProviderError as exc:
        _fail(str(exc))
        return

    if not tracks:
        _fail(f"{source_playlist.name!r} has no tracks to migrate.")

    console.print(
        Panel(
            f"[bold]{source_playlist.name}[/bold]\n"
            f"{len(tracks)} tracks · {source_provider.label} → {target_provider.label}",
            title="Planning",
        )
    )

    run = Run(
        id=uuid.uuid4().hex[:12],
        source_provider=source_provider,
        target_provider=target_provider,
        source_playlist_id=playlist_id,
        source_playlist_name=source_playlist.name,
        total=len(tracks),
    )

    settings = get_settings()
    with Store() as store:
        store.create_run(run)
        matcher = Matcher(
            dst,
            settings.thresholds,
            cache=None if no_cache else store.cache_lookup(target_provider),
        )

        results: list[MatchResult] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        ) as progress:
            task = progress.add_task("Matching", total=len(tracks))
            for position, track in enumerate(tracks, start=1):
                result = matcher.match(track)
                # Persisted as we go, so an interrupted plan is resumable.
                store.save_result(run.id, position, result)
                results.append(result)
                progress.update(
                    task,
                    advance=1,
                    description=f"[{_decision_style(result.decision)}]{track.title[:40]}",
                )

        store.refresh_counters(run.id)
        run = store.get_run(run.id) or run
        path = reporting.write(run, results, fmt)

    console.print()
    console.print(_summary_table(results))
    console.print(f"\nReport: [bold]{path}[/bold]")
    console.print(f"Run ID: [bold]{run.id}[/bold]")

    if pending_review(results):
        console.print("\nNext: [bold]migratify review[/bold]")
    else:
        console.print("\nNext: [bold]migratify apply[/bold]")


# --- review -----------------------------------------------------------------


@app.command()
def review(
    run_id: str | None = typer.Argument(None, help="Defaults to the most recent run."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Resolve the matches the scorer could not decide on its own."""
    setup_logging(verbose)

    with Store() as store:
        run = _resolve_run(store, run_id)
        results = store.load_results(run.id)
        queue = [
            (position, result)
            for position, result in enumerate(results, start=1)
            if result.decision is Decision.REVIEW and not result.chosen_id
        ]

        if not queue:
            console.print("[green]Nothing to review.[/green] Next: migratify apply")
            return

        console.print(
            Panel(
                f"{len(queue)} tracks need a decision.\n"
                "Pick a number, [bold]s[/bold] to skip this track, or [bold]q[/bold] to stop.",
                title=f"Review · {run.source_playlist_name}",
            )
        )

        for index, (position, result) in enumerate(queue, start=1):
            console.print(
                f"\n[bold]{index}/{len(queue)}[/bold]  "
                f"[cyan]{result.source.title}[/cyan] — {', '.join(result.source.artists)}"
            )
            source_len = result.source.duration_ms
            console.print(
                f"[dim]{result.source.album or 'no album'} · "
                f"{(source_len or 0) // 60000}:{((source_len or 0) // 1000) % 60:02d}[/dim]"
            )

            table = Table(show_header=True)
            table.add_column("#", justify="right")
            table.add_column("Candidate")
            table.add_column("Length")
            table.add_column("Score", justify="right")
            table.add_column("Why", style="dim")

            for number, candidate in enumerate(result.candidates, start=1):
                length = candidate.track.duration_ms or 0
                table.add_row(
                    str(number),
                    f"{candidate.track.title} — {', '.join(candidate.track.artists)}",
                    f"{length // 60000}:{(length // 1000) % 60:02d}" if length else "?",
                    f"{candidate.score:.0f}",
                    ", ".join(candidate.breakdown.reasons()) or "-",
                )
            console.print(table)

            choices = [str(n) for n in range(1, len(result.candidates) + 1)] + ["s", "q"]
            answer = Prompt.ask("Choice", choices=choices, default="s")

            if answer == "q":
                console.print("[dim]Stopping. Progress is saved.[/dim]")
                break
            if answer == "s":
                result.decision = Decision.SKIPPED
                result.resolved_by = "manual"
            else:
                chosen = result.candidates[int(answer) - 1]
                result.chosen_id = chosen.track.id
                result.resolved_by = "manual"

            store.save_result(run.id, position, result)

        store.refresh_counters(run.id)

    console.print("\nNext: [bold]migratify apply[/bold]")


# --- apply ------------------------------------------------------------------


@app.command()
def apply(
    run_id: str | None = typer.Argument(None, help="Defaults to the most recent run."),
    name: str | None = typer.Option(None, "--name", help="Override the playlist name."),
    public: bool = typer.Option(False, "--public", help="Create it public."),
    no_attribution: bool = typer.Option(
        False, "--no-attribution", help="Do not add a provenance line to the description."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Create the destination playlist and add the accepted tracks.

    The only command that writes to a music service.
    """
    setup_logging(verbose)

    with Store() as store:
        run = _resolve_run(store, run_id)
        results = store.load_results(run.id)
        to_write = store.unwritten(run.id)

        if not to_write:
            if accepted_ids(results):
                console.print("[green]Everything in this run is already applied.[/green]")
            else:
                _fail("Nothing accepted to write. Run: migratify review")
            return

        still_pending = len(pending_review(results))
        console.print(
            Panel(
                f"[bold]{run.source_playlist_name}[/bold]\n"
                f"{run.direction}\n\n"
                f"{len(to_write)} tracks will be added"
                + (
                    f"\n[yellow]{still_pending} unresolved matches will be left out[/yellow]"
                    if still_pending
                    else ""
                ),
                title="About to write",
            )
        )

        if not yes and not typer.confirm("Continue?", default=True):
            console.print("[dim]Nothing was written.[/dim]")
            return

        try:
            src = get_provider(run.source_provider)
            dst = get_provider(run.target_provider)
            source_playlist = src.get_playlist(run.source_playlist_id)

            description = source_playlist.description or ""
            if not no_attribution:
                provenance = artwork.describe_description(
                    source_playlist.name, run.source_provider.label
                )
                description = f"{description}\n\n{provenance}".strip()

            # Reuse the playlist from an earlier partial apply rather than
            # creating a second one.
            playlist_id = run.target_playlist_id
            if not playlist_id:
                playlist_id = dst.create_playlist(
                    name or source_playlist.name, description, public
                )
                run.target_playlist_id = playlist_id
                store.update_run(run)
                console.print(f"[green]Created[/green] {dst.playlist_url(playlist_id)}")

            ids = [r.chosen_id for r in to_write if r.chosen_id]
            added = dst.add_tracks(playlist_id, ids)
            store.mark_written(run.id, ids)
            console.print(f"[green]Added[/green] {added} tracks")

            uploaded, local_cover = artwork.transfer(
                source_playlist.cover_url, run.id, dst, playlist_id
            )
            if uploaded:
                console.print("[green]Cover uploaded[/green]")
            elif local_cover:
                console.print(
                    f"[yellow]Cover not uploaded[/yellow] — {run.target_provider.label} has no "
                    f"API for playlist artwork.\nThe original is at [bold]{local_cover}[/bold] "
                    "if you want to set it by hand."
                )

            run.status = RunStatus.APPLIED
            store.update_run(run)

        except ProviderError as exc:
            run.status = RunStatus.FAILED
            store.update_run(run)
            _fail(str(exc))
            return

    console.print(f"\n[bold green]Done.[/bold green] {dst.playlist_url(playlist_id)}")


# --- migrate ----------------------------------------------------------------


@app.command()
def migrate(
    playlist: str = typer.Argument(..., help="Playlist URL or ID."),
    to: str | None = typer.Option(None, "--to"),
    source: str | None = typer.Option(None, "--from"),
    public: bool = typer.Option(False, "--public"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Plan, review and apply in one guided pass."""
    # Typer leaves the decorated functions callable, so the guided flow is
    # literally the three commands in sequence -- no duplicated logic.
    plan(playlist=playlist, to=to, source=source, fmt="md", no_cache=False, verbose=verbose)

    with Store() as store:
        run = _resolve_run(store, None)
        needs = pending_review(store.load_results(run.id))

    if needs:
        review(run_id=run.id, verbose=verbose)

    apply(
        run_id=run.id,
        name=None,
        public=public,
        no_attribution=False,
        yes=False,
        verbose=verbose,
    )


# --- reporting --------------------------------------------------------------


@app.command("report")
def report_cmd(
    run_id: str | None = typer.Argument(None, help="Defaults to the most recent run."),
    fmt: str = typer.Option("md", "--format", help="md, csv or json."),
) -> None:
    """Re-render the report for a run."""
    with Store() as store:
        run = _resolve_run(store, run_id)
        path = reporting.write(run, store.load_results(run.id), fmt)
    console.print(f"Report: [bold]{path}[/bold]")


@app.command()
def runs(limit: int = typer.Option(20, "--limit")) -> None:
    """Past migrations."""
    with Store() as store:
        history = store.list_runs(limit)

    if not history:
        console.print("No runs yet. Start with: migratify plan <playlist-url>")
        return

    table = Table(title="Runs")
    table.add_column("ID", style="dim")
    table.add_column("Playlist")
    table.add_column("Direction")
    table.add_column("Auto", justify="right", style="green")
    table.add_column("Review", justify="right", style="yellow")
    table.add_column("Miss", justify="right", style="red")
    table.add_column("Status")

    for run in history:
        table.add_row(
            run.id,
            run.source_playlist_name,
            run.direction,
            str(run.auto),
            str(run.review),
            str(run.miss),
            run.status.value,
        )
    console.print(table)


if __name__ == "__main__":  # pragma: no cover
    app()
