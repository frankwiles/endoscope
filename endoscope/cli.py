import typer
import uvicorn

from .app import create_app
from .config import EndoscopeConfig

app = typer.Typer(
    name="endoscope",
    help="Lightweight debug artifact capture CLI",
    no_args_is_help=True,
)


@app.callback()
def main():
    """Lightweight debug artifact capture CLI"""


@app.command("serve")
def serve_cmd(
    api_key: str = typer.Option(
        None,
        "--api-key",
        "-k",
        envvar="ENDO_API_KEY",
        help="API key for authentication",
    ),
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
    missing = [
        name
        for name, val in [
            ("--api-key", api_key),
            ("--project", project),
            ("--s3-access-key", s3_access_key),
            ("--s3-secret-key", s3_secret_key),
            ("--s3-bucket", s3_bucket),
        ]
        if not val
    ]

    if missing:
        print(f"Required options not set: {', '.join(missing)}")
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


def run():
    app()
