"""Endoscope CLI — command-line interface for debug artifact capture.

Commands:
    serve        Start the collector API service
    list         List sessions
    show         Show session details
    pull         Download session files locally
    delete       Delete a session
    prune        Bulk-delete old sessions
    api-key      Generate a random API key
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Optional

import os
import httpx
import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .app import create_app
from .client import EndoscopeAPIClient, EndoscopeAPIError
from .config import EndoscopeConfig

app = typer.Typer(
    name="endoscope",
    help="Lightweight debug artifact capture CLI",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


# ------------------------------------------------------------------
# Shared options — attached to the callback so all subcommands
# can access them, but they're optional (serve / api-key don't need them).
# ------------------------------------------------------------------


class State:
    """Mutable state shared across commands via Typer context."""

    api_url: str = "http://localhost:8000"
    api_key: str = ""
    project: str = ""
    insecure: bool = False


@app.callback()
def main(
    ctx: typer.Context,
    api_url: str = typer.Option(
        "http://localhost:8000",
        "--api-url",
        envvar="ENDO_API_URL",
        help="Endoscope API base URL",
    ),
    api_key: str = typer.Option(
        "",
        "--api-key",
        envvar="ENDO_API_KEY",
        help="API key for authentication",
    ),
    project: str = typer.Option(
        "",
        "--project",
        envvar="ENDO_PROJECT",
        help="Project name (must match server config)",
    ),
    insecure: bool = typer.Option(
        False,
        "--insecure",
        envvar="ENDO_INSECURE",
        help="Disable SSL certificate verification (for self-signed certs)",
    ),
) -> None:
    """Lightweight debug artifact capture CLI."""
    state = State()
    state.api_url = api_url
    state.api_key = api_key
    state.project = project
    state.insecure = insecure
    ctx.ensure_object(dict)
    ctx.obj["state"] = state


def _require_client(state: State) -> EndoscopeAPIClient:
    """Build a client, validating that required options are present."""
    missing = []
    if not state.api_key:
        missing.append("--api-key")
    if not state.project:
        missing.append("--project")
    if missing:
        err_console.print(
            f"Required options not set: {', '.join(missing)}\n"
            "Set them via flags or environment variables (ENDO_API_KEY, ENDO_PROJECT)."
        )
        raise typer.Exit(code=1)
    return EndoscopeAPIClient(
        base_url=state.api_url,
        api_key=state.api_key,
        project=state.project,
        verify_ssl=not state.insecure,
    )


# ------------------------------------------------------------------
# serve
# ------------------------------------------------------------------


@app.command("serve")
def serve_cmd(
    project: str = typer.Option(
        None, "--project", "-p", envvar="ENDO_PROJECT", help="Project name"
    ),
    s3_endpoint: str = typer.Option(
        "https://s3.us-east-1.amazonaws.com",
        "--s3-endpoint",
        envvar="ENDO_S3_ENDPOINT",
        help="S3-compatible endpoint URL (e.g. http://localhost:9000)",
    ),
    s3_access_key: str = typer.Option(
        None, "--s3-access-key", envvar="ENDO_S3_ACCESS_KEY", help="S3 access key ID"
    ),
    s3_secret_key: str = typer.Option(
        None,
        "--s3-secret-key",
        envvar="ENDO_S3_SECRET_KEY",
        help="S3 secret access key",
    ),
    s3_bucket: str = typer.Option(
        None, "--s3-bucket", envvar="ENDO_S3_BUCKET", help="S3 bucket name"
    ),
    s3_region: str = typer.Option(
        "us-east-1", "--s3-region", envvar="ENDO_S3_REGION", help="S3 region"
    ),
    host: str = typer.Option(
        "0.0.0.0", "--host", envvar="ENDO_HOST", help="Host to bind to"
    ),
    port: int = typer.Option(
        8000, "--port", envvar="ENDO_PORT", help="Port to bind to"
    ),
    debug: bool = typer.Option(
        False, "--debug", envvar="ENDO_DEBUG", help="Enable debug mode"
    ),
    pretty_json_logs: bool = typer.Option(
        False,
        "--pretty-json-logs",
        envvar="ENDO_PRETTY_JSON_LOGS",
        help="Pretty-print JSON log output",
    ),
):
    """Start the endoscope collector API service."""

    api_key = os.environ.get("ENDO_API_KEY")
    if not api_key:
        err_console.print(
            "Required environment variable not set: ENDO_API_KEY\n"
            "API key must be provided via ENDO_API_KEY environment variable for security."
        )
        raise typer.Exit(code=1)

    missing = [
        name
        for name, val in [
            ("--project", project),
            ("--s3-access-key", s3_access_key),
            ("--s3-secret-key", s3_secret_key),
            ("--s3-bucket", s3_bucket),
        ]
        if not val
    ]

    if missing:
        err_console.print(f"Required options not set: {', '.join(missing)}")
        raise typer.Exit(code=1)

    cfg = EndoscopeConfig(
        api_key=api_key,
        project=project,
        s3_endpoint=s3_endpoint,
        s3_access_key=s3_access_key,
        s3_secret_key=s3_secret_key,
        s3_bucket=s3_bucket,
        s3_region=s3_region,
        host=host,
        port=port,
        debug=debug,
        pretty_json_logs=pretty_json_logs,
    )
    uvicorn.run(create_app(cfg), host=host, port=port)


# ------------------------------------------------------------------
# list
# ------------------------------------------------------------------


@app.command("list")
def list_cmd(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Output as JSON instead of Rich table"
    ),
) -> None:
    """List sessions for the configured project."""
    client = _require_client(ctx.obj["state"])
    sessions = client.list_sessions()

    if json_output:
        console.print_json(json.dumps(sessions))
        return

    if not sessions:
        console.print("No sessions found.")
        return

    table = Table(title="Sessions")
    table.add_column("Session ID", style="cyan", no_wrap=True)
    table.add_column("Timestamp", style="green")
    table.add_column("Events", justify="right")
    table.add_column("Files", justify="right")

    for s in sessions:
        table.add_row(
            str(s.get("session_id", "")),
            s.get("timestamp", ""),
            str(s.get("event_count", 0)),
            str(s.get("file_count", 0)),
        )

    console.print(table)


# ------------------------------------------------------------------
# show
# ------------------------------------------------------------------


@app.command("show")
def show_cmd(
    ctx: typer.Context,
    session_id: str = typer.Argument(help="Session UUID"),
    json_output: bool = typer.Option(
        False, "--json", help="Output as JSON instead of Rich panel"
    ),
) -> None:
    """Show detailed information about a session."""
    client = _require_client(ctx.obj["state"])
    session = client.get_session(session_id)

    if json_output:
        console.print_json(json.dumps(session))
        return

    lines = [
        f"[bold]Session ID:[/bold]  {session.get('session_id', '')}",
        f"[bold]Project:[/bold]     {session.get('project', '')}",
        f"[bold]Timestamp:[/bold]   {session.get('timestamp', '')}",
    ]

    events = session.get("events", [])
    lines.append(f"[bold]Events:[/bold]     {len(events)}")
    for i, evt in enumerate(events):
        lines.append(f"  [{i}] {json.dumps(evt)}")

    files = session.get("files", [])
    lines.append(f"[bold]Files:[/bold]      {len(files)}")
    for f in files:
        lines.append(f"  - {f}")

    console.print(Panel("\n".join(lines), title=f"Session {session_id}"))


# ------------------------------------------------------------------
# pull
# ------------------------------------------------------------------


@app.command("pull")
def pull_cmd(
    ctx: typer.Context,
    session_id: Optional[str] = typer.Argument(
        None, help="Session UUID (omit with --all or --last)"
    ),
    out_dir: Path = typer.Option(
        Path("./endoscope-out/"),
        "--out-dir",
        help="Output directory",
    ),
    all_sessions: bool = typer.Option(
        False, "--all", help="Pull all sessions"
    ),
    last: Optional[int] = typer.Option(
        None, "--last", help="Pull N most recent sessions"
    ),
) -> None:
    """Download session metadata and files locally."""
    client = _require_client(ctx.obj["state"])

    if all_sessions or last is not None:
        sessions = client.list_sessions()
        if last is not None:
            sessions = sessions[:last]
        if not sessions:
            console.print("No sessions to pull.")
            return
        for s in sessions:
            sid = str(s["session_id"])
            dest = client.pull_session(sid, out_dir)
            console.print(f"Pulled [cyan]{sid}[/cyan] -> {dest}")
        return

    if not session_id:
        err_console.print("Provide a session ID, or use --all / --last.")
        raise typer.Exit(code=1)

    dest = client.pull_session(session_id, out_dir)
    console.print(f"Pulled [cyan]{session_id}[/cyan] -> {dest}")


# ------------------------------------------------------------------
# delete
# ------------------------------------------------------------------


@app.command("delete")
def delete_cmd(
    ctx: typer.Context,
    session_id: str = typer.Argument(help="Session UUID"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation prompt"
    ),
) -> None:
    """Delete a session and all its objects."""
    client = _require_client(ctx.obj["state"])

    if not force:
        confirm = typer.confirm(
            f"Delete session {session_id}? This cannot be undone."
        )
        if not confirm:
            raise typer.Abort()

    client.delete_session(session_id)
    console.print(f"Deleted session [cyan]{session_id}[/cyan]")


# ------------------------------------------------------------------
# prune
# ------------------------------------------------------------------


@app.command("prune")
def prune_cmd(
    ctx: typer.Context,
    older_than: Optional[str] = typer.Option(
        None, "--older-than", help="Prune sessions older than (e.g. 7d, 24h)"
    ),
    all_sessions: bool = typer.Option(
        False, "--all", help="Prune all sessions"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation prompt"
    ),
) -> None:
    """Bulk-delete sessions by age or all at once."""
    client = _require_client(ctx.obj["state"])

    if not older_than and not all_sessions:
        err_console.print("Provide --older-than or --all.")
        raise typer.Exit(code=1)

    if not force:
        if all_sessions:
            msg = "Prune ALL sessions?"
        else:
            msg = f"Prune sessions older than {older_than}?"
        if not typer.confirm(msg):
            raise typer.Abort()

    result = client.prune_sessions(older_than=older_than, all=all_sessions)
    console.print(f"Pruned [bold]{result.get('pruned', 0)}[/bold] session(s).")


# ------------------------------------------------------------------
# api-key
# ------------------------------------------------------------------


@app.command("api-key")
def api_key_cmd() -> None:
    """Generate a random API key and print to stdout."""
    key = secrets.token_urlsafe(24)
    console.print(key)


def run():
    try:
        app()
    except EndoscopeAPIError as exc:
        err_console.print(f"[red]Error:[/red] {exc.detail}")
        raise SystemExit(1)
    except httpx.HTTPError as exc:
        err_console.print(f"[red]Connection error:[/red] {exc}")
        raise SystemExit(1)
