import os

import typer
import uvicorn

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
    project: str | None = typer.Option(
        None, "--project", "-p", envvar="ENDO_PROJECT", help="Project name"
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", "-k", envvar="ENDO_API_KEY", help="API key for authentication"
    ),
    s3_endpoint: str | None = typer.Option(
        None, "--s3-endpoint", envvar="ENDO_S3_ENDPOINT",
        help="S3-compatible endpoint URL (e.g. http://localhost:9000)",
    ),
    s3_access_key: str | None = typer.Option(
        None, "--s3-access-key", envvar="ENDO_S3_ACCESS_KEY", help="S3 access key ID"
    ),
    s3_secret_key: str | None = typer.Option(
        None, "--s3-secret-key", envvar="ENDO_S3_SECRET_KEY", help="S3 secret access key"
    ),
    s3_bucket: str | None = typer.Option(
        None, "--s3-bucket", envvar="ENDO_S3_BUCKET", help="S3 bucket name"
    ),
    s3_region: str | None = typer.Option(
        None, "--s3-region", envvar="ENDO_S3_REGION", help="S3 region"
    ),
    host: str = typer.Option(
        "0.0.0.0", "--host", envvar="ENDO_HOST", help="Host to bind to"
    ),
    port: int = typer.Option(
        8000, "--port", envvar="ENDO_PORT", help="Port to bind to"
    ),
    reload: bool = typer.Option(
        False, "--reload", help="Enable auto-reload for development"
    ),
):
    """Start the endoscope collector API service."""
    _set_env("ENDO_PROJECT", project)
    _set_env("ENDO_API_KEY", api_key)
    _set_env("ENDO_S3_ENDPOINT", s3_endpoint)
    _set_env("ENDO_S3_ACCESS_KEY", s3_access_key)
    _set_env("ENDO_S3_SECRET_KEY", s3_secret_key)
    _set_env("ENDO_S3_BUCKET", s3_bucket)
    _set_env("ENDO_S3_REGION", s3_region)

    uvicorn.run("endoscope.app:app", host=host, port=port, reload=reload)


def _set_env(key: str, value: str | None) -> None:
    if value is not None:
        os.environ[key] = value


def run():
    app()
